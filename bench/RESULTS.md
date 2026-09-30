# Benchmark results

Run 2026-09-29 on a 2-core / 8 GB Linux VM, Python 3.11, DuckDB 1.5.6.
Repos: django @ 9332b163 (7,091 files), vscode @ 4b243603 (19,545 files), requests @ 611c616 (130 files).

## Index size

| repo | files | symbols | refs | commits | db size |
|---|---:|---:|---:|---:|---:|
| django | 7,087 | 51,864 | 849,470 | 2,000 | 99 MB |
| vscode | 19,544 | 364,778 | 6,545,413 | (no-git) | 549 MB |
| requests | 128 | 911 | 14,527 | 1,203 | 8 MB |

The full vscode (re)index took 144 s.

## Latency (`bench/latency.py`, medians)

| | django | vscode | requests |
|---|---:|---:|---:|
| no-op freshen | 138 ms | 242 ms | 42 ms |
| freshen after editing 1 file | 327 ms | 508 ms | 179 ms |
| + call-graph sync (only if the query needs edges) | 792 ms (63.6k refs) | 1,160 ms (22.3k refs) | 125 ms |
| `defs()` | 32 ms | 76 ms | 18 ms |
| `callers()`, confident | 37 ms | 209 ms | 29 ms |
| `grep()` literal | 89 ms | 367 ms | 40 ms |
| `callees()` | 40 ms | 99 ms | 19 ms |
| 2-hop transitive callers | 40 ms | 169 ms | 33 ms |
| unreferenced functions (whole repo) | 55 ms | 266 ms | 25 ms |
| churn × classes | 33 ms | 42 ms | 25 ms |

Symbols used: django `QuerySet.get_or_create` (edit `django/db/models/query.py`), vscode `createDecorator` (edit `src/vs/base/common/strings.ts`), requests `Session.request`.

## vs ripgrep (`bench/vs_grep.py`, django)

The grep side is simulated generously: every rg call is exact, and reading a hit's enclosing function counts as one call per distinct file.

| Question | grep: calls | grep: bytes | grep notes | duckgrep: calls | duckgrep: bytes | duckgrep result |
|---|---:|---:|---|---:|---:|---|
| Where is get_or_create defined? | 1 | 484 | 5 matching lines | 1 | 882 | 5 defs with signature + qualname |
| Who calls get_or_create, from which function? | 22 | 57,978 | 124 hits, 69 are call sites (55 docs/comments/defs); +21 file reads for the enclosing function | 1 | 9,143 | 73 call sites with caller |
| What does SQLCompiler.execute_sql call, and where are those defined? | 20 | 16,421 | read the function, then one rg per callee name (19) | 1 | 3,207 | 37 refs with targets |
| Everything that reaches SQLCompiler.execute_sql within 3 hops | 110 | 103,959 | iterative rg -w + reading each hit's function; 23 names explored | 1 | 113 | 4 functions via confident edges; 2,639 if name-only edges are followed too |
| Public functions in django/utils never referenced anywhere | 334 | — | one rg per function (334) | 1 | 64 | 1 candidate |

On a plain "where is X defined" question, grep is as good and cheaper. The win is on structural questions.

## Call-graph accuracy vs jedi (`bench/accuracy.py`, requests, 300 sampled calls)

jedi's goto-definition serves as the reference. 55 calls had an in-repo target; jedi resolved 245 outside the repo.

| duckgrep tier | share | contains jedi target | exactly it | avg candidates |
|---|---:|---:|---:|---:|
| self | 32.7% | 100% | 100% | 1.0 |
| import | 23.6% | 100% | 100% | 1.0 |
| local | 20.0% | 100% | 100% | 1.0 |
| name | 12.7% | 100% | 100% | 1.0 |
| module | 5.5% | 100% | 100% | 1.0 |
| qualified | 3.6% | 100% | 100% | 1.0 |
| unresolved | 1.8% | 0% | 0% | 0 |

Confident tiers covered 85.5% of in-repo calls with 100% precision. Of the calls jedi resolved outside the repo, duckgrep marked 86% `unresolved` and 14% `name`, meaning a false in-repo candidate. The sample is small; a django run is still to do.

## A/B evaluation: pilot findings (2026-09-30)

