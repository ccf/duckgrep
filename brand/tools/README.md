# Brand tools

Regenerate the brand's derived files. Run from the repo root.

- `python brand/tools/tokens_to_css.py` — `tokens.json` → `tokens.css`. Standard library only.
- `python brand/tools/build_marks.py` — the SVGs in `logos/`. The duck-d geometry is at the top of the file; "grep" and "duckgrep" are outlined from JetBrains Mono ExtraBold and Hanken Grotesk Bold.
- `python brand/tools/build_icons.py` — `favicons/` and `social/` (renders HTML with Playwright's Chromium). Edit the card copy in `card()`.

Both build scripts need `fonttools brotli pillow playwright` (e.g. `uv run --with fonttools --with brotli --with pillow --with playwright python …`, then `playwright install chromium` once) and the font files, fetched from npm:

```bash
mkdir -p brand/tools/fonts && cd brand/tools/fonts
npm pack @fontsource/jetbrains-mono@5.3.0 @fontsource/hanken-grotesk@5.3.0
for f in *.tgz; do mkdir -p "${f%.tgz}" && tar xzf "$f" -C "${f%.tgz}"; done   # GNU and BSD (macOS) tar
```

`brand/tools/fonts/` is git-ignored; set `DUCKGREP_FONTS` to use another directory.
