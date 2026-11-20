// Exercise the real page code with a small DOM/transport stand-in. No model/network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');

function element() {
  return {children: [], hidden: false, value: 'm4a', dataset: {}, listeners: {},
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }, setAttribute() {}, removeAttribute(name) { if (name === 'value') delete this.value; }, pause() {}, load() {},
    click() { this.clicks=(this.clicks||0)+1; for (const callback of this.listeners.click||[]) callback({preventDefault() {}}); }};
}

async function page(href) {
  const nodes = new Map(), fetched = [], uploads = [];
  const intervals = new Map();
  let nextTimer = 1, now = Date.parse('2026-11-20T03:00:00Z');
  let taskResponse = {status: 'completed', filename: 'sample.wav', stems: {vocals: 'vocals.wav'}};
  let downloadResponse;
  const blobs = new Map(), revoked = [];
  class TestURL extends URL {
    static createObjectURL(blob) { const key='blob:test-'+blobs.size; blobs.set(key,blob); return key; }
    static revokeObjectURL(key) { revoked.push(key); blobs.delete(key); }
  }
  const get = id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
  const context = vm.createContext({
    location: new URL(href), URL:TestURL, Blob, AbortController, console,
    setTimeout() { return 1; }, clearTimeout() {},
    setInterval(callback) { const id = nextTimer++; intervals.set(id, callback); return id; },
    clearInterval(id) { intervals.delete(id); },
    Date: class extends Date { static now() { return now; } },
    crypto: {getRandomValues(array) { array.fill(1); return array; }},
    localStorage: {getItem() { return '[]'; }, setItem() {}},
    document: {getElementById: get, createElement: element, addEventListener() {}, querySelectorAll() { return []; }},
    window: {addEventListener() {}},
    FormData: class { append() {} },
    XMLHttpRequest: class {
      constructor() { this.upload = {}; this.status = 200; this.responseText = '{"task_id":"job"}'; }
      open(method, url) { uploads.push({method, url}); }
      setRequestHeader() {}
      send() { this.onload(); }
    },
    async fetch(url, options) {
      fetched.push({url, options});
      if (url.endsWith('/download')) {
        if (typeof downloadResponse === 'function') return downloadResponse(url,options);
        return downloadResponse;
      }
      return {ok: true, async json() {
        if (url.includes('web-config')) return {allowed_extensions: ['.wav'], max_file_size_mb: 50, retention_hours: 24};
        if (taskResponse instanceof Error) throw taskResponse;
        return taskResponse;
      }};
    },
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return {context, nodes, fetched, uploads, get, intervals, blobs, revoked,
    setTask(task) { taskResponse = task; },
    setDownload(response) { downloadResponse = response; },
    tick(milliseconds) { now += milliseconds; for (const callback of intervals.values()) callback(); },
    evaluate: code => vm.runInContext(code, context)};
}

for (const mount of ['/', '/audio/']) {
  test(`all audio transport and result URLs follow ${mount}`, async () => {
    const p = await page(`http://127.0.0.1:18782${mount}?from=test`);
    const expected = `http://127.0.0.1:18782${mount}api/v1/`;
    assert.equal(p.fetched.length, 2);
    assert.ok(p.fetched.every(request => request.url.startsWith(expected)));
    await p.evaluate('upload({name: "song.wav"}, "job")');
    p.evaluate('setMode("extraction")');
    await p.evaluate('upload({name: "movie.mp4"}, "job")');
    assert.equal(p.uploads[0].url, expected + 'separate/async');
    assert.equal(p.uploads[1].url, expected + 'extract/async?format=m4a');

    p.evaluate('showResults("job", {filename:"song.wav", stems:{vocals:"vocals.wav"}, aac_stems:{vocals:"vocals.m4a"}})');
    assert.equal(p.get('download-all').href, expected + 'task/job/download');
    const [head] = p.get('tracks').children[0].children;
    assert.equal(p.evaluate('mixer.tracks[0].url'), expected + 'task/job/stem/vocals?format=m4a');
    assert.equal(p.get('mixer').hidden, false);
    assert.deepEqual(head.children.slice(1).map(link => link.href), [
      expected + 'task/job/stem/vocals?format=m4a', expected + 'task/job/stem/vocals?format=wav']);

    p.evaluate('showExtraction("video", {filename:"movie.mp4", export_format:"wav"})');
    assert.equal(p.get('download-audio').href, expected + 'task/video/audio');
    assert.equal(p.get('extraction-player').src, expected + 'task/video/audio?preview=true');
    await p.evaluate('separateExtraction()');
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(p.fetched.some(request => request.url === expected + 'task/video/separate' && request.options.method === 'POST'));
    assert.ok(p.fetched.some(request => request.url.includes(expected + 'task/')));
    assert.ok(p.fetched.every(request => request.url.startsWith(expected)));
  });
}

test('HTML resource URLs stay within the audio mount', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  for (const match of html.matchAll(/(?:href|src)="([^\"]+)"/g)) {
    if (match[1].startsWith('data:')) continue;
    assert.ok(new URL(match[1], 'https://example.com/audio/').pathname.startsWith('/audio/'));
  }
});

function zipResponse({length=8, type='application/zip', broken=false, signature=true}={}) {
  const bytes=new Uint8Array(signature ? [80,75,3,4,1,2,3,4] : [123,34,1,2,3,4,5,6]);
  let index=0;
  return {ok:true, headers:{get(key) {return {'Content-Type':type,'Content-Length':String(length)}[key]||null;}},
    body:{getReader() {return {async read() {
      if (broken && index===1) throw new Error('Connection interrupted');
      if (index===2) return {done:true};
      const value=bytes.slice(index*4,++index*4); return {done:false,value};
    }};}}};
}

test('ZIP downloads show preparation, prevent duplicate requests and provide a real save link', async () => {
  const p=await page('http://127.0.0.1/audio/');
  p.evaluate('showResults("job", {filename:"音乐.mp3",stems:{vocals:"vocals.wav"}})');
  let release;
  p.setDownload(() => new Promise(resolve => {release=resolve;}));
  const event={preventDefault() {this.prevented=true;}};
  const pending=p.get('download-all').listeners.click[0](event);
  assert.equal(event.prevented,true);
  assert.match(p.get('download-all').textContent,/正在准备/);
  assert.equal(p.get('download-cancel').hidden,false);
  p.tick(2000);
  assert.match(p.get('download-message').textContent,/2 秒/);
  await p.get('download-all').listeners.click[0]({preventDefault() {}});
  assert.equal(p.fetched.filter(request=>request.url.endsWith('/download')).length,1);
  release(zipResponse()); await pending;
  assert.match(p.get('download-all').href,/^blob:/);
  assert.equal(p.get('download-all').download,'音乐-stems.zip');
  assert.match(p.get('download-all').textContent,/保存 ZIP/);
  assert.match(p.get('download-message').textContent,/完整接收/);
  assert.equal(p.get('download-all').clicks,1,'attempt browser saving once while retaining a manual link');
  assert.equal(p.get('download-cancel').hidden,true);
  assert.equal(p.intervals.size,0);
  const again={preventDefault() {this.prevented=true;}};
  await p.get('download-all').listeners.click[0](again);
  assert.equal(again.prevented,undefined,'a later user click must use normal browser download handling');
  p.evaluate('setMode("extraction")');
  assert.equal(p.blobs.size,0,'leaving the task releases the completed download Blob');
  assert.equal(p.revoked.length,1);
});

test('invalid, incomplete and interrupted ZIP responses show a retry instead of saving', async () => {
  const p=await page('http://127.0.0.1/audio/');
  p.evaluate('showResults("job", {filename:"song.mp3",stems:{vocals:"vocals.wav"}})');
  for (const response of [zipResponse({length:99}),zipResponse({type:'text/html'}),zipResponse({signature:false}),zipResponse({broken:true}),
    {ok:false,status:410,json:async()=>({detail:'结果已过期'})}]) {
    p.setDownload(response);
    await p.get('download-all').listeners.click[0]({preventDefault() {}});
    assert.match(p.get('download-all').textContent,/重试/);
    assert.equal(p.blobs.size,0);
    assert.equal(p.get('download-progress').hidden,true);
    assert.equal(p.evaluate('downloadJob.busy'),false);
  }
  assert.equal(p.get('download-message').textContent,'结果已过期');
  p.setDownload(zipResponse());
  await p.get('download-all').listeners.click[0]({preventDefault() {}});
  assert.equal(p.blobs.size,1,'a valid retry can still prepare a file');
  p.evaluate('resetDownload()');
});

test('ZIP downloads can be cancelled and cannot update another task', async () => {
  const p=await page('http://127.0.0.1/audio/');
  p.evaluate('showResults("job", {filename:"song.mp3",stems:{vocals:"vocals.wav"}})');
  p.setDownload((url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('Aborted')))));
  let pending=p.get('download-all').listeners.click[0]({preventDefault() {}});
  p.get('download-cancel').listeners.click[0](); await pending;
  assert.match(p.get('download-message').textContent,/已取消/);
  assert.equal(p.evaluate('downloadJob.busy'),false);
  pending=p.get('download-all').listeners.click[0]({preventDefault() {}});
  p.evaluate('showResults("next", {filename:"other.mp3",stems:{vocals:"vocals.wav"}})');
  await pending;
  assert.equal(p.get('download-all').href,'http://127.0.0.1/audio/api/v1/task/next/download');
  assert.equal(p.get('download-status').hidden,true);
});