The question: does a Claude Code agent find code with fewer tool calls, tokens and round trips when it has duckgrep, without losing accuracy? And how does Serena compare? The design is in `docs/specs/2026-09-29-ab-eval-harness-design.md` and the harness in `bench/eval/`.

**Setup.**
- **Tasks:** 40.
  - 10 Python issue localizations from SWE-bench Lite.
  - 10 Rust localizations: 5 from SWE-bench Multilingual and 5 SWE-bench-Live issues from after the model's training cutoff.
  - 10 Python and 10 Rust structural questions about callers, two-hop callers and importers, with keys from jedi and rust-analyzer.
- **Setups:** three, each with Claude Code's Bash, Read, Grep and Glob. The baseline has nothing more; the others add duckgrep's `query` tool or Serena's navigation tools.
- **Runs:** two repetitions of each task and setup, 240 runs in all. They used Claude Code 2.1.285 with Sonnet 5.5 at medium effort, on the harness at main 407a30a. All 240 completed, at a total cost of $7.40.
- **Turns to locate:** recomputed from the saved transcripts with the rule in `bench/eval/locate.py` (`python -m bench.eval rescore`). A function now counts as located when a result shows its definition, not merely its name. Under the first rule, duckgrep's structural gains below were −0.25 and −0.20 rounds with intervals spanning zero, because its JSON-escaped rows hid the names.

**Findings.**
- **Structural questions: agents use duckgrep, and it saves round trips.** Agents called it in 75% of runs. Accuracy stayed level: 100% and 95% success, against the baseline's 100% and 90%. With duckgrep:
  - Round trips fell to ×0.83 [0.75, 0.91] on Python (better on 90% of tasks) and ×0.87 [0.77, 0.97] on Rust (75%).
  - Agents saw a gold location about half a round earlier: −0.45 [−0.75, −0.15] rounds on Python and −0.50 [−0.75, −0.25] on Rust.
  - Tool calls (−14% to −15%) and tokens (−10% to −13%) point the same way, but their intervals include no change.
- **Localization: neither tool was used.** Given an issue, the agent went straight to Grep and Read in all 80 duckgrep and Serena runs. Those tables therefore measure what it costs to carry the tool, which for duckgrep is nothing measurable (tokens ×1.01 and ×0.93).
- **Serena costs more than it gives here.** It was used in 2 of 120 runs. Its tool definitions and instructions add 44% to 53% tokens and 7% to 17% cost to every run, and it beat the baseline on at most 20% of tasks.
- **Accuracy doesn't separate the setups.** Every failed run was checked against the fix and the source, and there was no scoring bug or wrong key.
  - The one interval that excludes zero, Serena's −15 points on Python localization, comes from runs that never called Serena. They had the same tool traces as successful runs, with a different final answer.
  - 17 of the 34 failures come from keys stricter than the issue. pixi-6335 and rspack-14803 fail all 6 runs although every run named the core fix: their keys include edits the issue gives no reason for, such as visibility-only `pub(crate)` → `pub` hunks and an unrelated feature in pixi's PR. rust-analyzer-22751 and tokio-6603 lose runs that named an equivalent fix site.
  - The rest are agent misses: stopping at the symptom (sympy), misreading the issue (rust-analyzer), or passing over or misattributing a grep hit. duckgrep's enclosing-function column avoids that last error: on ripgrep's two-hop question the baseline went 0/2 and duckgrep 2/2.
- **No comparison can pass the correction.** With 10 tasks per table the smallest possible Wilcoxon p is 0.002, and Holm multiplies it by 80. The intervals and win rates are the evidence.

**For the full run.**
- **Add tasks, not repetitions.** Run-to-run variance is 24% to 39% of the total, so 2 repetitions are enough. From the pilot's paired spread, 80% power after correction needs:
  - 17 to 46 tasks per table for duckgrep's round trips and turns to locate on structural questions;
  - about 80 for tool calls;
  - 150 to 230 for tokens.

  50 structural questions per language is the useful size.
- **Localization needs the hinted setup** that the spec plans as the follow-up. More tasks under a neutral prompt only measure whether the agent discovers the tool, and the pilot shows it doesn't.
- **Before building the suite:**
  - derive keys without visibility-only hunks;
  - drop tasks whose fix bundles an unrelated feature;
  - say in the structural prompt whether a recursive function counts as its own caller (one run left it out on purpose).
