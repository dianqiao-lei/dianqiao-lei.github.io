# Dianqiao Lei · Homepage

This folder contains the static homepage under `/Users/dianqiao/Desktop/make_homepage/Homepage`. Its DOM, responsive breakpoints, cards, sidebar outline, publication timeline, footer hooks, and desktop root scaling are aligned to the public `ChenAnno/ChenAnno.github.io` template used by [chenyanzhe.page](https://chenyanzhe.page/). The personal content and local images are Dianqiao Lei's.

## Local preview

From this folder, run:

```bash
python3 -m http.server 8000
```

Then open <http://localhost:8000>.

The page is plain static HTML/CSS/JS. The selected production domain is `leidq.page`; the root `CNAME` file contains that hostname for GitHub Pages deployment.

## GitHub Pages deployment

1. Register `leidq.page` with a domain registrar and keep access to its DNS management page.
2. Create a public GitHub repository, upload the contents of this folder, and enable **Settings → Pages → Deploy from a branch** for the `main` branch and `/ (root)`.
3. In **Settings → Pages → Custom domain**, enter `leidq.page` and save. GitHub will retain the `CNAME` file.
4. At the registrar, create these DNS records:
   - `A` record, host `@`, values `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, and `185.199.111.153`;
   - `CNAME` record, host `www`, value `<your-github-username>.github.io`.
5. Return to GitHub Pages and enable **Enforce HTTPS** after the certificate is issued. `.page` domains require HTTPS in browsers.

DNS changes can take several minutes and occasionally up to 24 hours to propagate.

## Source of truth

Text is synchronized from the latest files in `../CV`; the portrait, publication teasers, Tsinghua mark, Oxford and Stanford seals, favicon, and local visitor-map placeholder are under `images/`. The reference repository is public at `https://github.com/ChenAnno/ChenAnno.github.io`.

The footer now includes a visible 900×409 dotted world map. It shows local fallback markers until a personal `/api/visitors` endpoint is configured; markers can be hovered, clicked, or focused from the keyboard.