test('track controls change the current mix, reset volumes, and release it on task changes', async () => {
  const p=await page('http://127.0.0.1/audio/');
  p.evaluate('showResults("job", {filename:"song.wav",stems:{vocals:"vocals.wav",bass:"bass.wav"}})');
  p.evaluate('mixerControls.get("vocals").volume.value="25"; mixerControls.get("vocals").volume.listeners.input[0]()');
  assert.equal(p.evaluate('mixer.tracks[0].volume'),.25);
  assert.equal(p.evaluate('mixerControls.get("vocals").value.textContent'),'25%');
  p.evaluate('mixerControls.get("bass").mute.listeners.click[0]()');
  assert.equal(p.evaluate('mixer.tracks[1].muted'),true);
  p.get('mix-reset').listeners.click[0]();
  assert.equal(p.evaluate('mixer.tracks[0].volume'),.88);
  assert.equal(p.evaluate('mixer.tracks[1].muted'),false);
  p.evaluate('mixer.position=()=>0; mixer.play=async function(){this.playing=true;this.notify()}; mixer.pause=function(){this.playing=false;this.notify()}');
  assert.equal(p.evaluate('mixerControls.get("vocals").solo.title'),'单独播放人声');
  assert.equal(p.evaluate('mixerControls.get("vocals").solo.dataset.icon'),'play');
  await p.evaluate('mixerControls.get("vocals").solo.listeners.click[0]()');
  assert.equal(p.evaluate('mixerControls.get("vocals").solo.dataset.icon'),'pause');
  assert.equal(p.evaluate('mixerControls.get("vocals").solo.title'),'暂停人声');
  await p.evaluate('mixerControls.get("vocals").solo.listeners.click[0]()');
  assert.equal(p.evaluate('mixer.playing'),false);
  assert.equal(p.evaluate('mixerControls.get("vocals").solo.dataset.icon'),'play');
  p.evaluate('const previousMixer=mixer; setMode("extraction")');
  assert.equal(p.evaluate('previousMixer.disposed'),true);
  assert.equal(p.evaluate('mixer'),null);
  assert.equal(p.get('mixer').hidden,true);
});