- **Environment:**
  - Stable Rust here is 1.95, while lakehq/sail asks for 1.96 and rspack builds on nightly. rust-analyzer's build scripts can fail there, so Serena may miss macro-generated items.
  - During Serena runs, rust-analyzer rewrites bat's `Cargo.lock` and writes one for tokio. Every run restores its worktree afterwards.

<!-- eval:pilot -->
## A/B evaluation: pilot

240 runs, all completed normally. 7 left files in their worktree, all restored after the run: Cargo.lock (4 runs), Python bytecode (3 runs). Cost $7.40 at list rates (Claude Code billed $7.40). Claude Code 2.1.285; model claude-sonnet-5-5.

How to read the tables: each row pairs a setup with the baseline task by task, a task's repetitions averaged first. Tokens and cost are compared as ratios of geometric means; tool calls and round trips as ratios on log(1 + n), that is of 1 + n; turns to locate, located, success and F1 as differences, shares in percentage points. Intervals are 95% bootstrap intervals over tasks. p is a Wilcoxon signed-rank test and p (Holm) corrects it across all 80 comparisons in the report. With at most 10 tasks per comparison the smallest possible p is 0.0020, so no comparison can reach p < 0.05 after correction: read the intervals and win rates.

### Python localization

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 10 | 2.9 | duckgrep 2.7 | ×0.95 [0.84, 1.07] | 0.438 | 1.000 | 55% |
| tool calls | 10 | 2.9 | serena 2.8 | ×0.98 [0.86, 1.09] | 0.922 | 1.000 | 45% |
| round trips | 10 | 3.6 | duckgrep 3.4 | ×0.95 [0.86, 1.04] | 0.625 | 1.000 | 55% |
| round trips | 10 | 3.6 | serena 3.5 | ×0.98 [0.90, 1.05] | 0.438 | 1.000 | 50% |
| tokens | 10 | 30,776 | duckgrep 30,966 | ×1.01 [0.84, 1.17] | 0.557 | 1.000 | 30% |
| tokens | 10 | 30,776 | serena 45,622 | ×1.48 [1.32, 1.65] | 0.002 | 0.156 | 0% |
| cost ($) | 10 | 0.0239 | duckgrep 0.0238 | ×0.99 [0.85, 1.16] | 0.846 | 1.000 | 40% |
| cost ($) | 10 | 0.0239 | serena 0.0280 | ×1.17 [1.03, 1.36] | 0.049 | 1.000 | 20% |
| turns to locate | 10 | 2.6 | duckgrep 2.4 | -0.20 [-0.90, +0.25] | 1.000 | 1.000 | 45% |
| turns to locate | 10 | 2.6 | serena 2.3 | -0.30 [-1.00, +0.15] | 0.625 | 1.000 | 50% |
| located | 10 | 85% | duckgrep 85% | +0 pp [+0, +0] | 1.000 | 1.000 | 50% |
| located | 10 | 85% | serena 85% | +0 pp [+0, +0] | 1.000 | 1.000 | 50% |
| success | 10 | 95% | duckgrep 95% | +0 pp [+0, +0] | 1.000 | 1.000 | 50% |
| success | 10 | 95% | serena 80% | -15 pp [-30, -5] | 0.250 | 1.000 | 35% |
| F1 | 10 | 0.86 | duckgrep 0.88 | +0.03 [-0.03, +0.09] | 0.500 | 1.000 | 55% |
| F1 | 10 | 0.86 | serena 0.80 | -0.06 [-0.15, +0.02] | 0.250 | 1.000 | 40% |

