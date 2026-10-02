#!/bin/sh
# Assemble duckgrep.dev into _site/ from site/ and the brand assets. It only copies; nothing compiles.
# Cloudflare Workers Builds runs this, then `npx wrangler deploy` serves _site/ (see wrangler.jsonc). An argument builds elsewhere.
set -eu
cd "$(dirname "$0")/.."
OUT="${1:-_site}"
rm -rf "$OUT"
mkdir -p "$OUT/brand"
cp site/index.html site/site.css site/site.js site/_headers "$OUT"/
cp brand/tokens.css brand/components.css "$OUT"/brand/
cp -R brand/logos "$OUT"/brand/logos
rm -f "$OUT"/brand/logos/README.md
# favicons and link-preview cards live at the site root, where browsers and crawlers look for them
for f in brand/favicons/*; do
  case "$f" in *.md) ;; *) cp "$f" "$OUT"/ ;; esac
done
cp brand/social/og-image-dark.png brand/social/og-image-light.png "$OUT"/
echo "built $OUT/ ($(find "$OUT" -type f | wc -l | tr -d ' ') files)"
