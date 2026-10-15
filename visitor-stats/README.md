# City-level visitor map API

This independent Cloudflare Worker supplies the homepage's existing map. The
website itself stays on GitHub Pages. `worker.mjs` has no imports or build step;
it can also be pasted directly into the Cloudflare Dashboard editor.

## What is recorded

- Each accepted `POST /api/visit` adds one page view. Repeat visits count; there
  is no unique-visitor identifier or deduplication.
- Location comes exclusively from Cloudflare's `request.cf`: country, region,
  city, latitude and longitude. Body data and client-supplied location headers
  are ignored. Coordinates are rounded to one decimal place **before storage**.
- D1 stores one aggregate row per country/region/city, plus an unknown-location
  count and the timestamp of the first accepted visit. It stores no raw IP,
  user agent, page URL, referrer, per-visit time or browsing history.
- Missing or invalid city data, including the default `(0, 0)` coordinate,
  increments `unmappedPageviews` without a point.
  IP geolocation is approximate and may describe a VPN or network exit city.
- The map starts counting when its first production request is accepted. Its
  totals are independent of the footer's existing Busuanzi Total Views; prior
  visits cannot be reconstructed or imported as invented city points.

## HTTP contract

`POST /api/visit` requires the exact `Origin: https://dianqiao-lei.github.io`.
Send no body and no custom headers. `GET /api/visitors` is public, read-only and
never increments a counter. Both return the same JSON shape:

```json
{
  "places": [],
  "mappedPageviews": 0,
  "unmappedPageviews": 0,
  "startedAt": null
}
```

After visits, each place has `{ "lat", "lon", "cc", "city", "n" }`, and
`startedAt` is the first accepted visit's ISO UTC timestamp. `n` is page views,
not people. `mappedPageviews` equals the sum of all returned `n` values. Region
distinguishes same-named cities in storage but is not included in the response.

Responses use `Cache-Control: no-store`. `OPTIONS` is handled without touching
D1. Unsupported paths return 404, unsupported methods 405, disallowed write
origins 403, and unavailable/missing D1 or migrations 503. No empty-success
payload is fabricated for database failures.

The frontend should send **one POST per production page load**, then draw the
returned payload. On failure it may GET existing statistics; do not retry POST
automatically, because a lost response may still have committed the visit.
Local previews must not send production writes. CORS restricts browsers; an
outside HTTP client can forge Origin, so these public counters are not an
authenticated or abuse-proof analytics system.

## Tests (no account, network, or installed dependencies)

Use Node.js 22.13+ (Node 24 recommended):

```sh
cd visitor-stats
node --test
```

Tests run the real SQL in an in-memory SQLite database through a small D1 API
adapter. Fixture locations never leave the test process or enter a remote DB.
They cover source/method checks, CORS, body spoofing, rounding, unknown locations,
atomic increments and transaction rollback, start time and read-only GETs.
This does not replace a deployment smoke check of Cloudflare bindings and GeoIP.

## Deploy with the Cloudflare Dashboard

1. Sign into the intended Cloudflare account and create a D1 database named
   `dianqiao-visitor-stats`.
2. Run `schema.sql` in that database's console. It creates empty aggregates only;
   there are no sample visits. Do not run test fixtures against the remote DB.
3. Create the Worker `dianqiao-visitor-stats`, paste `worker.mjs`, and add a D1
   binding named **DB** pointing to the created database. Keep Workers Logs /
   observability disabled; the Worker itself does not log requests.
4. Deploy and record the actual `https://…workers.dev` URL. No custom domain or
   change to the GitHub Pages hostname is needed.
5. GET that URL's `/api/visitors` to confirm an empty payload before any real
   visitor arrives. This check does not count a view. Add the real base URL to
   the homepage's map configuration and publish the frontend separately.
6. Confirm a normal production visit supplies city data, or is honestly counted
   as unmapped. Never POST synthetic `request.cf` locations into production.

Cloudflare Dashboard/Playground previews may omit `request.cf`, so preview
locations must not be used as evidence that live geolocation is broken.

## Optional CLI deployment and future migrations

Install dependencies and authenticate:

```sh
npm install
npx wrangler login
```

Create a D1 database only if one has not already been created in the Dashboard:

```sh
npx wrangler d1 create dianqiao-visitor-stats
```

Copy its **actual** `database_id` into `wrangler.jsonc` under the DB binding. The
checked-in ID refers to the database created for this homepage; verify it before
any remote migration or deployment. Do not let missing configuration create an unintended
additional database. No API token belongs in this repository.

For a new database, apply the initial migration explicitly, then deploy:

```sh
npx wrangler d1 migrations apply dianqiao-visitor-stats --remote
npx wrangler deploy
```

If `schema.sql` was already applied via the Dashboard, the initial migration is
safe to apply: `IF NOT EXISTS` and `ON CONFLICT DO NOTHING` preserve counters and
only add the migration record. Future changes belong in new numbered SQL files
under `migrations/`; update `schema.sql` as a fresh-install snapshot. No schema
changes occur during visitor requests.

For local development, use `npm run db:migrate:local` followed by `npm run dev`.
Do not use `wrangler dev --remote` with the production database for test writes.
The city primary key is indexed, and SQL `n = n + 1` runs within a D1 `batch`
transaction; concurrent visits do not depend on a lossy read/modify/write loop.

## Official references

- [Cloudflare request.cf location fields](https://developers.cloudflare.com/workers/runtime-apis/request/)
- [D1 prepared statements and bind](https://developers.cloudflare.com/d1/worker-api/prepared-statements/)
- [D1 batch transaction behavior](https://developers.cloudflare.com/d1/worker-api/d1-database/)
- [D1 migrations](https://developers.cloudflare.com/d1/reference/migrations/)
- [Wrangler D1 configuration](https://developers.cloudflare.com/workers/wrangler/configuration/)

## Production deployment

- API: `https://dianqiao-visitor-stats.leidq51.workers.dev`
- Worker: `dianqiao-visitor-stats`
- D1 database: `dianqiao-visitor-stats`, bound as `DB`
- D1 ID: `6872a84b-19c1-4bd8-a740-0638006055a7` (resource identifier, not a secret)
