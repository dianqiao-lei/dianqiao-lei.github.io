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
2. Enable **Settings → Pages → Deploy from a branch** for the `main` branch and `/ (root)`.
3. Leave **Custom domain** empty to use the default GitHub Pages address. The `.nojekyll` file enables plain static-file publishing.

## Source of truth

Text is synchronized from the latest files in `../CV`; the portrait, publication teasers, Tsinghua mark, Oxford and Stanford seals, favicon, and dotted world map are under `images/`. The reference repository is public at `https://github.com/ChenAnno/ChenAnno.github.io`.

## Visitor statistics

The footer's **Total Views** uses the free public [soxft/Busuanzi service](https://busuanzi.9420.ltd/) and its [official integration](https://github.com/soxft/busuanzi/wiki/Usage). Counting began on October 15, 2026; earlier visits were not recorded and cannot be recovered. This is a page-view total, not a unique-visitor total: refreshing or revisiting the page adds a view.

`main.js` loads the counter only on `dianqiao-lei.github.io`, so local previews do not count. The service separates sites by their full hostname, including the GitHub Pages subdomain. It receives the current URL, IP address, and browser user agent, and stores an anonymous visitor identifier in browser local storage. No account or API key is included in this repository.

The count shows `Unavailable` if the service cannot be reached; no fallback number is invented. The public service does not guarantee uptime or data preservation.

### Visitor city map

The existing dotted map now draws real, approximate city locations through the independent [Cloudflare Worker API](https://dianqiao-visitor-stats.leidq51.workers.dev/api/visitors). The homepage remains on GitHub Pages. `visitor-map.js` records one view per production page load and uses the returned city aggregates to size the dots. Hover, click or focus a dot to see the city, country and page-view count. Local previews never record a visit.

The map service uses Cloudflare IP geolocation and stores only city-level aggregate counts, rounded coordinates, an unknown-location count and the first recorded visit time. It stores no raw IP address, user agent or per-visit log. A VPN may place a visit in its exit city; missing locations do not produce invented points. Both the map caption and API report when map counting began. Map counts and Busuanzi Total Views have independent start times and coverage, so they need not match.

The tested Worker source, empty schema, migration and deployment details live in [`visitor-stats/`](visitor-stats/README.md). Workers Logs and Traces are disabled for this service. If the map API is unavailable, a status message is shown and the existing Total Views counter continues independently.
