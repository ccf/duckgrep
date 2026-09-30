Link-preview cards. Each shows the lockup, the tagline ("Grep finds strings. duckgrep answers questions.") in bold Hanken Grotesk with "answers questions." in the `match-bg` highlight, the subhead in `ink-muted`, and the hero proof point as SQL — `callers('get_or_create')` with the django result (1 query, 9 KB vs 22 grep calls, 58 KB) — and the cover motif (disc, pill and dot grid in `duck-yellow` on an ink slab, one `accent-orange` disc). Opaque PNG, no transparency.

- `og-image-dark.png` / `og-image-light.png` — 1200×630, for Open Graph and X/Twitter (`summary_large_image`). Dark is the default: it stands out in light-mode feeds.
- `github-social-dark.png` / `github-social-light.png` — 1280×640 for the repository's Settings → Social preview; content stays inside GitHub's 40px safe border.

```html
<meta property="og:title" content="duckgrep">
<meta property="og:description" content="Grep finds strings. duckgrep answers questions. Give your coding agent a live, queryable model of your codebase, so questions that take dozens of greps take one query.">
<meta property="og:image" content="https://YOUR-DOMAIN/og-image-dark.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
```

Replace YOUR-DOMAIN with the site's real domain. New cards (a blog post, a release) keep this layout: swap the tagline for the post title (max two lines at 48px, one phrase highlighted at most) and the SQL for a real query or command from the post; keep the subhead only on product cards.
