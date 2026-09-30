# duckgrep brand

The duckgrep design system: the rules for the website, docs, this repo's README and social cards. Open `index.html` in a browser for a specimen of everything below.

## What's here

| path | what |
|---|---|
| `README.md` | the rules (this file) |
| `tokens.json` | source of truth for colour (light and dark), type, spacing and radius |
| `tokens.css` | CSS custom properties and `.type-*` classes, generated from `tokens.json` |
| `components.css` | `.dg-btn` buttons, `.dg-term` terminal blocks, `.dg-match` highlights |
| `components/` | usage notes for each component |
| `logos/` | the duck-d mark, lockups and `dgrep` wordmark (pure-path SVG, no fonts needed) |
| `favicons/` | favicon set, app icons, `site.webmanifest` |
| `social/` | Open Graph and GitHub social-preview cards |
| `tools/` | scripts that regenerate `tokens.css`, the marks, icons and cards |
| `index.html` | specimen page |

## Using it on a site

```html
<link rel="stylesheet" href="/brand/tokens.css">      <!-- loads the Google Fonts too -->
<link rel="stylesheet" href="/brand/components.css">
```

Colours are `var(--token-name)`; type styles are classes (`.type-display`, `.type-body`, `.type-code` …). Light is the default; dark follows the OS, and `<html data-theme="light|dark">` forces either. Change a value in `tokens.json`, then run `python brand/tools/tokens_to_css.py`.

---

duckgrep gives your coding agent **a live, queryable model of your codebase** (definitions, callers, imports, history), so questions that take dozens of greps take one query. Its identity borrows DuckDB's vocabulary — the yellow, near-black ink, flat surfaces, a grotesk plus JetBrains Mono — and adds one idea of its own: the **duck-d**, a lowercase *d* built from a disc and a pill that doubles as a duck's head looking up, bill first. Use this system for the website, docs, the GitHub README and social cards.

## Name and mark

- The product is **duckgrep**, always lowercase, one word, set in the sans. The CLI is also **`duckgrep`** (`duckgrep callers`, `duckgrep q`) and the MCP tool is **`query(sql)`**, both always in mono. Never "DuckGrep", "Duck Grep" or "DG".
- **dgrep** is a monogram only — the `dgrep` wordmark for stickers, avatars and tight spaces. It is not a command: never write `dgrep` in install steps, examples or prose.
- The **duck-d** is the mark. Use `duckgrep-mark.svg` (yellow d on an ink tile) as the default avatar, favicon and GitHub org image; `duckgrep-mark-yellow.svg` (ink d on a yellow tile) where the ground is already dark or busy.
- Use `duckgrep-lockup-light.svg` / `-dark.svg` (tile + "duckgrep") for the site header, the top of the GitHub README and anywhere the name must be read. Use `dgrep-wordmark-light.svg` / `-dark.svg` only as a playful secondary mark (stickers, merch, a 404 page).
- The duck-d's eye is its counter. Never fill it, move it, or add a bill, feet or wings. Never recolour the d outside ink and `duck-yellow`.
- Clear space around any mark is the width of the d's stem; minimum size is 16px for the tile, 20px tall for lockups.
- Favicons and app icons (`favicons/`) use a small-size cut of the duck-d with a heavier stem and larger eye; never shrink the full mark below 32px, use `favicon.svg` instead. Link previews use the cards in `social/`; each folder's README has the `<head>` snippet.
- The duck-d is an homage, not a copy: never place it inside a circle, and never combine it with DuckDB's own logo in one lockup.

For the GitHub README, which cannot follow the viewer's theme with CSS, use a `<picture>` with both lockups:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/logos/duckgrep-lockup-dark.svg">
  <img alt="duckgrep" src="brand/logos/duckgrep-lockup-light.svg" height="48">
