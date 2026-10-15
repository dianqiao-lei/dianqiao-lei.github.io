import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';
import worker from '../worker.mjs';

const schema = readFileSync(new URL('../schema.sql', import.meta.url), 'utf8');
const migration = readFileSync(new URL('../migrations/0001_initial.sql', import.meta.url), 'utf8');
const ORIGIN = 'https://dianqiao-lei.github.io';
const TEST_CITY = {
  country: 'CN', region: 'Beijing', city: 'Beijing',
  latitude: '39.9042', longitude: '116.4074',
};

// Runs actual schema/upsert SQL in memory, modeling D1's documented atomic batch.
// It has no network client and no file-backed database.
class MemoryD1 {
  constructor(initialSchema = schema) {
    this.sqlite = new DatabaseSync(':memory:');
    if (initialSchema) this.sqlite.exec(initialSchema);
    this.batchCalls = 0;
    this.failOn = null;
  }
  prepare(sql) {
    return {
      sql, values: [],
      bind(...values) { return { sql, values }; },
    };
  }
  async batch(statements) {
    this.batchCalls += 1;
    this.sqlite.exec('BEGIN');
    try {
      const results = statements.map(({ sql, values }) => {
        if (this.failOn?.test(sql)) throw new Error('Injected database error');
        const prepared = this.sqlite.prepare(sql);
        const bindings = Object.fromEntries(values.map((value, i) => [`?${i + 1}`, value]));
        const rows = values.length ? prepared.all(bindings) : prepared.all();
        return { success: true, results: rows.map(row => ({ ...row })) };
      });
      this.sqlite.exec('COMMIT');
      return results;
    } catch (error) {
      this.sqlite.exec('ROLLBACK');
      throw error;
    }
  }
}

function database(t, initialSchema) {
  const db = new MemoryD1(initialSchema);
  t.after(() => db.sqlite.close());
  return db;
}

function request(path = '/api/visit', options = {}) {
  const { method = 'POST', origin = ORIGIN, cf = TEST_CITY, body, headers = {} } = options;
  const allHeaders = { ...headers };
  if (origin !== null) allHeaders.Origin = origin;
  const req = new Request(`https://test.invalid${path}`, { method, headers: allHeaders, body });
  Object.defineProperty(req, 'cf', { value: cf });
  return req;
}

async function result(db, path, options) {
  const response = await worker.fetch(request(path, options), { DB: db });
  return { response, data: response.status === 204 ? null : await response.json() };
}

test('initial migration and snapshot match; schema contains no fixture visits', async t => {
  const withoutComments = sql => sql.replace(/^--.*$/gm, '').trim();
  assert.equal(withoutComments(schema), withoutComments(migration));
  const db = database(t, migration);
  const { response, data } = await result(db, '/api/visitors', { method: 'GET', origin: null });
  assert.equal(response.status, 200);
  assert.deepEqual(data, { places: [], startedAt: null, unmappedPageviews: 0, mappedPageviews: 0 });
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), '*');
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.equal(db.sqlite.prepare("SELECT COUNT(*) n FROM sqlite_master WHERE type='table'").get().n, 2);
});

test('production POST uses CF city, rounds before storage, returns committed totals', async t => {
  const db = database(t);
  const req = request('/api/visit?city=Fake', {
    body: JSON.stringify({ city: 'Fake', lat: 1, lon: 2, ip: '192.0.2.1' }),
    headers: { 'Content-Type': 'application/json', 'X-City': 'Fake', 'User-Agent': 'private-agent' },
  });
  const response = await worker.fetch(req, { DB: db });
  const data = await response.json();
  assert.equal(response.status, 200);
  assert.equal(req.bodyUsed, false);
  assert.deepEqual(data.places, [{ lat: 39.9, lon: 116.4, cc: 'CN', city: 'Beijing', n: 1 }]);
  assert.equal(data.mappedPageviews, 1);
  assert.equal(data.unmappedPageviews, 0);
  assert.match(data.startedAt, /^\d{4}-\d{2}-\d{2}T.*Z$/);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  assert.equal(db.batchCalls, 1);
  const row = db.sqlite.prepare('SELECT * FROM visitor_cities').get();
  assert.deepEqual({ ...row }, { cc: 'CN', region: 'Beijing', city: 'Beijing', lat: 39.9, lon: 116.4, n: 1 });
  assert.equal(JSON.stringify(row).includes('private-agent'), false);
});