### Rust localization

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 10 | 4.2 | duckgrep 3.6 | ×0.87 [0.77, 1.00] | 0.121 | 1.000 | 75% |
| tool calls | 10 | 4.2 | serena 4.1 | ×0.97 [0.84, 1.16] | 0.383 | 1.000 | 60% |
| round trips | 10 | 4.3 | duckgrep 3.9 | ×0.92 [0.81, 1.05] | 0.250 | 1.000 | 60% |
| round trips | 10 | 4.3 | serena 4.4 | ×1.02 [0.92, 1.14] | 0.844 | 1.000 | 55% |
| tokens | 10 | 42,511 | duckgrep 39,353 | ×0.93 [0.73, 1.15] | 0.770 | 1.000 | 40% |
| tokens | 10 | 42,511 | serena 61,218 | ×1.44 [1.17, 1.78] | 0.014 | 1.000 | 10% |
| cost ($) | 10 | 0.0367 | duckgrep 0.0317 | ×0.86 [0.75, 1.01] | 0.105 | 1.000 | 80% |
| cost ($) | 10 | 0.0367 | serena 0.0393 | ×1.07 [0.93, 1.24] | 0.232 | 1.000 | 20% |
| turns to locate | 10 | 2.7 | duckgrep 2.5 | -0.25 [-1.15, +0.50] | 0.812 | 1.000 | 55% |
| turns to locate | 10 | 2.7 | serena 2.9 | +0.20 [-0.50, +1.05] | 1.000 | 1.000 | 50% |
| located | 10 | 90% | duckgrep 85% | -5 pp [-30, +15] | 1.000 | 1.000 | 50% |
| located | 10 | 90% | serena 85% | -5 pp [-15, +0] | 1.000 | 1.000 | 45% |
| success | 10 | 65% | duckgrep 55% | -10 pp [-25, +0] | 0.500 | 1.000 | 40% |
| success | 10 | 65% | serena 65% | +0 pp [-15, +15] | 1.000 | 1.000 | 50% |
| F1 | 10 | 0.60 | duckgrep 0.60 | +0.00 [-0.08, +0.10] | 1.000 | 1.000 | 40% |
| F1 | 10 | 0.60 | serena 0.53 | -0.07 [-0.17, +0.04] | 0.281 | 1.000 | 25% |

### Python structural questions

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 10 | 3.4 | duckgrep 2.7 | ×0.85 [0.70, 1.01] | 0.195 | 1.000 | 60% |
| tool calls | 10 | 3.4 | serena 3.3 | ×1.00 [0.90, 1.09] | 0.578 | 1.000 | 45% |
| round trips | 10 | 3.8 | duckgrep 3.0 | ×0.83 [0.75, 0.91] | 0.008 | 0.602 | 90% |
| round trips | 10 | 3.8 | serena 3.6 | ×0.98 [0.91, 1.04] | 0.453 | 1.000 | 55% |
| tokens | 10 | 29,850 | duckgrep 26,084 | ×0.87 [0.70, 1.07] | 0.432 | 1.000 | 60% |
| tokens | 10 | 29,850 | serena 44,147 | ×1.48 [1.33, 1.63] | 0.002 | 0.156 | 0% |
| cost ($) | 10 | 0.0254 | duckgrep 0.0225 | ×0.88 [0.69, 1.13] | 0.432 | 1.000 | 70% |
| cost ($) | 10 | 0.0254 | serena 0.0292 | ×1.15 [1.04, 1.28] | 0.049 | 1.000 | 20% |
| turns to locate | 10 | 1.6 | duckgrep 1.1 | -0.45 [-0.75, -0.15] | 0.062 | 1.000 | 75% |
| turns to locate | 10 | 1.6 | serena 1.9 | +0.30 [+0.00, +0.75] | 0.500 | 1.000 | 40% |
| located | 10 | 100% | duckgrep 100% | +0 pp [+0, +0] | 1.000 | 1.000 | 50% |
| located | 10 | 100% | serena 95% | -5 pp [-15, +0] | 1.000 | 1.000 | 45% |
| success | 10 | 100% | duckgrep 100% | +0 pp [+0, +0] | 1.000 | 1.000 | 50% |
| success | 10 | 100% | serena 90% | -10 pp [-25, +0] | 0.500 | 1.000 | 40% |
| F1 | 10 | 1.00 | duckgrep 0.98 | -0.02 [-0.05, +0.00] | 0.500 | 1.000 | 40% |
| F1 | 10 | 1.00 | serena 0.94 | -0.06 [-0.14, +0.00] | 0.250 | 1.000 | 35% |

