# Published visitor map

The homepage displays `visitor-locations.json` from its own origin, so saved
points remain available even when a visitor cannot reach an analytics service.
The active pipeline is [`update-51la-snapshot.py`](update-51la-snapshot.py),
documented in detail in [`51la-README.md`](51la-README.md).

## Daily workflow

`.github/workflows/pages.yml` deploys the static site on pushes, manual runs and
every day at **00:00 Beijing time** (16:00 UTC; scheduled runs may be delayed).
Pushes restore and deploy the last valid map
without querying 51.LA. Scheduled runs synchronize closed days; manual runs can
also include today's provisional visits.

Each build validates and merges the checked-in aggregate state, the last
published `visitor-51la-state.json`, and an Actions cache containing only that
aggregate state. Monthly request charges use the largest known value; daily
summaries use validated replacement data, never the sum of repeated exports.
The state cache is saved before deployment, including after recoverable API
failures, so a failed deployment does not normally reset the request budget.

For eligible refreshes, `fetch-geo-database.py` downloads the current monthly
DB-IP City Lite MMDB from its official HTTPS origin. Downloads and decompression
are bounded; redirects, invalid metadata and incomplete files are rejected.
Only a 404 for the current release permits trying the previous month. The MMDB
remains in the runner's temporary directory and is excluded from artifacts.

The exporter queries 51.LA's documented read-only **per-pageview** API with GitHub Secrets
`LA_ACCESS_KEY` and `LA_SECRET_KEY`. IPs are resolved against the local MMDB
only in memory. No raw visit data, IPs, UUIDs, URLs, API responses or secrets are
logged, cached or published. The provider itself receives visitor information
through its SDK, as described in the main README. Keep the required DB-IP
attribution beside the map.

Each returned record contributes one view, even when its `pv` field is zero;
the exporter does not sum session-level `pv` or deduplicate by IP/UUID. Thus a
session that changes IP can contribute views to separate locations. Complete
daily results replace the previous day's distribution, including corrections
whose overall total remains unchanged. Partial pagination never replaces a day.

The free provider allowance is 100 requests/month. The workflow uses a
conservative 90-request ceiling and a three-request run cap, charging each
attempt before sending it. It never purchases additional quota. If a runner is
force-killed before its updated cache is saved, a few reservations may be lost;
the reserve and provider limit remain relevant. See the detailed guide before
changing request limits or using the same API credentials elsewhere.

Each scheduled run prioritizes yesterday, rechecks the day before yesterday
for late-arriving records, then repairs eligible gaps and legacy days. With
at most 100 views per day, these two daily fetches usually use 60–62 requests
per month before repairs or manual runs. More views require more 100-row pages.
The free plan retains raw visit details for 15 days, so exhausted quota or a
long outage can prevent recovery of older missing records.

## Migration and recovery

`visitor-51la-state.json` retains a frozen genuine pre-cutover Cloudflare
snapshot and an explicit collection boundary. New 51.LA daily aggregates are
added only after that boundary; later changes to the old Worker are not folded
into the new total. Re-fetching a day replaces its previous summary, preventing
double counting between manual and scheduled runs.

Version-1 session-derived state migrates to version 2 without changing the
genuine 13-view baseline or the monthly request ledger. Legacy daily counts
remain available until a complete per-pageview fetch replaces them. Per-day
`method` distinguishes retained `session-ip` summaries from corrected
`pageview-ip` summaries; `recordCount` replaces the old `sessionCount` field.

On API, quota, geolocation or remote-state failures, the last valid map remains
available with its genuine `updatedAt`. It does not receive a new timestamp
merely because the homepage was deployed. Unsafe restoration fails closed
instead of resetting the quota or replacing newer deployed points with an old
baseline. See the detailed guide for the first-deployment exception and recovery
rules. A same-origin snapshot cannot recover visits blocked before they reached
51.LA or guarantee city accuracy behind a proxy. Unresolvable records are kept
in the unmapped count instead of being placed at invented locations; not every
real visit can be guaranteed a map point. GitHub schedules can be delayed or
disabled after prolonged inactivity; the snapshot's `updatedAt` records its
actual freshness.

The static deployment allowlist includes `visitor-locations.json` and
`visitor-51la-state.json`, both containing public aggregates only. It excludes
scripts, database files, raw API data and secrets. See
[`51la-README.md`](51la-README.md) for validated restoration, manual commands,
exit codes and missing-day coverage.

## Local checks

With Node.js 24 and Python 3.10 or newer:

```sh
node --test scripts/test/visitor-snapshot.test.mjs visitor-map.test.cjs main-analytics.test.cjs
python3 -m unittest discover -s scripts/test -p 'test_*.py' -v
```

The tests use local fixtures and do not record visits or query the production
API. `update-visitor-snapshot.mjs` and its tests remain as the original
Cloudflare-export implementation; it is **not run by the daily workflow**.
Do not run that legacy exporter over the active 51.LA snapshot, because it lacks
the new daily state and quota ledger.
