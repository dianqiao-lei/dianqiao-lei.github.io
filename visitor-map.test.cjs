const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, 'visitor-map.js'), 'utf8');
const NOW = '2026-10-15T12:00:00.000Z';
const CACHE = 'visitor-map-public-v1';
const DAILY_CACHE = 'visitor-map-daily-v1';
const ENDPOINT = 'https://collector.example.invalid';
const live = (n = 3) => ({
  places: [{ city: 'Beijing', cc: 'CN', lat: 39.9, lon: 116.4, n }],
  mappedPageviews: n, unmappedPageviews: 1, startedAt: '2026-10-15T01:00:00.000Z'
});
const saved = (n = 3, updatedAt = '2026-10-15T10:00:00.000Z') => ({ ...live(n), updatedAt });
const flush = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };

function harness({ cache, dailyCache, daily = false, endpoint = ENDPOINT, production = true, fetchAvailable = true, snapshot = 'visitor-locations.json', storageDenied = false, abortAvailable = true } = {}) {
  const nodes = [];
  function node() {
    return { attrs: {}, style: {}, events: {}, hidden: false, textContent: '',
      setAttribute(name, value) { this.attrs[name] = value; },
      addEventListener(name, listener) { this.events[name] = listener; },
      getBoundingClientRect() { return { left: 0, right: 900, width: 900 }; }
    };
  }
  const svg = node();
  svg.children = [];
  svg.replaceChildren = () => { svg.children = []; };
  svg.appendChild = child => { svg.children.push(child); };
  const tip = node();
  const status = node();
  const map = node();
  map.attrs = { 'data-endpoint': endpoint, 'data-snapshot': snapshot, 'data-refresh': daily ? 'daily' : undefined };
  map.getAttribute = name => map.attrs[name];
  map.querySelector = name => name === 'svg' ? svg : name === '.visitor-map__tip' ? tip : null;
  const location = new URL(production ? 'https://dianqiao-lei.github.io/#service' : 'http://127.0.0.1:8000/#service');
  const storage = new Map(cache ? [[CACHE, typeof cache === 'string' ? cache : JSON.stringify(cache)]] : []);
  if (dailyCache) storage.set(DAILY_CACHE, typeof dailyCache === 'string' ? dailyCache : JSON.stringify(dailyCache));
  const localStorage = {
    getItem(key) { if (storageDenied) throw new Error('Storage denied'); return storage.get(key) || null; },
    setItem(key, value) { if (storageDenied) throw new Error('Storage denied'); storage.set(key, value); }
  };
  const calls = [];
  const fetch = (url, options) => new Promise((resolve, reject) => {
    calls.push({ url, options, resolve: data => resolve({ ok: true, json: async () => data }),
      reject, respond: resolve });
  });
  let timerId = 0;
  const timers = new Map();
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [NOW])); }
    static now() { return Date.parse(NOW); }
  }
  const window = { Intl, localStorage, ...(fetchAvailable ? { fetch } : {}), ...(abortAvailable ? { AbortController } : {}) };
  vm.runInNewContext(source, { window, document: {
    getElementById: id => id === 'visitor-map' ? map : id === 'visitor-map-status' ? status : null,
    createElementNS() { const item = node(); nodes.push(item); return item; }
  }, location, URL, Date: Clock, Intl, fetch, AbortController,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); }
  });
  return { calls, svg, tip, map, status, storage, timers,
    call(method, suffix) { return calls.find(c => c.options.method === method && c.url.endsWith(suffix)); },
    timeoutAll() { for (const [id, fn] of [...timers]) { timers.delete(id); fn(); } }
  };
}

async function rejectCollector(h) {
  h.call('POST', '/api/visit').reject(new Error('Blocked'));
  await flush();
  h.call('GET', '/api/visitors').reject(new Error('Blocked'));
  await flush();
}

function markerCount(h, n) {
  assert.equal(h.svg.children.length, 1);
  assert.match(h.svg.children[0].attrs['aria-label'], new RegExp(` · ${n} page view`));
}

test('same-origin saved cities render before the blocked collector finishes and survive both failures', async () => {
  const h = harness();
  await flush();
  assert.equal(h.calls.length, 2);
  const snapshot = h.call('GET', '/visitor-locations.json');
  assert.equal(snapshot.url, 'https://dianqiao-lei.github.io/visitor-locations.json');
  assert.equal(snapshot.options.mode, 'same-origin');
  snapshot.resolve(saved());
  await flush();
  markerCount(h, 3);
  assert.match(h.status.textContent, /saved Oct 15, 2026, 10:00 UTC/);
  await rejectCollector(h);
  markerCount(h, 3);
  assert.match(h.status.textContent, /saved /);
  assert.equal(h.calls.filter(c => c.options.method === 'POST').length, 1);
});