test('writes from absent, null, local, lookalike and unrelated origins never reach D1', async t => {
  const db = database(t);
  for (const origin of [null, 'null', 'http://localhost:8000', 'https://dianqiao-lei.github.io.evil.invalid', 'https://other.github.io', `${ORIGIN}/`]) {
    const { response } = await result(db, '/api/visit', { origin });
    assert.equal(response.status, 403, String(origin));
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
  }
  assert.equal(db.batchCalls, 0);
});

test('CORS preflight checks routes, method, headers and origin without writing', async t => {
  const db = database(t);
  let response = (await result(db, '/api/visit', {
    method: 'OPTIONS', headers: { 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'Content-Type' },
  })).response;
  assert.equal(response.status, 204);
  assert.equal(response.headers.get('Access-Control-Allow-Methods'), 'POST');
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  response = (await result(db, '/api/visitors', {
    method: 'OPTIONS', origin: 'https://example.invalid', headers: { 'Access-Control-Request-Method': 'GET' },
  })).response;
  assert.equal(response.status, 204);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), '*');
  for (const [options, status] of [
    [{ origin: 'null', headers: { 'Access-Control-Request-Method': 'POST' } }, 403],
    [{ headers: { 'Access-Control-Request-Method': 'DELETE' } }, 405],
    [{ headers: { 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'X-Location' } }, 403],
  ]) {
    assert.equal((await result(db, '/api/visit', { method: 'OPTIONS', ...options })).response.status, status);
  }
  assert.equal(db.batchCalls, 0);
});

test('unknown paths and unsupported methods cannot count a view', async t => {
  const db = database(t);
  for (const [path, method, status] of [
    ['/api/visit/', 'POST', 404], ['/api/missing', 'OPTIONS', 404],
    ['/api/visit', 'GET', 405], ['/api/visit', 'DELETE', 405],
    ['/api/visitors', 'POST', 405], ['/api/visitors', 'HEAD', 405],
  ]) {
    const { response } = await result(db, path, { method });
    assert.equal(response.status, status);
    if (status === 405) assert.match(response.headers.get('Allow'), /OPTIONS/);
  }
  assert.equal(db.batchCalls, 0);
});

test('missing or invalid CF locations count only as unmapped, never body-supplied points', async t => {
  const db = database(t);
  const locations = [undefined, null, {},
    { ...TEST_CITY, country: 'XX' }, { ...TEST_CITY, country: 'T1' },
    { ...TEST_CITY, city: '' }, { ...TEST_CITY, city: 'bad\u0000city' },
    { ...TEST_CITY, latitude: '' }, { ...TEST_CITY, longitude: null },
    { ...TEST_CITY, latitude: '91' }, { ...TEST_CITY, longitude: '-181' },
    { ...TEST_CITY, latitude: NaN }, { ...TEST_CITY, latitude: true },
    { ...TEST_CITY, latitude: '0', longitude: '0' },
    { ...TEST_CITY, latitude: '0.01', longitude: '-0.02' },
  ];
  for (const cf of locations) {
    const req = request('/api/visit', { body: JSON.stringify(TEST_CITY) });
    // Build a separate request to represent entirely absent provider metadata.
    const raw = new Request(req.url, { method: 'POST', headers: req.headers, body: JSON.stringify(TEST_CITY) });
    Object.defineProperty(raw, 'cf', { value: cf });
    assert.equal((await worker.fetch(raw, { DB: db })).status, 200);
  }
  const { data } = await result(db, '/api/visitors', { method: 'GET' });
  assert.deepEqual(data.places, []);
  assert.equal(data.mappedPageviews, 0);
  assert.equal(data.unmappedPageviews, locations.length);
  assert.ok(data.startedAt);
});

