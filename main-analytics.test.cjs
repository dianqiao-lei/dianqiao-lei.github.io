const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8');
const start = source.indexOf('/* Basic 51.LA page analytics.');
const end = source.indexOf('/* Reveal on scroll:', start);
assert(start !== -1 && end > start, '51.LA loader exists in main.js');
const loader = source.slice(start, end);

function harness(url) {
  const scripts = [];
  const calls = [];
  const context = vm.createContext({
    location: new URL(url),
    window: { LA: { init: options => calls.push(JSON.parse(JSON.stringify(options))) } },
    document: { createElement: type => ({ type }), head: { appendChild: script => scripts.push(script) } }
  });
  return { scripts, calls, context, run() { vm.runInContext(loader, context); } };
}

test('production HTTPS loads asynchronously and initializes only once with optional tracking disabled', () => {
  const h = harness('https://dianqiao-lei.github.io/#service');
  h.run();
  h.run();
  assert.equal(h.scripts.length, 1);
  assert.equal(h.calls.length, 0, 'initialization waits for SDK load');
  assert.equal(h.scripts[0].src, 'https://sdk.51.la/js-sdk-pro.min.js');
  assert.equal(h.scripts[0].async, true);
  h.scripts[0].onload();
  h.scripts[0].onload();
  assert.deepEqual(h.calls, [{ id: '3RWElwTgQjmTbrIQ', ck: '3RWElwTgQjmTbrIQ',
    hashMode: false, autoTrack: false, screenRecord: false }]);
  assert.doesNotThrow(() => h.scripts[0].onerror());
  const broken = harness('https://dianqiao-lei.github.io/');
  broken.context.window.LA.init = () => { throw new Error('SDK unavailable'); };
  broken.run();
  assert.doesNotThrow(() => broken.scripts[0].onload());
});

test('local previews, HTTP, and lookalike hosts never load or initialize analytics', () => {
  for (const url of ['http://127.0.0.1:8000/', 'http://localhost:8000/',
    'http://dianqiao-lei.github.io/', 'https://dianqiao-lei.github.io.example.com/',
    'https://preview.example.com/', 'file:///tmp/index.html']) {
    const h = harness(url);
    h.run();
    assert.equal(h.scripts.length, 0, url);
    assert.equal(h.calls.length, 0, url);
    assert.equal(h.context.window.__homepage51LaStarted, undefined, url);
  }
});
