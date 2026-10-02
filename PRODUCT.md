# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

static HTML/CSS: hand-written pages that use `brand/tokens.css` and `brand/components.css` directly, with no build step. Hosted on Cloudflare Pages from this repo (the domain is duckgrep.dev).

## Users

Two audiences, both deciding whether to install:
- **Developers who already use a coding agent** (Claude Code first, other MCP clients second). They arrive from a link or the GitHub repo and judge in about a minute whether duckgrep is worth adding.
- **Engineering leads evaluating agent tooling for a team,** who weigh what it saves in tool calls, tokens and cost.

For both, the measured results and the cost savings are the reason to install. They carry the most weight.

## Product Purpose

duckgrep gives a coding agent a live, queryable model of a codebase (definitions, callers, imports, history), so structural questions take one query instead of a search loop. It indexes a repo with tree-sitter into a single DuckDB file, and exposes it as one read-only `query(sql)` MCP tool.

Success is measured, not asserted: the agent must spend fewer tool calls, round trips, tokens and dollars than plain grep on real tasks, at equal accuracy.

## Positioning

Grep finds strings. duckgrep answers questions.
- **Not a fixed menu:** code-navigation tools and language servers offer one (find definition, find references). duckgrep gives agents a query language over the parse, the call graph and git history, so one call can answer questions no tool author anticipated.
- **A fair comparison, measured:** in the same benchmark, Serena added tokens and cost, while duckgrep cut them.

## Operating Context

- **Install:** `uv tool install duckgrep`, from a public repo and PyPI. Both are planned for launch, and the owner takes those steps.
- **Setup in Claude Code:** `claude mcp add duckgrep -- duckgrep mcp`. One line in the user's `CLAUDE.md` tells the agent to use it, and the measured numbers come with that line.
- **Running:** the index lives in `<repo>/.duckgrep/index.duckdb`, and refreshes before every query.

## Capabilities and Constraints

- **Languages:** Python, TypeScript/TSX, JavaScript, Go and Rust. Other text files are indexed for `grep()`.
- **Resolution:** syntax-based, not type-checked. Every call-graph edge carries a resolution tier and says when it is guessing.
- **Where it doesn't help:**
  - finding where to fix a bug from an issue report: no measured saving;
  - "where is this string": grep is as good.
- **Site scope at launch:** one landing page. The docs stay in the GitHub README.

## Brand Commitments

`brand/README.md` is binding:
- **The tagline:** "Grep finds strings. duckgrep answers questions.", verbatim, on two lines.
- **The subhead,** the four reasons in order, and the proof points.
- **Voice:** sentence case, no superlatives, exclamation marks or emoji.
- **Numbers:** real, with the repo named, linked to `bench/RESULTS.md`, and the simulated multiples never presented as an agent's saving.
- **Logos and assets:** the duck-d and lockups, the tokens, the components, the favicons and the social cards.

## Evidence on Hand

- **The agent A/B run** (`bench/RESULTS.md`; 3,620 Claude Code runs on 724 tasks, 2026-10-02). On 494 structural questions with duckgrep:
  - Python: tool calls −18%, round trips −19%, tokens −18%, cost −24%;
  - Rust: −16%, −12%, −12% and −25%;
  - accuracy: 96% for duckgrep, against 94% and 96% without it;
  - Serena: +23–56% tokens.
- **The django benchmark** (simulated generously for grep): 22 calls and 58 KB against 1 call and 9 KB. For "everything within 3 hops", 110 calls and 104 KB against 1 call and 113 bytes.
- **Latency:** 30–90 ms queries on django.
- **Call-graph accuracy against jedi** (freqtrade, django, requests).
- **Absent, and never to be fabricated:** users, testimonials, logos of companies using it, download counts, or pricing.

## Product Principles

1. Lead with measured savings; every number links to its source and keeps its scope.
2. Show, then tell: a claim sits next to the real query and its real output.
3. Say plainly where it doesn't help; honesty is part of the pitch.
4. One install path, one setup line, one instruction line: get the visitor to a working agent in a minute.

## Accessibility & Inclusion

No product-specific requirement established; WCAG AA contrast is already met by the brand tokens' text pairs.