</picture>
```

## Messaging

- **Tagline:** "Grep finds strings. duckgrep answers questions." Always two sentences, in that order, verbatim. Set it on two lines, breaking after "strings."; when it is the headline, give "answers questions" the `match-bg`/`match-ink` highlight.
- **Subhead:** "Give your coding agent a live, queryable model of your codebase (definitions, callers, imports, history), so questions that take dozens of greps take one query." Use it under the tagline, in link previews and as the repo description.
- **The problem:** agents find their way around with chains of `grep` → read file → `grep` again. That works for "where is this string." The questions that matter when changing code are structural — *who calls this, what does it call, what breaks if I change it, what's dead* — and each turns into a loop of searches, file reads and guesses, paid for in turns and tokens.
- **Four reasons, in this order, with their bold lead-ins as written** (feature cards or section heads):
  1. **It answers structural questions, not text matches.**
  2. **It answers questions no tool author anticipated.** — a query language, not a fixed menu.
  3. **It joins code with its history.** — call graph, git churn and authorship in one place.
  4. **It's trustworthy mid-edit.** — refreshed before every query; every edge says how it was resolved.
- **Hero proof point** — quote exactly, with the repo named: on django, finding every call to `get_or_create` and the function it sits in took 22 grep calls and 58 KB of output; duckgrep answered it in one query and 9 KB. Supporting: "everything within 3 hops of `execute_sql`" took 110 calls and 104 KB vs 1 call and 113 bytes; typical queries run in 30–90 ms on django (7k files). Link every number to `bench/RESULTS.md`.
- **The mechanism** (for "How it works", never the headline): tree-sitter parses the repo into a single DuckDB file, exposed as one read-only `query(sql)` MCP tool whose description carries the whole schema.
- **Honesty is part of the brand.** State what it can't resolve (resolution tiers, known gaps) as plainly as the README does; never claim full type resolution.

## Voice

- Plain and confident, like the repo README: a bold claim as a full sentence, then the evidence that backs it. Contrast is the house move ("Grep finds strings. duckgrep answers questions."; "one query, not a search loop"). "It answers at once and builds the index in the background." not "duckgrep empowers you to…".
- Show, then tell: every claim sits next to a real `duckgrep` command or SQL query and its real output.
- Sentence case for headings and buttons ("Why it matters", "How it works", "Install", "Results so far"). No exclamation marks, no emoji, no superlatives.
- "you" for the reader; "agents" for coding agents (Claude Code first — its one-line setup is `claude mcp add duckgrep -- duckgrep mcp`). Numbers are real benchmarks with the repo named, or left out.
- Name the comparison fairly: ripgrep and Serena are the reference points; state measured differences, never disparage.
- Credit DuckDB and tree-sitter plainly: "Built on DuckDB." Never imply endorsement by DuckDB Labs or the DuckDB Foundation.

## Colour

- Pages are `surface`; cards and nav are `surface-raised` edged with a `border` hairline. The system is flat: no shadows, no gradients.
- `duck-yellow` is a fill, never a text colour on light grounds. Spend it on the mark, the one primary button per view, and `match-bg`. Anything on yellow is `on-yellow`.
- Text is `ink`, secondary text `ink-muted`. Links are `link` (dark olive in light, yellow in dark), underlined on hover.
- `accent-orange` is a rare second hue: a "new" tag or one chart series. Never two orange things in one view.
- The match highlight is the brand in action: in query results and site search, wrap each hit in `match-bg` with `match-ink` and `radius-sm`.
- Every interactive element gets a 2px solid `focus` ring at 2px offset — blue, so it never reads as a match.

## Type

- Hanken Grotesk (Google Fonts, 400/600/700) stands in for DuckDB's Suisse Int'l; JetBrains Mono (400/700/800) is the same mono DuckDB uses. Load both from Google Fonts.
- Headlines: `display` once per page, then `h1`–`h3`. Running text `body`, ledes `body-lg`, metadata `small` in `ink-muted`.
- Everything a user could type or an agent could read — commands, paths, SQL, output — is mono: `code` in blocks, `code-sm` in dense result lists. Eyebrows and tags use `label` in capitals.

## Layout, spacing and shape

- Space on the 8px grid: `space-2` inside controls, `space-4` card padding, `space-5` between cards, `space-7` between desktop sections.
- Content column max 1120px; prose max 68ch.
- `radius-md` for buttons, inputs, cards and terminals; `radius-sm` for tags, inline code and highlights; `radius-pill` for pills. Shapes come from the mark: discs and pills, never chevrons or blobs.

## Terminal and code

- Terminal blocks are `surface-code` with `radius-md`, `space-3` padding and a 1px `border`. The prompt `$` is `code-comment`; the command is `ink`.
- duckgrep prints plain tables: a header row and dashed rule in `ink-muted`, then rows. Colour the path column `code-keyword`, line numbers `code-function`, the symbol that was asked about in `match-bg`/`match-ink`, and other columns `ink`.
- Show resolution tiers as words, never colour alone: confident tiers (`import`, `module`, `self`, …) in `ink`, `name` and `ambiguous` in `ink-muted`.
- SQL syntax uses the `code-*` tokens (derived from the DuckDB CLI's own highlighting).

## Iconography

- duckgrep has no icon set; prefer text labels. Where an icon is unavoidable use a stroked 24px set at 1.5–2px stroke in `ink` (flagged: no set chosen yet). No emoji anywhere.
- Arrows in links are the text glyph "→" in the current colour.
