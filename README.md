# Dianqiao Lei · Homepage

This folder contains Dianqiao Lei's static academic homepage. Its layout is based on the public `ChenAnno/ChenAnno.github.io` template used by [chenyanzhe.page](https://chenyanzhe.page/). The personal content and local images are Dianqiao Lei's.

## Local preview

From this folder, run:

```bash
python3 -m http.server 8000
```

Then open <http://localhost:8000>.

The page is plain static HTML/CSS/JS. Its public address is [dianqiao-lei.github.io](https://dianqiao-lei.github.io/).

## GitHub Pages deployment

1. Upload the contents of this folder to [dianqiao-lei/dianqiao-lei.github.io](https://github.com/dianqiao-lei/dianqiao-lei.github.io).
2. Set **Settings → Pages → Source → GitHub Actions**. `.github/workflows/pages.yml` tests and publishes the static site on main-branch updates, manual runs, and a daily schedule at 00:00 Beijing time (16:00 UTC; GitHub may delay scheduled runs). Ordinary source updates preserve the saved map without querying the analytics API.
3. Leave **Custom domain** empty to use the default GitHub Pages address. The workflow copies only public homepage assets into the deployment artifact.

## Search visibility

The homepage intentionally opts out of search results with a static `robots` meta tag in the HTML head: `noindex, nofollow, noimageindex, nosnippet`. This covers the homepage and its section anchors, including name-based searches, while keeping direct links usable. Keep this tag when editing the page, and add it to any future HTML pages.

`robots.txt` blocks crawling of `/images/` but deliberately leaves the HTML accessible: search engines must fetch the page to see `noindex`. Do not add a site-wide `Disallow: /`, which would prevent them from seeing the exclusion. No sitemap is submitted.

This is an indexing preference, not access control. Existing search listings may remain until the engine recrawls the page. Other sites, the public GitHub repository, and crawlers that ignore the rules are outside this page's control. For urgent removal of an existing Google result, use the verified site's Search Console Removals tool alongside the persistent `noindex` rule. See [Google's noindex guidance](https://developers.google.com/search/docs/crawling-indexing/block-indexing) and [image removal guidance](https://developers.google.com/search/docs/crawling-indexing/prevent-images-on-your-page).

## Source of truth

Text is synchronized from the latest files in `../CV`; the portrait, publication teasers, Tsinghua mark, Oxford and Stanford seals, favicon, and dotted world map are under `images/`. The reference repository is public at `https://github.com/ChenAnno/ChenAnno.github.io`.

## Visitor statistics

The footer's **Total Views** uses the free public [soxft/Busuanzi service](https://busuanzi.9420.ltd/) and its [official integration](https://github.com/soxft/busuanzi/wiki/Usage). Counting began on October 15, 2026; earlier visits were not recorded and cannot be recovered. This is a page-view total, not a unique-visitor total: refreshing or revisiting the page adds a view.

`main.js` loads the counter only on `dianqiao-lei.github.io`, so local previews do not count. The service separates sites by their full hostname, including the GitHub Pages subdomain. It receives the current URL, IP address, and browser user agent, and stores an anonymous visitor identifier in browser local storage. No account or API key is included in this repository.

The count shows `Unavailable` if the service cannot be reached; no fallback number is invented. The public service does not guarantee uptime or data preservation.

### Visitor city map

New visits are collected by [51.LA V6](https://v6.51.la/). The homepage loads its
SDK asynchronously only on `https://dianqiao-lei.github.io`; local previews do
not load it. The project uses strict domain matching. Event auto-tracking,
hash-change tracking and screen recording are explicitly disabled. 51.LA still
receives basic visit information, including IP/browser information, and uses
browser identifiers; it is not a city-only or cookie-free collector. Its public
SDK ID is separate from the administrative API secrets stored in GitHub Secrets.

The map reads `visitor-locations.json` from the **same origin as the homepage**
and a validated browser cache. It no longer requires visitors to reach the
Cloudflare Worker to display saved points. Hover, click or focus a point to see
its approximate city and page-view count (labelled `visits`). Saved points
remain visible if a refresh fails, and snapshot metadata retains its actual
update timestamp; no sample locations are added.

The daily Pages workflow queries 51.LA's historical **individual page views**, resolves IPs
against an offline DB-IP City Lite database, and publishes only city-level
aggregates with coordinates rounded to one decimal place. Each record counts
once using its own IP; session totals are not assigned wholesale to one city,
and repeat views sharing an IP or browser identifier are not deduplicated.
Raw records, IPs, UUIDs, URLs and API secrets are never logged, cached, or saved
in published files or workflow artifacts. The homepage includes the required DB-IP
attribution. A VPN may locate a visit at its exit city; unresolved locations
remain unplotted rather than being assigned a guessed city.

Migration uses a fixed real snapshot of the old Cloudflare counts and an
explicit boundary, `2026-10-15T13:19:58.040Z`. Only eligible 51.LA page views after
that boundary contribute new counts. A day's export **replaces** that day's
previous export, even if correcting locations leaves the same total; it is not
added twice. The old Worker's subsequently changing
total is never added to the new stream. `visitor-51la-state.json` preserves the
baseline, daily city summaries and API request budget between deployments.
Legacy session-based daily summaries migrate without resetting the genuine
13-view baseline or request ledger, and are corrected after complete per-PV
fetches replace them.

Synchronization runs daily at **00:00 Beijing time**, fetching yesterday and
rechecking the day before yesterday for late-arriving records; a manual refresh
can also include today's provisional visits. The workflow limits itself to 90 of the
provider's 100 free API calls per month, with at most three requests per run.
With up to 100 daily views, routine fetching and rechecking usually use about
60–62 calls per month, before repairs and manual refreshes. The provider keeps
visit details for 15 days; older missing records may no longer be recoverable.
It preserves valid points on API, quota or database-download failures. GitHub
schedules may run late; the snapshot's `updatedAt` records actual freshness.
Map counts and the independent Busuanzi Total Views have different coverage and
need not match. Visits blocked before reaching 51.LA cannot be recovered, and
unlocatable records remain in the unmapped count. The map therefore cannot
promise a point for every real visit.

See [`scripts/README.md`](scripts/README.md) for the workflow and
[`scripts/51la-README.md`](scripts/51la-README.md) for pagination, quota, recovery
and migration details. The original Worker in `visitor-stats/` and the unused
self-hosted proposal in `visitor-server/` are retained as source references;
neither is required by the daily map pipeline. Do not rerun their deployment
tools or add their live counters to the saved 51.LA state.
