# 51.LA daily city snapshot

`update-51la-snapshot.py` queries the official read-only historical page-view API,
looks up each page view's IP in a local DB-IP City Lite MMDB, and publishes only
city aggregates. It does not send an IP to another geolocation provider. API
responses, IP addresses, UUIDs, referrers, and visited URLs are never saved or
printed. The 51.LA collection service itself still receives those fields; this
export is not a claim that 51.LA collects only anonymous city counts.

## Verified API contract

Sources: [historical page-view API](https://v6.51.la/doc/index.html#/访问明细/2),
[signing rules](https://v6.51.la/doc/index.html#/调用说明/README),
and [official free-plan limits](https://v6.51.la/).

- Read-only query: `POST https://v6-open.51.la/open/visitor/detail/page/list`.
- Body: `maskId`, `day` (`YYYY-MM-dd`), `page`, `size=100`, `accessKey`,
  four-character `nonce`, millisecond `timestamp`, and `sign`.
- Signature: uppercase SHA256 hex of the UTF-8 text
  `accessKey=…&nonce=…&secretKey=…&timestamp=…`, in that exact order.
- Keep the account's default **medium security** setting. Low-security mode
  signs differently; high-security mode encrypts the response and is not
  implemented by this exporter.
- Each returned row counts as **one page view**, regardless of its `pv` field.
  City `n` counts these records, rather than summing a session's `pv`. If a
  visitor changes IP within one session, each page view is located using its
  own IP instead of assigning the entire session to one location.
- A UUID is not a required event ID. Repeated IPs, UUIDs, timestamps, or even
  identical rows are not deduplicated: they can represent genuine repeat views.
- `dateTime` is interpreted in the account's China time zone
  (`Asia/Shanghai`). Confirm this on the initial real response if the provider
  changes its time-zone behavior. No client locale is used.
- All reported pages must be present, with the requested page number/size,
  stable total/page counts, and exactly the expected number of rows. A changing
  current-day list may fail this consistency check and is retried later.

## Files and dependencies

Python 3.10+ and `maxminddb==3.2.0` are required for a query. Restore-only mode and
the synthetic tests use only the standard library. Supply a current city MMDB
with `--mmdb`; the exporter does not download one or open network geolocation
connections. Keep the DB-IP attribution required by the database's license in
the published site.

`visitor-51la-state.json` contains the frozen genuine pre-cutover snapshot,
one replaceable city aggregate per day, configuration, and request counters.
It is safe to publish with the site: it contains no per-visitor identifiers.
`visitor-locations.json` contains the existing map schema plus `coverage`.
The state file is necessary to avoid both double counting and resetting the
monthly request budget. Do not replace it with a fresh state on each deployment.

State version 2 records each day's `method` (`pageview-ip`, or retained legacy
`session-ip`) and `recordCount`. Reading a version-1 state preserves its existing
city counts, the genuine **13-view baseline**, and request ledger while marking
its daily data as legacy. Each legacy day is corrected only after a successful
full per-PV fetch; migration itself invents no visits and resets no allowance.

Run tests without any external requests:

```sh
python3 -m unittest discover -s scripts/test -p 'test_51la_snapshot.py' -v
```

## One-time cutover

Freeze the last real old-collector snapshot **before enabling the new SDK**.
Do not add subsequently growing old-collector counters to the new totals.
Use an explicit cutover boundary and a baseline whose `updatedAt` does not
postdate that boundary:

```sh
python3 scripts/update-51la-snapshot.py \
  --initialize --restore-only \
  --baseline visitor-locations.json \
  --cutover-at 2026-10-15T13:19:58.040Z
```

This initializes only the known baseline, without a 51.LA API call or invented
visits. `--initial-monthly-used N` can conservatively include requests already
made during account setup. Initialization refuses an existing state. The
cutover date is derived from the timestamp in `Asia/Shanghai`; it is not a
second independently configured date.

Page-view timestamps are checked against this boundary, so a session spanning
it does not force all of that session's views into one side. The API's example
timestamps have only second precision; the collection boundary and provider
timestamp precision still limit claims about an exact subsecond cutover.

## Restore and daily synchronization

Before any build, restore the previous Actions cache to a separate location,
then merge it with the last published aggregate state:

```sh
python3 scripts/update-51la-snapshot.py --restore-only \
  --state-url https://dianqiao-lei.github.io/visitor-51la-state.json \
  --cache-state .visitor-analytics-cache/visitor-51la-state.json
```

The checked-out state, optional valid cache, and successful remote state must
have the same mask, baseline, and cutover. For each month, the largest known
request charge is kept. For each day, the latest complete export replaces the
older export; summaries are never added to each other. Missing cache files are
normal; an existing invalid cache fails closed because its request charges
cannot safely be discarded. If a network failure prevents reading remote state,
only a validated restored cache permits retaining the snapshot with exit `2`;
an old checked-in baseline alone is insufficient. A successfully downloaded
but invalid or conflicting document always exits `1`, without replacing the
public snapshot. This prevents a stale baseline from overwriting newer history
or resetting known request charges.

The first deployment has one narrow exception: an actual HTTP 404 may publish
the genuine initial seed when it has no daily summaries, no request charges,
and no previous attempt timestamp. It exits `2` and makes no API query. After
publishing the initial state, a separate manual refresh can query the API.
An HTTP 404 can also use an existing validated cache. In either exit-`2` case,
publish the retained snapshot but skip an API query until restoration succeeds.

For a scheduled or requested refresh, pass secrets only as environment
variables `LA_ACCESS_KEY` and `LA_SECRET_KEY`:

```sh
python3 scripts/update-51la-snapshot.py \
  --mmdb .visitor-analytics/dbip-city-lite.mmdb \
  --monthly-limit 90 --max-requests-per-run 3
```

The workflow runs daily at **00:00 Beijing time** (16:00 UTC; GitHub may delay
the run). It queries yesterday first, then rechecks the day before yesterday
for late-arriving records, then repairs older missing/provisional or legacy
days within the latest 15 calendar dates. A day already exported on or after
its second following day does not need another routine late-arrival recheck.
`--include-today` optionally queries today after closed days; today's summary
remains explicitly provisional. The provider's free plan retains detail logs
for 15 days, so older missing days remain identified as gaps instead of being
fabricated or repeatedly queried. `--lookback-days` can reduce the window but
cannot exceed 14 days before today.

Daily summaries are committed only after every page succeeds. A corrected
export replaces its day's previous distribution even when the total is
unchanged: equal totals do not imply equal locations. Legacy session aggregates
are replaced by the verified per-PV result, not added to it. If a fetched result
has a lower combined mapped/unmapped total than the saved day, its previous
summary is retained for review, including during legacy migration; history is
not silently reduced. Country-only or unresolvable IP locations
increase `unmappedPageviews`; they are never plotted at guessed city centers.

The provider's free allowance is 100 API requests/month. The default local
budget is 90, leaving 10 for setup/debugging/uncertain interrupted runs. Every
attempt is charged and atomically saved **before** the HTTP request, including
timeouts and malformed responses; there are no automatic retries. The first
page may reveal that the remaining pages exceed the current run/month budget.
In that case the whole daily update is discarded, its old aggregate is retained,
and the request charge remains. A day requiring more than three pages cannot
complete under the default run cap; a manual run may explicitly increase
`--max-requests-per-run` within the remaining monthly allowance. No paid quota
is purchased automatically.

With at most 100 page views per day, fetching yesterday plus the late-arrival
recheck usually needs about **60–62 requests per month**, before manual
refreshes, repairs, and retries on later runs. Every additional 100-row page
uses another request. Higher traffic can exhaust the free budget; this is not
an unlimited export service. Gaps must be repaired before the provider's
15-day retention expires if the original records are to remain recoverable.

Run workflows serially and save the state cache even after an API failure,
before attempting deployment. A cache is not an atomic, external transaction:
a runner killed between an HTTP request and cache upload can lose a charge.
The 10-call reserve mitigates this; the provider's 100-call hard limit remains
the final protection. Shared credentials used by other tools also consume the
provider allowance and must be included in this operational budget.

## Exit codes and freshness

- `0`: restoration/query completed normally; safe to cache and deploy.
- `2`: a valid prior snapshot is retained, but API, state restoration, quota,
  or geolocation was unavailable. Safe to cache and deploy the retained files;
  inspect the fixed, non-sensitive reason in `coverage.lastOutcome`. Never
  interpret this as a completed daily fetch.
- `1`: no valid durable state, invalid cache, downloaded state that is invalid
  or conflicts with saved history, network failure without recovery cache, or
  an unrecoverable local write failure. Do not deploy new output. Preserve any
  valid charged state cache.

`updatedAt` is the latest **successful** daily export (or the frozen baseline's
timestamp). A failed attempt does not make old data appear fresh.
`coverage.completeThrough` describes contiguous fully synchronized dates from
cutover, `missingDays` records gaps, and `provisionalDays` identifies incomplete
calendar days. A fully fetched closed day can still receive its later recheck.
`lastAttemptAt` is separate from data freshness. A snapshot makes
known points load from the same origin as the homepage; it cannot reconstruct
visits that never reached 51.LA (for example, blocked scripts or network
requests), guarantee city-level IP accuracy, or identify a visitor's physical
location when they use a VPN/proxy. Unlocatable records remain counted as
unmapped; no claim is made that every real visit can be plotted.
