import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { endpointFromHTML, endpointToReadURL, parseOptions, updateSnapshot, validateStatistics } from '../update-visitor-snapshot.mjs';

const now = new Date('2026-10-15T12:00:00.000Z');
const apiURL = 'https://example.invalid/api/visitors';
const previousURL = 'https://example.invalid/visitor-locations.json';
const logger = { warn() {}, info() {} };
const statistics = (n = 3) => ({
  places: [{ lat: 39.9, lon: 116.4, cc: 'CN', city: 'Beijing', n }],
  mappedPageviews: n,
  unmappedPageviews: 1,
  startedAt: '2026-10-15T01:00:00.000Z',
});
const snapshot = (n = 3, updatedAt = '2026-10-15T10:00:00.000Z') => ({ ...statistics(n), updatedAt });
const response = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });

test('the collector is configurable by environment or argument without allowing a write endpoint', () => {
  assert.deepEqual(parseOptions([], {}), {});
  assert.deepEqual(parseOptions([], { VISITOR_STATS_ENDPOINT: 'https://stats.example.invalid' }), {
    apiURL: 'https://stats.example.invalid/api/visitors',
  });
  assert.deepEqual(parseOptions(['--endpoint', 'https://new.example.invalid/api/visitors'], {
    VISITOR_STATS_ENDPOINT: 'https://old.example.invalid',
  }), { apiURL: 'https://new.example.invalid/api/visitors' });
  assert.equal(endpointToReadURL('https://example.invalid/stats/'), 'https://example.invalid/stats/api/visitors');
  for (const url of ['http://example.invalid', 'https://example.invalid/api/visit',
    'https://secret@example.invalid', 'https://example.invalid/?key=secret']) {
    assert.throws(() => endpointToReadURL(url), /Endpoint/);
  }
  assert.throws(() => parseOptions(['--unknown'], {}), /Usage/);
});

