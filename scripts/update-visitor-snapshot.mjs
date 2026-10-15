#!/usr/bin/env node
// Read-only export of public city aggregates. Never calls /api/visit.
import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const DEFAULT_PREVIOUS_URL = 'https://dianqiao-lei.github.io/visitor-locations.json';
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_PLACES = 20_000;
const CLOCK_SKEW_MS = 5 * 60 * 1000;

export function endpointToReadURL(endpoint) {
  const url = new URL(endpoint);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash ||
      /\/api\/visit\/?$/.test(url.pathname)) {
    throw new Error('Endpoint must be an HTTPS statistics base URL or /api/visitors URL');
  }
  const path = url.pathname.replace(/\/+$/, '');
  url.pathname = path.endsWith('/api/visitors') ? path : `${path}/api/visitors`;
  return url.href;
}

export function parseOptions(args, env = process.env) {
  let endpoint = env.VISITOR_STATS_ENDPOINT;
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] !== '--endpoint' || !args[i + 1]) throw new Error('Usage: node scripts/update-visitor-snapshot.mjs [--endpoint HTTPS_URL]');
    endpoint = args[++i];
  }
  return endpoint ? { apiURL: endpointToReadURL(endpoint) } : {};
}

// This repository's entry page is static HTML. Read its map configuration so a
// collector migration changes one endpoint, not separate browser/build copies.
export function endpointFromHTML(html) {
  const tags = html.replace(/<!--[\s\S]*?-->/g, '').match(/<[a-z][\w:-]*\b(?:[^"'>]|"[^"]*"|'[^']*')*>/gi) || [];
  const maps = [];
  for (const tag of tags) {
    const attributes = new Map();
    for (const match of tag.matchAll(/\s([a-z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/gi)) {
      attributes.set(match[1].toLowerCase(), match[2] ?? match[3]);
    }
    if (attributes.get('id') === 'visitor-map') maps.push(attributes);
  }
  if (maps.length !== 1 || !maps[0].get('data-endpoint')) {
    throw new Error('index.html must contain one #visitor-map with a data-endpoint');
  }
  return endpointToReadURL(maps[0].get('data-endpoint'));
}

function timestamp(value, name, now) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)) {
    throw new Error(`${name} must be an ISO UTC timestamp`);
  }
  const date = new Date(value);
  if (!Number.isFinite(date.getTime()) || date.toISOString() !== value ||
      date.getTime() > now.getTime() + CLOCK_SKEW_MS) {
    throw new Error(`${name} is not a valid timestamp`);
  }
  return value;
}

function counter(value, name) {
  if (!Number.isSafeInteger(value) || value < 0) throw new Error(`${name} must be a nonnegative integer`);
  return value;
}

/** Validate the complete response; never silently drop invalid city records. */
export function validateStatistics(input, { snapshot = false, now = new Date() } = {}) {
  if (!input || typeof input !== 'object' || Array.isArray(input) ||
      !Array.isArray(input.places) || input.places.length > MAX_PLACES) {
    throw new Error('Invalid visitor statistics');
  }
  let sum = 0;
  const places = input.places.map(place => {
    if (!place || typeof place !== 'object' || Array.isArray(place) ||
        !Number.isFinite(place.lat) || Math.abs(place.lat) > 90 ||
        !Number.isFinite(place.lon) || Math.abs(place.lon) > 180 ||
        (place.lat === 0 && place.lon === 0) ||
        typeof place.cc !== 'string' || !/^[A-Z]{2}$/.test(place.cc) || place.cc === 'XX' ||
        typeof place.city !== 'string' || !place.city.trim() || place.city.length > 120 ||
        /[\u0000-\u001f\u007f]/.test(place.city)) {
      throw new Error('Invalid city record');
    }
    const n = counter(place.n, 'City page views');
    if (n === 0) throw new Error('City page views must be positive');
    sum += n;
    if (!Number.isSafeInteger(sum)) throw new Error('City page view total is too large');
    // Explicitly allow only public aggregate fields into the published file.
    return { lat: place.lat, lon: place.lon, cc: place.cc, city: place.city.trim(), n };
  });
  const mappedPageviews = counter(input.mappedPageviews, 'Mapped page views');
  const unmappedPageviews = counter(input.unmappedPageviews, 'Unmapped page views');
  if (sum !== mappedPageviews || !Number.isSafeInteger(mappedPageviews + unmappedPageviews)) {
    throw new Error('Visitor totals do not match city records');
  }
  const total = mappedPageviews + unmappedPageviews;
  const startedAt = input.startedAt === null && total === 0
    ? null : timestamp(input.startedAt, 'startedAt', now);
  const result = { places, mappedPageviews, unmappedPageviews, startedAt };
  if (snapshot) {
    result.updatedAt = timestamp(input.updatedAt, 'updatedAt', now);
    if (startedAt && startedAt > result.updatedAt) throw new Error('Snapshot predates the first visit');
  }
  return result;
}

async function fetchJson(url, fetchFn) {
  const target = new URL(url);
  if (target.protocol !== 'https:' || target.username || target.password) {
    throw new Error('Statistics sources must be HTTPS URLs without credentials');
  }
  const response = await fetchFn(target.href, {
    method: 'GET',
    headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' },
    redirect: 'error',
    signal: AbortSignal.timeout(20_000),
  });
  if (!response.ok || !response.body) throw new Error(`Statistics request returned HTTP ${response.status}`);
  const length = response.headers.get('content-length');
  if (length && Number(length) > MAX_RESPONSE_BYTES) throw new Error('Statistics response is too large');
  const reader = response.body.getReader();
  const chunks = [];
  let bytes = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_RESPONSE_BYTES) throw new Error('Statistics response is too large');
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  return JSON.parse(Buffer.concat(chunks, bytes).toString('utf8'));
}

function total(data) {
  return data.mappedPageviews + data.unmappedPageviews;
}

function bestBaseline(local, published) {
  if (!local) return published;
  if (!published) return local;
  // Scheduled builds start from Git, so recover the last successful deployment
  // before contacting the API. A stale CDN response must not roll counters back.
  if (total(published) < total(local)) return local;
  if (total(local) < total(published)) return published;
  return published.updatedAt > local.updatedAt ? published : local;
}

/** On source failure, retain genuine previous statistics and their timestamp. */
export async function updateSnapshot({
  outputPath = resolve('visitor-locations.json'),
  apiURL,
  indexPath = resolve('index.html'),
  previousURL = DEFAULT_PREVIOUS_URL,
  fetchFn = globalThis.fetch,
  now = new Date(),
  logger = console,
} = {}) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) throw new Error('Invalid build date');
  const sourceURL = apiURL ? endpointToReadURL(apiURL) : endpointFromHTML(await readFile(indexPath, 'utf8'));
  let local = null;
  let published = null;
  try {
    local = validateStatistics(JSON.parse(await readFile(outputPath, 'utf8')), { snapshot: true, now });
  } catch (error) {
    if (error.code !== 'ENOENT') logger.warn(`Local visitor snapshot rejected: ${error.message}`);
  }
  try {
    published = validateStatistics(await fetchJson(previousURL, fetchFn), { snapshot: true, now });
  } catch (error) {
    logger.warn(`Published visitor snapshot unavailable: ${error.message}`);
  }
  let result = bestBaseline(local, published);
  let refreshed = false;
  try {
    const current = validateStatistics(await fetchJson(sourceURL, fetchFn), { now });
    if (result && (total(current) < total(result) ||
        current.mappedPageviews < result.mappedPageviews ||
        current.unmappedPageviews < result.unmappedPageviews)) {
      throw new Error('API counters regressed; retaining the last known statistics');
    }
    if (current.startedAt && current.startedAt > now.toISOString()) {
      throw new Error('First visit is later than this build');
    }
    result = { ...current, updatedAt: now.toISOString() };
    refreshed = true;
  } catch (error) {
    logger.warn(`Live visitor statistics unavailable: ${error.message}`);
  }
  if (!result) throw new Error('No valid visitor snapshot exists; refusing to publish empty or invented statistics');

  await mkdir(dirname(outputPath), { recursive: true });
  const temporaryPath = `${outputPath}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporaryPath, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
    await rename(temporaryPath, outputPath);
  } finally {
    await rm(temporaryPath, { force: true });
  }
  logger.info(`${refreshed ? 'Updated' : 'Retained'} visitor snapshot: ${result.places.length} cities, ${total(result)} page views, as of ${result.updatedAt}`);
  return { refreshed, data: result };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  Promise.resolve().then(() => updateSnapshot(parseOptions(process.argv.slice(2)))).catch(error => {
    console.error(`Visitor snapshot build failed: ${error.message}`);
    process.exitCode = 1;
  });
}