test('city primary key merges repeats, retains rounded location and separates regions', async t => {
  const db = database(t);
  await result(db, '/api/visit');
  await result(db, '/api/visit', { cf: { ...TEST_CITY, city: ' beijing ', region: 'beijing', latitude: '39.96' } });
  const { data } = await result(db, '/api/visit', { cf: { ...TEST_CITY, region: 'Different region' } });
  assert.equal(data.places.length, 2);
  assert.equal(data.places[0].n, 2);
  assert.equal(data.places[0].lat, 39.9);
  assert.equal(data.mappedPageviews, 3);
  const plan = db.sqlite.prepare("EXPLAIN QUERY PLAN SELECT n FROM visitor_cities WHERE cc='CN' AND region='Beijing' AND city='Beijing'").all();
  assert.ok(plan.some(row => /PRIMARY KEY/.test(row.detail)));
});

test('multiple pending visits increment atomically with no read/modify/write loss', async t => {
  const db = database(t);
  const responses = await Promise.all(Array.from({ length: 60 }, () => result(db, '/api/visit')));
  assert.ok(responses.every(({ response }) => response.status === 200));
  const { data } = await result(db, '/api/visitors', { method: 'GET' });
  assert.equal(data.places[0].n, 60);
  assert.equal(data.mappedPageviews, 60);
  assert.equal(data.unmappedPageviews, 0);
});

test('GET is read-only and later visits preserve the initial timestamp', async t => {
  const db = database(t);
  await result(db, '/api/visit');
  const firstTimestamp = '2026-10-15T00:00:00.000Z';
  db.sqlite.prepare('UPDATE visitor_totals SET started_at = ?').run(firstTimestamp);
  await result(db, '/api/visit');
  const a = await result(db, '/api/visitors', { method: 'GET', origin: 'https://example.invalid' });
  const b = await result(db, '/api/visitors', { method: 'GET', origin: null });
  assert.deepEqual(a.data, b.data);
  assert.equal(b.data.mappedPageviews, 2);
  assert.equal(b.data.startedAt, firstTimestamp);
});

test('failed transaction rolls back both the point and first-visit timestamp', async t => {
  const db = database(t);
  db.failOn = /INSERT INTO visitor_cities/;
  const { response, data } = await result(db, '/api/visit');
  assert.equal(response.status, 503);
  assert.deepEqual(data, { error: 'Visitor statistics unavailable' });
  db.failOn = null;
  const read = await result(db, '/api/visitors', { method: 'GET' });
  assert.deepEqual(read.data, { places: [], startedAt: null, unmappedPageviews: 0, mappedPageviews: 0 });
});

test('missing migration or DB binding returns failure rather than fabricated zero', async t => {
  const db = database(t, '');
  for (const [path, method] of [['/api/visit', 'POST'], ['/api/visitors', 'GET']]) {
    const { response, data } = await result(db, path, { method });
    assert.equal(response.status, 503);
    assert.deepEqual(data, { error: 'Visitor statistics unavailable' });
  }
  assert.equal((await worker.fetch(request(), {})).status, 503);
  assert.equal(db.sqlite.prepare("SELECT COUNT(*) n FROM sqlite_master WHERE type='table'").get().n, 0);
});

test('reapplying initial schema does not erase existing counts', async t => {
  const db = database(t);
  const { data } = await result(db, '/api/visit');
  db.sqlite.exec(migration);
  assert.deepEqual((await result(db, '/api/visitors', { method: 'GET' })).data, data);
});
