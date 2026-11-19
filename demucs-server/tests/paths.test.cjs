// Exercise the real page code with a small DOM/transport stand-in. No model/network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');

function element() {
  return {children: [], hidden: false, value: 'm4a', dataset: {},
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    addEventListener() {}, setAttribute() {}, removeAttribute() {}, pause() {}, load() {}};
}

async function page(href) {
  const nodes = new Map(), fetched = [], uploads = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
  const context = vm.createContext({
    location: new URL(href), URL, AbortController, console,
    setTimeout() { return 1; }, clearTimeout() {},
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
      return {ok: true, async json() {
        if (url.includes('web-config')) return {allowed_extensions: ['.wav'], max_file_size_mb: 50, retention_hours: 24};
        return {status: 'completed', filename: 'sample.wav', stems: {vocals: 'vocals.wav'}};
      }};
    },
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return {context, nodes, fetched, uploads, get, evaluate: code => vm.runInContext(code, context)};
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
    const [head, player] = p.get('tracks').children[0].children;
    assert.equal(player.src, expected + 'task/job/stem/vocals?format=m4a');
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
