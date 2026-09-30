Favicons and app icons for the website and docs. All use a small-size cut of the duck-d (heavier stem, larger eye) so it survives at 16px: `duck-yellow` d on an `ink` (#0d0d0d) tile, eye in #0d0d0d.

- `favicon.svg` — modern browsers; scales cleanly. Rounded tile.
- `favicon.ico` — 16, 32 and 48px in one file for legacy browsers and tools.
- `favicon-16.png`, `favicon-32.png`, `favicon-48.png` — PNG fallbacks.
- `apple-touch-icon.png` — 180px, full-bleed square (iOS rounds the corners itself).
- `icon-192.png`, `icon-512.png` — PWA / Android icons, rounded tile.
- `icon-maskable-512.png` — full-bleed square with the d inside the 80% safe zone, for Android adaptive icons.
- `icon-yellow-512.png` — the inverse (#0d0d0d d on a yellow tile), for dark or busy grounds such as a GitHub org avatar on a dark profile.
- `site.webmanifest` — names the 192/512/maskable icons; theme colour #0d0d0d.

Put the files at the site root and add to every page's `<head>`:

```html
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
<meta name="theme-color" content="#0d0d0d">
```
