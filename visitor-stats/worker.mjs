// Single-file Cloudflare Worker. Bind a migrated D1 database as DB.
const ALLOWED_ORIGIN = 'https://dianqiao-lei.github.io';

const SELECT_PLACES = `
  SELECT lat, lon, cc, city, n FROM visitor_cities
  ORDER BY n DESC, cc, region, city
`;
const SELECT_TOTALS = `
  SELECT started_at AS startedAt,
         unmapped_pageviews AS unmappedPageviews,
         (SELECT COALESCE(SUM(n), 0) FROM visitor_cities) AS mappedPageviews
  FROM visitor_totals WHERE singleton = 1
`;

function responseHeaders(origin, publicRead) {
  const headers = new Headers({
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Vary': 'Origin',
  });
  if (publicRead) headers.set('Access-Control-Allow-Origin', '*');
  else if (origin === ALLOWED_ORIGIN) {
    headers.set('Access-Control-Allow-Origin', ALLOWED_ORIGIN);
  }
  return headers;
}

function json(data, status, headers) {
  headers.set('Content-Type', 'application/json; charset=utf-8');
  return new Response(JSON.stringify(data), { status, headers });
}

function locationText(value) {
  if (typeof value !== 'string') return '';
  const text = value.trim().normalize('NFC');
  return text.length <= 120 && !/[\u0000-\u001f\u007f]/.test(text) ? text : '';
}

function coordinate(value, limit) {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  if (typeof value === 'string' && !/^-?\d+(?:\.\d+)?$/.test(value.trim())) return null;
  const number = Number(value);
  return Number.isFinite(number) && Math.abs(number) <= limit
    ? Math.round(number * 10) / 10 : null;
}

function cityFromCloudflare(cf) {
  if (!cf) return null;
  const cc = typeof cf.country === 'string' ? cf.country.trim().toUpperCase() : '';
  const city = locationText(cf.city);
  const region = locationText(cf.region);
  const lat = coordinate(cf.latitude, 90);
  const lon = coordinate(cf.longitude, 180);
  // A country centroid or default coordinates must not masquerade as a city.
  if (!/^[A-Z]{2}$/.test(cc) || cc === 'XX' || !city || lat === null || lon === null ||
      (lat === 0 && lon === 0)) {
    return null;
  }
  return { cc, city, region, lat, lon };
}

async function statistics(db, place, recordVisit) {
  const statements = [];
  if (recordVisit) {
    statements.push(db.prepare(`
      UPDATE visitor_totals
      SET started_at = COALESCE(started_at, ?1),
          unmapped_pageviews = unmapped_pageviews + ?2
      WHERE singleton = 1
    `).bind(new Date().toISOString(), place ? 0 : 1));
    if (place) {
      statements.push(db.prepare(`
        INSERT INTO visitor_cities (cc, region, city, lat, lon, n)
        VALUES (?1, ?2, ?3, ?4, ?5, 1)
        ON CONFLICT (cc, region, city) DO UPDATE SET n = visitor_cities.n + 1
      `).bind(place.cc, place.region, place.city, place.lat, place.lon));
    }
  }
  statements.push(db.prepare(SELECT_PLACES), db.prepare(SELECT_TOTALS));
  // D1 batch is a transaction: increments, first-visit time and returned totals
  // commit together. No JavaScript read/modify/write counter or runtime DDL.
  const results = await db.batch(statements);
  if (results.some(result => !result.success)) throw new Error('D1 batch failed');
  const places = results[results.length - 2].results;
  const totals = results[results.length - 1].results[0];
  if (!totals) throw new Error('D1 migration is missing');
  return { places, ...totals };
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    const origin = request.headers.get('Origin');
    const publicRead = path === '/api/visitors';
    const headers = responseHeaders(origin, publicRead);
    const expectedMethod = path === '/api/visit' ? 'POST' : publicRead ? 'GET' : null;

    if (!expectedMethod) return json({ error: 'Not found' }, 404, headers);

    if (request.method === 'OPTIONS') {
      if (!publicRead && origin !== ALLOWED_ORIGIN) {
        return json({ error: 'Origin not allowed' }, 403, headers);
      }
      if (request.headers.get('Access-Control-Request-Method') !== expectedMethod) {
        headers.set('Allow', `${expectedMethod}, OPTIONS`);
        return json({ error: 'Method not allowed' }, 405, headers);
      }
      const requestedHeaders = (request.headers.get('Access-Control-Request-Headers') || '')
        .split(',').map(header => header.trim().toLowerCase()).filter(Boolean);
      if (requestedHeaders.some(header => header !== 'content-type')) {
        return json({ error: 'Header not allowed' }, 403, headers);
      }
      headers.set('Access-Control-Allow-Methods', expectedMethod);
      headers.set('Access-Control-Allow-Headers', 'Content-Type');
      headers.set('Access-Control-Max-Age', '86400');
      return new Response(null, { status: 204, headers });
    }

    if (request.method !== expectedMethod) {
      headers.set('Allow', `${expectedMethod}, OPTIONS`);
      return json({ error: 'Method not allowed' }, 405, headers);
    }
    if (!publicRead && origin !== ALLOWED_ORIGIN) {
      return json({ error: 'Origin not allowed' }, 403, headers);
    }

    try {
      if (!env?.DB) throw new Error('Missing DB binding');
      // Never parse the body, query string, user agent or IP headers for location.
      const place = publicRead ? null : cityFromCloudflare(request.cf);
      return json(await statistics(env.DB, place, !publicRead), 200, headers);
    } catch {
      // No request payload, IP, user agent or raw D1 error is logged or returned.
      return json({ error: 'Visitor statistics unavailable' }, 503, headers);
    }
  },
};