test('late saved response cannot replace successful live data or its cache', async () => {
  const h = harness({ cache: saved(2) });
  markerCount(h, 2);
  await flush();
  h.call('POST', '/api/visit').resolve(live(5));
  await flush();
  markerCount(h, 5);
  assert.match(h.status.textContent, /since Oct 15, 2026/);
  h.call('GET', '/visitor-locations.json').resolve(saved(3));
  await flush();
  markerCount(h, 5);
  assert.equal(JSON.parse(h.storage.get(CACHE)).mappedPageviews, 5);
  assert.equal(h.calls.length, 2, 'successful POST does not trigger a read fallback');
});

test('new snapshot upgrades cached cities, while an older snapshot never rolls cache back', async () => {
  for (const [initial, incoming, expected] of [[saved(3), saved(5, '2026-10-15T11:00:00.000Z'), 5],
    [saved(5, '2026-10-15T11:00:00.000Z'), saved(3), 5]]) {
    const h = harness({ cache: initial, production: false });
    await flush();
    h.call('GET', '/visitor-locations.json').resolve(incoming);
    await flush();
    markerCount(h, expected);
    assert.equal(JSON.parse(h.storage.get(CACHE)).mappedPageviews, expected);
  }
});

test('a valid empty or regressed live database cannot erase saved city points', async () => {
  for (const response of [{ places: [], mappedPageviews: 0, unmappedPageviews: 0, startedAt: null }, live(1)]) {
    const h = harness({ cache: saved(5) });
    await flush();
    h.call('POST', '/api/visit').resolve(response);
    h.call('GET', '/visitor-locations.json').reject(new Error('Unavailable'));
    await flush();
    markerCount(h, 5);
    assert.match(h.status.textContent, /saved /);
  }
});

test('local preview reads saved data but never posts or queries the collector', async () => {
  const h = harness({ production: false });
  await flush();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].options.method, 'GET');
  assert.equal(h.calls[0].url, 'http://127.0.0.1:8000/visitor-locations.json');
  h.calls[0].resolve(saved(1));
  await flush();
  assert.equal(h.map.attrs['aria-label'], 'Approximate visitor locations: 1 city');
  assert.match(h.svg.children[0].attrs['aria-label'], /1 page view$/);
});

test('snapshot and collector failure leave the cached timestamp and points visible', async () => {
  const original = saved(7);
  const h = harness({ cache: original });
  markerCount(h, 7);
  await flush();
  h.call('GET', '/visitor-locations.json').reject(new Error('Unavailable'));
  await rejectCollector(h);
  markerCount(h, 7);
  assert.deepEqual(JSON.parse(h.storage.get(CACHE)), original);
});