### Rust structural questions

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 10 | 2.9 | duckgrep 2.4 | ×0.86 [0.72, 1.01] | 0.125 | 1.000 | 75% |
| tool calls | 10 | 2.9 | serena 3.1 | ×1.04 [0.94, 1.16] | 0.562 | 1.000 | 45% |
| round trips | 10 | 3.6 | duckgrep 3.0 | ×0.87 [0.77, 0.97] | 0.062 | 1.000 | 75% |
| round trips | 10 | 3.6 | serena 3.6 | ×1.00 [0.92, 1.08] | 1.000 | 1.000 | 50% |
| tokens | 10 | 27,769 | duckgrep 25,037 | ×0.90 [0.72, 1.06] | 0.492 | 1.000 | 50% |
| tokens | 10 | 27,769 | serena 42,623 | ×1.53 [1.34, 1.75] | 0.002 | 0.156 | 0% |
| cost ($) | 10 | 0.0218 | duckgrep 0.0184 | ×0.84 [0.67, 1.04] | 0.232 | 1.000 | 60% |
| cost ($) | 10 | 0.0218 | serena 0.0254 | ×1.16 [1.05, 1.29] | 0.049 | 1.000 | 20% |
| turns to locate | 10 | 1.6 | duckgrep 1.1 | -0.50 [-0.75, -0.25] | 0.031 | 1.000 | 80% |
| turns to locate | 10 | 1.6 | serena 1.6 | -0.05 [-0.15, +0.00] | 1.000 | 1.000 | 55% |
| located | 10 | 100% | duckgrep 100% | +0 pp [+0, +0] | 1.000 | 1.000 | 50% |
| located | 10 | 100% | serena 100% | +0 pp [+0, +0] | 1.000 | 1.000 | 50% |
| success | 10 | 90% | duckgrep 95% | +5 pp [-15, +30] | 1.000 | 1.000 | 50% |
| success | 10 | 90% | serena 100% | +10 pp [+0, +30] | 1.000 | 1.000 | 55% |
| F1 | 10 | 0.98 | duckgrep 0.99 | +0.01 [-0.01, +0.03] | 0.500 | 1.000 | 55% |
| F1 | 10 | 0.98 | serena 0.99 | +0.01 [+0.00, +0.02] | 1.000 | 1.000 | 55% |

### Rust localization, post-cutoff issues

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 5 | 4.9 | duckgrep 4.1 | ×0.87 [0.68, 1.11] | 0.312 | 1.000 | 60% |
| tool calls | 5 | 4.9 | serena 5.2 | ×1.05 [0.85, 1.43] | 0.812 | 1.000 | 60% |
| round trips | 5 | 5.2 | duckgrep 4.2 | ×0.85 [0.70, 1.05] | 0.250 | 1.000 | 70% |
| round trips | 5 | 5.2 | serena 5.4 | ×1.03 [0.89, 1.25] | 1.000 | 1.000 | 60% |
| tokens | 5 | 57,333 | duckgrep 46,628 | ×0.81 [0.56, 1.21] | 0.438 | 1.000 | 60% |
| tokens | 5 | 57,333 | serena 79,450 | ×1.39 [0.96, 2.09] | 0.312 | 1.000 | 20% |
| cost ($) | 5 | 0.0454 | duckgrep 0.0388 | ×0.86 [0.65, 1.12] | 0.312 | 1.000 | 60% |
| cost ($) | 5 | 0.0454 | serena 0.0485 | ×1.07 [0.82, 1.43] | 0.812 | 1.000 | 40% |
| turns to locate | 5 | 3.1 | duckgrep 2.0 | -1.10 [-2.30, -0.20] | 0.250 | 1.000 | 80% |
| turns to locate | 5 | 3.1 | serena 3.5 | +0.40 [-0.90, +2.10] | 1.000 | 1.000 | 50% |
| located | 5 | 90% | duckgrep 100% | +10 pp [+0, +30] | 1.000 | 1.000 | 60% |
| located | 5 | 90% | serena 80% | -10 pp [-30, +0] | 1.000 | 1.000 | 40% |
| success | 5 | 40% | duckgrep 30% | -10 pp [-30, +0] | 1.000 | 1.000 | 40% |
| success | 5 | 40% | serena 30% | -10 pp [-30, +0] | 1.000 | 1.000 | 40% |
| F1 | 5 | 0.50 | duckgrep 0.54 | +0.04 [-0.11, +0.22] | 0.812 | 1.000 | 40% |
| F1 | 5 | 0.50 | serena 0.34 | -0.16 [-0.23, -0.08] | 0.062 | 1.000 | 0% |

