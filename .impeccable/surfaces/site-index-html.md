---
version: 1
slug: "site-index-html"
primary_target: "site/index.html"
related_targets: ["site/site.css","site/site.js"]
---

# duckgrep.dev landing page

Scope: the single landing page at duckgrep.dev. Mode: Persuade.
Audience: developers on a coding agent, and engineering leads evaluating one. Job: decide to install. Proof: the measured A/B results (bench/RESULTS.md). Constraints: brand/README.md is binding; static HTML/CSS plus a small replay script; no invented claims. Spec: docs/specs/2026-10-02-website.md.

## Direction contract

THESIS: The page is a race, not a pitch: one real question, replayed side by side from recorded transcripts, Claude Code alone against Claude Code with duckgrep. It refuses the category default of a hero headline over a feature-card grid with a code screenshot.

OWN-WORLD: The brand's flat DuckDB vocabulary: surface ground, ink type, hairline borders, no shadows or gradients. Yellow is only the match highlight and the one primary button; the terminals are surface-code blocks in JetBrains Mono, with the counters as tabular mono numerals under each. Hanken Grotesk for every word a person reads.

STORY: The visitor sees the same question cost fewer calls, tokens and cents with duckgrep, learns that this run is typical of 494, sees the full table, then installs with three lines.

FIRST VIEWPORT: Lockup and GitHub link top. The tagline at display size on two lines, "answers questions" in match yellow, the subhead and the install command with a copy button beside it. Below, two equal terminals filling the content width ("Claude Code alone" left, "+ duckgrep" right), each with its counter strip (calls, tokens, cost) beneath. Below them, one caption line naming the question, the repo and that the run sits at the median saving.

FORM: The race, the second of three dealt surface structures (benchmark paper led the roll, then the race, then question index); chosen by the owner. Seed key af08de17. Signature interaction: the replay plays once in view, steps land at the run's own pacing, counters tick and freeze on the real totals, the slower side keeps working after the faster one stops; a Replay control and a Rust tab. Motion grammar: step reveals by clip and opacity, exponential ease-out, nothing loops.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Decision record

- Surface roll: `impeccable concept-seed --scope surface --mode persuade` printed key af08de17, dealt indices 5, 1, 7 (benchmark paper led under THE ROLL; the race; question index).
- The owner's answer on the decision page, 2026-10-02: `{"optionId":"the-race","steer":"","buildPath":"code"}`. Code-led: no image generation on this machine.