test('polling displays server stages and percentages, with a live elapsed clock', async () => {
  const p = await page('http://127.0.0.1/audio/');
  p.setTask({status:'processing', stage:'inference', progress:48, kind:'separation', created_at:'2026-11-20T02:58:55Z'});
  await p.evaluate('poll("job", generation)');
  assert.equal(p.get('status-title').textContent, '正在分离音轨');
  assert.equal(p.get('task-progress-box').hidden, false);
  assert.equal(p.get('task-progress').value, 48);
  assert.equal(p.get('task-percent').textContent, '48%');
  assert.equal(p.get('task-elapsed').textContent, '提交已过 1 分 5 秒');
  p.tick(2000);
  assert.equal(p.get('task-elapsed').textContent, '提交已过 1 分 7 秒');
  assert.equal(p.get('task-progress').value, 48, 'time alone must not advance model progress');

  p.setTask({status:'processing', stage:'encoding', progress:96, started_at:'2026-11-20T02:59:52Z'});
  await p.evaluate('poll("job", generation)');
  assert.equal(p.get('status-title').textContent, '正在生成试听音频');
  assert.equal(p.get('task-percent').textContent, '96%');
  assert.equal(p.get('task-elapsed').textContent, '已处理 10 秒');
  assert.equal(p.intervals.size, 1, 'polling replaces the previous clock');
});

test('old workers, queueing, and invalid values use indeterminate progress', async () => {
  const p = await page('http://127.0.0.1/audio/');
  for (const progress of [undefined, null, '48', NaN, Infinity]) {
    p.setTask({status:'processing', kind:'extraction', progress});
    await p.evaluate('poll("job", generation)');
    assert.equal(p.get('status-title').textContent, '正在提取音频');
    assert.equal(p.get('task-progress').value, undefined);
    assert.equal(p.get('task-percent').textContent, '进度待更新');
    assert.equal(p.get('task-elapsed').hidden, true);
    assert.match(p.get('status-detail').textContent, /暂未提供处理百分比/);
  }
  p.setTask({status:'queued', progress:0, created_at:'2026-11-20T02:59:55Z'});
  await p.evaluate('poll("job", generation)');
  assert.equal(p.get('task-percent').textContent, '等待开始');
  assert.equal(p.get('task-progress').value, undefined);
  assert.equal(p.get('task-elapsed').textContent, '提交已过 5 秒');
  for (const [progress, expected] of [[0,0],[-1,0],[150,99],[100,99]]) {
    p.setTask({status:'processing', progress});
    await p.evaluate('poll("job", generation)');
    assert.equal(p.get('task-progress').value, expected);
  }
});

test('completion, failures, network errors and mode changes clear stale progress', async () => {
  const p = await page('http://127.0.0.1/audio/');
  const running = {status:'processing', stage:'inference', progress:30, created_at:'2026-11-20T02:59:00Z'};
  for (const task of [
    {status:'completed', filename:'song.wav', stems:{vocals:'vocals.wav'}},
    {status:'failed', error:'Model failed'},
    new Error('Connection lost'),
  ]) {
    p.setTask(running); await p.evaluate('poll("job", generation)');
    assert.equal(p.intervals.size, 1);
    p.setTask(task); await p.evaluate('poll("job", generation)');
    assert.equal(p.get('task-progress-box').hidden, true);
    assert.equal(p.intervals.size, 0);
  }
  p.setTask(running); await p.evaluate('poll("job", generation)');
  p.evaluate('setMode("extraction")');
  assert.equal(p.get('task-progress-box').hidden, true);
  assert.equal(p.intervals.size, 0);
});
