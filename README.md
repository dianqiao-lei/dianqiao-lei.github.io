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
2. Set **Settings → Pages → Source → GitHub Actions**. `.github/workflows/pages.yml` tests and publishes the static site on main-branch updates, manual runs, and an hourly schedule.
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

The existing dotted map now draws real, approximate city locations through the independent [Cloudflare Worker API](https://dianqiao-visitor-stats.leidq51.workers.dev/api/visitors). The homepage remains on GitHub Pages. `visitor-map.js` records one view per production page load and uses the returned city aggregates to size the dots. Hover, click or focus a dot to see the city, country and page-view count. Local previews never record a visit.

The map service uses Cloudflare IP geolocation and stores only city-level aggregate counts, rounded coordinates, an unknown-location count and the first recorded visit time. It stores no raw IP address, user agent or per-visit log. A VPN may place a visit in its exit city; missing locations do not produce invented points. Both the map caption and API report when map counting began. Map counts and Busuanzi Total Views have independent start times and coverage, so they need not match.

The tested Worker source, empty schema, migration and deployment details live in [`visitor-stats/`](visitor-stats/README.md). Workers Logs and Traces are disabled for this service. The map independently loads `visitor-locations.json` from the homepage origin and a validated browser cache before contacting the live API. A network failure retains genuine historical points and shows their saved time; it never replaces them with invented locations. The existing Total Views counter continues independently.

The Pages workflow refreshes the public aggregate snapshot hourly with read-only requests. It retains the last valid published snapshot if the API is unavailable. Scheduled runs may be delayed, so the displayed timestamp is the source of truth. See [`scripts/README.md`](scripts/README.md) for validation and recovery details. This improves display availability but cannot recover new visits that never reach the collector.

A proposed self-hosted replacement lives in `visitor-server/`, but is **not active**. Setup stopped because the server's public HTTP port was unreachable during certificate validation. The homepage still uses the Cloudflare endpoint. Do not change the endpoint to the server IP or rerun its bootstrap without resolving and reviewing the deployment first; the owner has chosen to pursue a hosted alternative instead of requesting server network changes.

### Mainland-friendly hosted collection

The homepage also initializes [51.LA V6](https://v6.51.la/) for its own HTTPS hostname only. The SDK loads asynchronously, with event auto-tracking, hash-change tracking, and screen recording explicitly disabled. Local previews do not load it. 51.LA receives basic visit information (including IP/browser information) and uses browser identifiers; it is not a city-only or cookie-free collector. The public SDK ID is not an administrative API secret.

The 51.LA project is bound to `dianqiao-lei.github.io` with strict domain matching. At this stage, collecting data in the hosted dashboard and exporting it onto the custom city map are separate: automatic map import is not yet configured. The map continues to read its existing Cloudflare aggregates and saved public snapshot until that import is validated.