test('the default endpoint follows #visitor-map configuration regardless of attribute order', async t => {
  const html = `<div data-endpoint='https://new-collector.example.invalid' aria-label='Locations' id='visitor-map'></div>`;
  assert.equal(endpointFromHTML(html), 'https://new-collector.example.invalid/api/visitors');
  assert.equal(endpointFromHTML(`<!-- <div id="visitor-map" data-endpoint="https://old.example.invalid"></div> -->${html}`),
    'https://new-collector.example.invalid/api/visitors');
  assert.throws(() => endpointFromHTML('<div id="visitor-map"></div>'), /data-endpoint/);
  assert.throws(() => endpointFromHTML(html + html), /one #visitor-map/);
  const f = await fixture(t, snapshot());
  const indexPath = join(f.directory, 'index.html');
  await writeFile(indexPath, html);
  const requested = [];
  await updateSnapshot({ outputPath: f.outputPath, indexPath, previousURL, now, logger,
    fetchFn: async url => {
      requested.push(url);
      return response(url === previousURL ? snapshot() : statistics(4));
    },
  });
  assert.deepEqual(requested, [previousURL, 'https://new-collector.example.invalid/api/visitors']);
  assert.equal((await f.read()).mappedPageviews, 4);
});

async function fixture(t, initial) {
  const directory = await mkdtemp(join(tmpdir(), 'visitor-snapshot-test-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const outputPath = join(directory, 'visitor-locations.json');
  if (initial) await writeFile(outputPath, JSON.stringify(initial));
  const run = fetchFn => updateSnapshot({ outputPath, apiURL, previousURL, fetchFn, now, logger });
  return { directory, outputPath, run, read: async () => JSON.parse(await readFile(outputPath, 'utf8')) };
}

test('fresh API data replaces the snapshot using GET only and exposes only aggregate fields', async t => {
  const f = await fixture(t, snapshot());
  const calls = [];
  const fresh = { ...statistics(5), ip: 'not-public', places: [{ ...statistics(5).places[0], address: 'not-public' }] };
  const result = await f.run(async (url, options) => {
    calls.push({ url, options });
    return response(url === apiURL ? fresh : snapshot(4));
  });
  assert.deepEqual(calls.map(call => call.url), [previousURL, apiURL]);
  assert(calls.every(call => call.options.method === 'GET' && !('body' in call.options)));
  assert(calls.every(call => call.options.redirect === 'error'));
  assert.equal(result.refreshed, true);
  assert.deepEqual(await f.read(), snapshot(5, now.toISOString()));
  assert.deepEqual(await readdir(f.directory), ['visitor-locations.json']);
});

test('API outage recovers the last successful deployment instead of stale checked-in data', async t => {
  const f = await fixture(t, snapshot(2, '2026-10-15T08:00:00.000Z'));
  const latest = snapshot(8, '2026-10-15T11:00:00.000Z');
  const result = await f.run(async url => {
    if (url === apiURL) throw new Error('network unavailable');
    return response(latest);
  });
  assert.equal(result.refreshed, false);
  assert.deepEqual(await f.read(), latest);
});

test('both networks unavailable preserve the local snapshot and its original updatedAt', async t => {
  const original = snapshot();
  const f = await fixture(t, original);
  await f.run(async () => { throw new Error('offline'); });
  assert.deepEqual(await f.read(), original);
});

test('a stale CDN response cannot roll back newer local totals', async t => {
  const original = snapshot(10);
  const f = await fixture(t, original);
  await f.run(async url => {
    if (url === apiURL) return new Response('Unavailable', { status: 503 });
    return response(snapshot(2, '2026-10-15T11:00:00.000Z'));
  });
  assert.deepEqual(await f.read(), original);
});

test('API counter reset or invalid data cannot erase known visitor cities', async t => {
  for (const invalid of [statistics(1), { ...statistics(), places: [] }]) {
    const f = await fixture(t, snapshot(8));
    await f.run(async url => response(url === apiURL ? invalid : snapshot(5)));
    assert.equal((await f.read()).mappedPageviews, 8);
  }
});

test('missing or corrupt fallback plus unavailable API fails without writing fake data', async t => {
  const f = await fixture(t);
  await assert.rejects(f.run(async () => new Response('Unavailable', { status: 503 })), /No valid visitor snapshot/);
  assert.deepEqual(await readdir(f.directory), []);
  await writeFile(f.outputPath, '{broken');
  await assert.rejects(f.run(async () => response({})), /No valid visitor snapshot/);
  assert.equal(await readFile(f.outputPath, 'utf8'), '{broken');
});

test('the first valid empty database is allowed without inventing a start date or point', async t => {
  const f = await fixture(t);
  const empty = { places: [], mappedPageviews: 0, unmappedPageviews: 0, startedAt: null };
  await f.run(async url => url === apiURL ? response(empty) : new Response('', { status: 404 }));
  assert.deepEqual(await f.read(), { ...empty, updatedAt: now.toISOString() });
});

test('validation rejects bad coordinates, names, country codes and counts', () => {
  const patches = [
    { lat: '39.9' }, { lat: 91 }, { lon: -181 }, { lat: 0, lon: 0 },
    { city: '' }, { city: '  ' }, { city: 'A\nB' }, { cc: 'XX' }, { cc: 'China' },
    { n: 0 }, { n: -1 }, { n: 1.5 }, { n: Number.MAX_SAFE_INTEGER + 1 },
  ];
  for (const patch of patches) {
    const data = statistics();
    Object.assign(data.places[0], patch);
    assert.throws(() => validateStatistics(data, { now }), /Invalid|positive|integer|match/);
  }
  assert.throws(() => validateStatistics({ ...statistics(), unmappedPageviews: -1 }, { now }), /integer/);
  assert.throws(() => validateStatistics({ ...statistics(), places: [null] }, { now }), /Invalid/);
});

test('validation rejects invalid or future dates and impossible timestamp ordering', () => {
  for (const startedAt of [null, 'yesterday', '2026-02-30T01:00:00.000Z', '2027-01-01T00:00:00.000Z']) {
    assert.throws(() => validateStatistics({ ...statistics(), startedAt }, { now }), /timestamp/);
  }
  assert.throws(() => validateStatistics({ ...snapshot(), updatedAt: '2026-10-15T00:00:00.000Z' }, { snapshot: true, now }), /predates/);
  assert.throws(() => validateStatistics({ ...snapshot(), updatedAt: 'invalid' }, { snapshot: true, now }), /timestamp/);
});

test('oversized response or invalid JSON falls back to the genuine snapshot', async t => {
  for (const oversized of [true, false]) {
    const f = await fixture(t, snapshot());
    await f.run(async () => new Response(oversized ? ' '.repeat(2 * 1024 * 1024 + 1) : '<html>error</html>'));
    assert.deepEqual(await f.read(), snapshot());
  }
});
