const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const engine = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8').split('const $ =')[0];

function fixture(options = {}) {
  const param = () => ({value:1, cancelScheduledValues() {},
    setValueAtTime(value) { this.value = value; }, setTargetAtTime(value) { this.value = value; }});
  const nodes = [];
  const context = {currentTime:100, destination:{}, closed:false, resumes:0,
    resume() { this.resumes++; return Promise.resolve(); }, close() { this.closed=true; return Promise.resolve(); },
    createGain() { return {gain:param(), connect() {}, disconnect() {}}; },
    createDynamicsCompressor() { return {threshold:param(),knee:param(),ratio:param(),attack:param(),release:param(),connect() {}}; },
    decodeAudioData: options.decode || (async () => ({duration:10})),
    createBufferSource() {
      const node = {connect() {}, disconnect() {}, stop() {this.stopped=true;}, start(when, offset) {this.when=when; this.offset=offset;}};
      nodes.push(node); return node;
    },
  };
  const requests = [], changes = [];
  const sandbox = vm.createContext({window:{AudioContext:function() {return context;}}, AbortController,
    setTimeout, clearTimeout,
    async fetch(url, options_) {
      requests.push(url);
      if (options.fetch) return options.fetch(url, options_);
      return {ok:true, arrayBuffer:async () => new ArrayBuffer(8)};
    },
  });
  vm.runInContext(engine + '; this.Mixer = StemMixer;', sandbox);
  const mixer = new sandbox.Mixer(['vocals','drums','bass','other'].map(name => ({name,url:'https://private.test/'+name})),
    value => changes.push({loading:value.loading, playing:value.playing}));
  return {mixer,context,nodes,requests,changes};
}

test('all stems wait for decoding, then start on exactly one audio clock', async () => {
  const resolvers=[];
  const f=fixture({decode:() => new Promise(resolve => resolvers.push(resolve))});
  const playing=f.mixer.play();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(resolvers.length,4);
  assert.equal(f.nodes.length,0,'a partly loaded mix must never play');
  assert.equal(f.mixer.loading,true);
  resolvers.forEach(resolve => resolve({duration:10}));
  await playing;
  assert.equal(f.requests.length,4);
  assert.equal(f.nodes.length,4);
  assert.ok(f.nodes.every(node => node.when===100.05 && node.offset===0));
  assert.equal(f.mixer.playing,true);
  assert.equal(f.context.resumes,1);
});

test('volume, mute, and solo update gains immediately without restarting the mix', async () => {
  const f=fixture(); await f.mixer.play();
  const [vocals,drums,bass,other]=f.mixer.tracks;
  f.mixer.setVolume('vocals',.2);
  f.mixer.setVolume('bass',.6);
  assert.equal(vocals.gain.gain.value,.2);
  assert.equal(drums.gain.gain.value,.88);
  f.mixer.toggleMute('bass'); assert.equal(bass.gain.gain.value,0);
  f.mixer.toggleMute('bass'); assert.equal(bass.gain.gain.value,.6);
  f.mixer.setSolo('vocals');
  assert.equal(vocals.gain.gain.value,.2);
  assert.ok([drums,bass,other].every(track => track.gain.gain.value===0));
  f.mixer.setVolume('bass',.3);
  assert.equal(bass.gain.gain.value,0,'editing a non-solo track must not make it audible');
  f.mixer.setSolo(null);
  assert.equal(bass.gain.gain.value,.3);
  assert.equal(vocals.gain.gain.value,.2);
  assert.equal(f.nodes.length,4,'gain changes must not reschedule playback');
});

test('pause/resume and seek preserve the shared position and volume settings', async () => {
  const f=fixture(); await f.mixer.play();
  f.context.currentTime=102.05;
  f.mixer.setVolume('vocals',.4);
  f.mixer.pause();
  assert.ok(Math.abs(f.mixer.position()-2)<1e-8);
  assert.ok(f.nodes.every(node => node.stopped));
  f.context.currentTime=105;
  await f.mixer.play();
  assert.ok(f.nodes.slice(-4).every(node => node.when===105.05 && Math.abs(node.offset-2)<1e-8));
  assert.equal(f.requests.length,4,'resume reuses the already decoded stems');
  f.mixer.seek(6);
  assert.ok(f.nodes.slice(-4).every(node => node.offset===6 && node.when===105.05));
  assert.equal(f.mixer.tracks[0].gain.gain.value,.4);
  f.mixer.seek(100);
  assert.equal(f.mixer.playing,false);
  assert.equal(f.mixer.position(),10);
  await f.mixer.play();
  assert.ok(f.nodes.slice(-4).every(node => node.offset===0));
});

test('a failed track stops the whole load and supports a clean retry', async () => {
  let fail=true;
  const f=fixture({fetch:async url => ({ok:!(fail&&url.endsWith('bass')),status:410,arrayBuffer:async () => new ArrayBuffer(8)})});
  await assert.rejects(f.mixer.play(),/已过期/);
  assert.equal(f.mixer.playing,false);
  assert.equal(f.mixer.loading,false);
  assert.equal(f.nodes.length,0);
  assert.ok(f.mixer.tracks.every(track => !track.buffer));
  fail=false;
  await f.mixer.play();
  assert.equal(f.nodes.length,4);
});

test('task changes dispose loading audio and cannot start the old task later', async () => {
  const resolvers=[];
  const f=fixture({decode:() => new Promise(resolve => resolvers.push(resolve))});
  const playing=f.mixer.play();
  await new Promise(resolve => setImmediate(resolve));
  f.mixer.dispose();
  resolvers.forEach(resolve => resolve({duration:10}));
  await playing;
  assert.equal(f.context.closed,true);
  assert.equal(f.nodes.length,0);
  assert.ok(f.mixer.tracks.every(track => !track.buffer));
});

test('overlapping play clicks schedule a single mix and reset restores the defaults', async () => {
  const f=fixture();
  await Promise.all([f.mixer.play(),f.mixer.play()]);
  assert.equal(f.nodes.length,4);
  f.mixer.setVolume('vocals',-3); assert.equal(f.mixer.tracks[0].volume,0);
  f.mixer.setVolume('vocals',9); assert.equal(f.mixer.tracks[0].volume,1);
  f.mixer.toggleMute('drums'); f.mixer.setSolo('bass'); f.mixer.reset();
  assert.equal(f.mixer.solo,null);
  assert.ok(f.mixer.tracks.every(track => track.volume===.88&&!track.muted&&track.gain.gain.value===.88));
  f.context.currentTime=f.nodes[0].when+10;
  f.nodes[0].onended();
  assert.equal(f.mixer.playing,false);
  assert.equal(f.mixer.position(),10);
});
