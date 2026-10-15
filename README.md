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

Text is synchronized from the latest files in `../CV`; the portrait, publication teasers, Tsinghua mark, Oxford and Stanford seals, favicon, and decorative world map are under `images/`. The reference repository is public at `https://github.com/ChenAnno/ChenAnno.github.io`.

## Visitor statistics

The footer's **Total Views** uses the free public [soxft/Busuanzi service](https://busuanzi.9420.ltd/) and its [official integration](https://github.com/soxft/busuanzi/wiki/Usage). Counting began on October 15, 2026; earlier visits were not recorded and cannot be recovered. This is a page-view total, not a unique-visitor total: refreshing or revisiting the page adds a view.

`main.js` loads the counter only on `dianqiao-lei.github.io`, so local previews do not count. The service separates sites by their full hostname, including the GitHub Pages subdomain. It receives the current URL, IP address, and browser user agent, and stores an anonymous visitor identifier in browser local storage. No account or API key is included in this repository.

The count shows `Unavailable` if the service cannot be reached; no fallback number is invented. The public service does not guarantee uptime or data preservation. The dotted world map is decorative and contains no simulated visitor markers or claimed visitor-location data.