test('timed-out requests are bounded without AbortController and never retry a write', async () => {
  const h = harness({ cache: saved(), abortAvailable: false });
  await flush();
  h.timeoutAll();
  await flush();
  assert.equal(h.calls.filter(c => c.options.method === 'POST').length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/api/visitors')).length, 1);
  h.timeoutAll();
  await flush();
  markerCount(h, 3);
  assert.match(h.status.textContent, /saved /);
  assert.equal(h.timers.size, 0);
  // A late response from the expired write must not repaint or renew the cache.
  h.call('POST', '/api/visit').resolve(live(9));
  await flush();
  markerCount(h, 3);
});

test('read-only fallback can restore live data after a failed POST', async () => {
  const h = harness();
  await flush();
  h.call('GET', '/visitor-locations.json').resolve(saved(2));
  h.call('POST', '/api/visit').reject(new Error('Network error'));
  await flush();
  h.call('GET', '/api/visitors').resolve(live(4));
  await flush();
  markerCount(h, 4);
  assert.match(h.status.textContent, /since /);
  assert.equal(h.calls.filter(c => c.options.method === 'POST').length, 1);
});

test('validation rejects malformed cities, totals, and metadata as a whole', async () => {
  const patches = [
    d => { d.places[0] = null; }, d => { d.places[0].lat = '39.9'; },
    d => { d.places[0].lon = 181; }, d => { d.places[0].lat = 0; d.places[0].lon = 0; },
    d => { d.places[0].city = ['Beijing']; }, d => { d.places[0].city = ' '; },
    d => { d.places[0].city = 'Beijing\n'; }, d => { d.places[0].cc = 'XX'; },
    d => { d.places[0].cc = 'China'; }, d => { d.places[0].n = -1; },
    d => { d.places[0].n = 1.5; }, d => { d.places[0].n = Number.MAX_SAFE_INTEGER + 1; },
    d => { d.mappedPageviews = 5; }, d => { d.unmappedPageviews = -1; },
    d => { d.unmappedPageviews = '1'; }, d => { d.unmappedPageviews = Number.MAX_SAFE_INTEGER; },
    d => { d.startedAt = null; }, d => { d.startedAt = '2026-02-30T01:00:00.000Z'; },
    d => { d.startedAt = '2027-01-01T01:00:00.000Z'; }, d => { d.updatedAt = 'yesterday'; },
    d => { delete d.updatedAt; }, d => { d.updatedAt = '2026-10-15T00:00:00.000Z'; },
    d => { d.updatedAt = '2027-01-01T00:00:00.000Z'; }
  ];
  for (const patch of patches) {
    const data = saved();
    patch(data);
    const h = harness({ production: false });
    await flush();
    h.calls[0].resolve(data);
    await flush();
    assert.equal(h.svg.children.length, 0, patch.toString());
    assert.equal(h.storage.has(CACHE), false, patch.toString());
    assert.match(h.status.textContent, /No saved visitor locations/);
  }
});

test('only public aggregate fields are stored, and missing live updatedAt uses reception time', async () => {
  const data = live();
  data.ip = 'not-for-storage';
  data.places[0].ip = 'not-for-storage';
  const h = harness();
  await flush();
  h.call('POST', '/api/visit').resolve(data);
  h.call('GET', '/visitor-locations.json').reject(new Error('Unavailable'));
  await flush();
  const cached = JSON.parse(h.storage.get(CACHE));
  assert.equal(cached.updatedAt, NOW);
  assert.equal(JSON.stringify(cached).includes('not-for-storage'), false);
  assert.deepEqual(Object.keys(cached).sort(), ['mappedPageviews', 'places', 'startedAt', 'unmappedPageviews', 'updatedAt']);
});

test('disabled storage does not prevent network data or accessible tooltips', async () => {
  const h = harness({ storageDenied: true, production: false });
  await flush();
  h.calls[0].resolve(saved(1));
  await flush();
  const dot = h.svg.children[0];
  assert.equal(dot.attrs.tabindex, '0');
  assert.equal(dot.attrs.role, 'button');
  let prevented = false;
  dot.events.keydown({ key: 'Enter', preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(h.tip.hidden, false);
  assert.equal(h.tip.textContent, dot.attrs['aria-label']);
  dot.events.keydown({ key: 'Escape' });
  assert.equal(h.tip.hidden, true);
});

test('no-fetch browsers display saved cache or an honest unavailable message', () => {
  const cached = harness({ cache: saved(), fetchAvailable: false });
  markerCount(cached, 3);
  assert.match(cached.status.textContent, /saved /);
  const empty = harness({ fetchAvailable: false });
  assert.match(empty.status.textContent, /unavailable in this browser/);
});

test('cross-origin snapshot configuration is rejected without making a request', async () => {
  const h = harness({ production: false, snapshot: 'https://elsewhere.example.invalid/visitor-locations.json' });
  await flush();
  assert.equal(h.calls.length, 0);
  assert.equal(h.svg.children.length, 0);
});

test('an empty genuine database shows an empty state without inventing a point or start date', async () => {
  const h = harness({ production: false });
  await flush();
  h.calls[0].resolve({ places: [], mappedPageviews: 0, unmappedPageviews: 0, startedAt: null, updatedAt: NOW });
  await flush();
  assert.equal(h.svg.children.length, 0);
  assert.match(h.status.textContent, /No city-level visits recorded yet · saved/);
  assert.equal(h.map.attrs['aria-label'], 'Approximate visitor locations: 0 cities');
});


test('production daily mode reads only the homepage snapshot and ignores legacy collector cache', async () => {
  // Even a leftover endpoint attribute must not revive Cloudflare requests.
  const h = harness({ daily: true, cache: saved(99) });
  assert.equal(h.svg.children.length, 0, 'legacy source cache is isolated');
  await flush();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, 'https://dianqiao-lei.github.io/visitor-locations.json');
  assert.equal(h.calls[0].options.method, 'GET');
  assert.equal(h.calls[0].options.mode, 'same-origin');
  h.calls[0].resolve(saved(13));
  await flush();
  markerCount(h, 13);
  assert.match(h.status.textContent, /updated daily · saved Oct 15, 2026, 10:00 UTC/);
  assert.equal(JSON.parse(h.storage.get(DAILY_CACHE)).mappedPageviews, 13);
  assert.equal(JSON.parse(h.storage.get(CACHE)).mappedPageviews, 99, 'legacy cache remains untouched');
  assert.equal(h.calls.length, 1, 'no POST or collector read is attempted');
});

test('daily mode without a collector retains its own saved cache and timestamp when snapshot loading fails', async () => {
  const original = saved(15, '2026-10-15T11:00:00.000Z');
  const h = harness({ daily: true, endpoint: null, cache: saved(99), dailyCache: original });
  markerCount(h, 15);
  assert.match(h.status.textContent, /updated daily · saved Oct 15, 2026, 11:00 UTC/);
  await flush();
  assert.equal(h.calls.length, 1);
  h.calls[0].reject(new Error('Snapshot temporarily unavailable'));
  await flush();
  markerCount(h, 15);
  assert.match(h.status.textContent, /updated daily · saved Oct 15, 2026, 11:00 UTC/);
  assert.deepEqual(JSON.parse(h.storage.get(DAILY_CACHE)), original);
  assert.equal(h.calls.length, 1);
});
