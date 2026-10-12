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

Text is synchronized from the latest files in `../CV`; the portrait, publication teasers, Tsinghua mark, Oxford and Stanford seals, favicon, and local visitor-map placeholder are under `images/`. The reference repository is public at `https://github.com/ChenAnno/ChenAnno.github.io`.

The footer now includes a visible 900×409 dotted world map. It shows local fallback markers until a personal `/api/visitors` endpoint is configured; markers can be hovered, clicked, or focused from the keyboard.