### Adoption

| setup | kind | runs | used its tool | its share of calls | calls (used / not) |
|---|---|---:|---:|---:|---|
| duckgrep | localization | 40 | 0% | 0% | – / 3.4 |
| duckgrep | structural | 40 | 75% | 47% | 2.9 / 2.2 |
| serena | localization | 40 | 0% | 0% | – / 3.8 |
| serena | structural | 40 | 5% | 2% | 2.5 / 3.6 |

### By repo

| kind | repo | setup | runs | tool calls | cost ($) | success |
|---|---|---|---:|---:|---:|---:|
| localization | astropy/astropy | baseline | 2 | 3.0 | 0.021 | 100% |
| localization | astropy/astropy | duckgrep | 2 | 3.0 | 0.024 | 100% |
| localization | astropy/astropy | serena | 2 | 3.0 | 0.024 | 50% |
| localization | burntsushi/ripgrep | baseline | 2 | 4.5 | 0.040 | 100% |
| localization | burntsushi/ripgrep | duckgrep | 2 | 4.0 | 0.034 | 100% |
| localization | burntsushi/ripgrep | serena | 2 | 2.5 | 0.041 | 100% |
| localization | django/django | baseline | 2 | 2.0 | 0.022 | 100% |
| localization | django/django | duckgrep | 2 | 3.0 | 0.034 | 100% |
| localization | django/django | serena | 2 | 2.5 | 0.044 | 100% |
| localization | gleam-lang/gleam | baseline | 2 | 6.5 | 0.046 | 100% |
| localization | gleam-lang/gleam | duckgrep | 2 | 4.0 | 0.033 | 50% |
| localization | gleam-lang/gleam | serena | 2 | 5.5 | 0.040 | 50% |
| localization | lakehq/sail | baseline | 2 | 3.5 | 0.032 | 100% |
| localization | lakehq/sail | duckgrep | 2 | 2.0 | 0.022 | 100% |
| localization | lakehq/sail | serena | 2 | 4.0 | 0.042 | 100% |
| localization | matplotlib/matplotlib | baseline | 2 | 2.0 | 0.015 | 100% |
| localization | matplotlib/matplotlib | duckgrep | 2 | 1.0 | 0.010 | 100% |
| localization | matplotlib/matplotlib | serena | 2 | 1.0 | 0.012 | 100% |
| localization | mwaskom/seaborn | baseline | 2 | 5.5 | 0.050 | 100% |
| localization | mwaskom/seaborn | duckgrep | 2 | 5.5 | 0.043 | 100% |
| localization | mwaskom/seaborn | serena | 2 | 6.0 | 0.056 | 50% |
| localization | nushell/nushell | baseline | 2 | 4.0 | 0.031 | 100% |
| localization | nushell/nushell | duckgrep | 2 | 3.0 | 0.023 | 50% |
| localization | nushell/nushell | serena | 2 | 4.5 | 0.037 | 100% |
| localization | pallets/flask | baseline | 2 | 1.0 | 0.012 | 100% |
| localization | pallets/flask | duckgrep | 2 | 1.0 | 0.013 | 100% |
| localization | pallets/flask | serena | 2 | 1.0 | 0.013 | 100% |
| localization | prefix-dev/pixi | baseline | 2 | 5.0 | 0.042 | 0% |
| localization | prefix-dev/pixi | duckgrep | 2 | 6.0 | 0.052 | 0% |
| localization | prefix-dev/pixi | serena | 2 | 4.0 | 0.042 | 0% |
| localization | psf/requests | baseline | 2 | 3.0 | 0.023 | 100% |
| localization | psf/requests | duckgrep | 2 | 3.5 | 0.025 | 100% |
| localization | psf/requests | serena | 2 | 4.0 | 0.030 | 100% |
| localization | pylint-dev/pylint | baseline | 2 | 2.0 | 0.015 | 100% |
| localization | pylint-dev/pylint | duckgrep | 2 | 2.0 | 0.014 | 100% |
| localization | pylint-dev/pylint | serena | 2 | 2.5 | 0.019 | 100% |
| localization | rust-lang/rust-analyzer | baseline | 2 | 2.5 | 0.030 | 0% |
| localization | rust-lang/rust-analyzer | duckgrep | 2 | 3.5 | 0.038 | 0% |
| localization | rust-lang/rust-analyzer | serena | 2 | 5.5 | 0.052 | 0% |
| localization | scikit-learn/scikit-learn | baseline | 2 | 2.0 | 0.020 | 100% |
| localization | scikit-learn/scikit-learn | duckgrep | 2 | 2.0 | 0.021 | 100% |
| localization | scikit-learn/scikit-learn | serena | 2 | 2.0 | 0.019 | 100% |
| localization | sharkdp/bat | baseline | 2 | 4.0 | 0.034 | 100% |
| localization | sharkdp/bat | duckgrep | 2 | 3.0 | 0.031 | 100% |
| localization | sharkdp/bat | serena | 2 | 4.0 | 0.037 | 100% |
| localization | sphinx-doc/sphinx | baseline | 2 | 3.0 | 0.021 | 100% |
| localization | sphinx-doc/sphinx | duckgrep | 2 | 2.5 | 0.024 | 100% |
| localization | sphinx-doc/sphinx | serena | 2 | 2.0 | 0.023 | 100% |
| localization | sympy/sympy | baseline | 2 | 10.0 | 0.107 | 50% |
| localization | sympy/sympy | duckgrep | 2 | 6.5 | 0.068 | 50% |
| localization | sympy/sympy | serena | 2 | 8.5 | 0.124 | 0% |
| localization | tokio-rs/tokio | baseline | 2 | 3.0 | 0.028 | 50% |
| localization | tokio-rs/tokio | duckgrep | 2 | 3.0 | 0.027 | 50% |
| localization | tokio-rs/tokio | serena | 2 | 3.0 | 0.030 | 100% |
| localization | uutils/coreutils | baseline | 2 | 3.0 | 0.019 | 100% |
| localization | uutils/coreutils | duckgrep | 2 | 2.5 | 0.018 | 100% |
| localization | uutils/coreutils | serena | 2 | 2.5 | 0.020 | 100% |
| localization | web-infra-dev/rspack | baseline | 2 | 9.0 | 0.104 | 0% |
| localization | web-infra-dev/rspack | duckgrep | 2 | 6.5 | 0.062 | 0% |
| localization | web-infra-dev/rspack | serena | 2 | 7.5 | 0.073 | 0% |
| structural | BurntSushi/ripgrep | baseline | 10 | 4.0 | 0.032 | 80% |
| structural | BurntSushi/ripgrep | duckgrep | 10 | 2.8 | 0.026 | 90% |
| structural | BurntSushi/ripgrep | serena | 10 | 3.6 | 0.033 | 100% |
| structural | psf/requests | baseline | 10 | 3.2 | 0.022 | 100% |
| structural | psf/requests | duckgrep | 10 | 3.3 | 0.029 | 100% |
| structural | psf/requests | serena | 10 | 2.9 | 0.027 | 100% |
| structural | pytest-dev/pytest | baseline | 10 | 4.1 | 0.031 | 100% |
| structural | pytest-dev/pytest | duckgrep | 10 | 2.4 | 0.021 | 100% |
| structural | pytest-dev/pytest | serena | 10 | 4.6 | 0.037 | 80% |
| structural | sharkdp/fd | baseline | 10 | 2.6 | 0.020 | 100% |
| structural | sharkdp/fd | duckgrep | 10 | 2.4 | 0.017 | 100% |
| structural | sharkdp/fd | serena | 10 | 3.1 | 0.023 | 100% |

### Variance

| setup | metric | within-task SD | between-task SD | within share of variance |
|---|---|---:|---:|---:|
| baseline | log tokens | 0.37 | 0.51 | 34% |
| baseline | log(1 + tool calls) | 0.28 | 0.39 | 34% |
| duckgrep | log tokens | 0.24 | 0.43 | 24% |
| duckgrep | log(1 + tool calls) | 0.24 | 0.34 | 34% |
| serena | log tokens | 0.36 | 0.44 | 39% |
| serena | log(1 + tool calls) | 0.28 | 0.39 | 35% |

### Setup costs (not included above)

| setup | worktrees | total seconds | total MB |
|---|---:|---:|---:|
| duckgrep | 24 | 82 | 626 |
| serena | 24 | 356 | – |
<!-- /eval:pilot -->
