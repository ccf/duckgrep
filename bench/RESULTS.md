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

Confident tiers covered 85.5% of in-repo calls with 100% precision. Of the calls jedi resolved outside the repo, duckgrep marked 86% `unresolved` and 14% `name`, meaning a false in-repo candidate.

### freqtrade and django (2026-10-03, 3,000 sampled calls each)

Run with duckgrep at main 2e48a98 on shallow clones: freqtrade f2ec745 and django 0ae93a0, both of 2026-10-02. jedi now runs without its bundled django-stubs. With them, jedi answered django's own calls from the stubs, so they counted as external. An earlier run with the stubs (duckgrep dc9814b) reported 1,411 in-repo calls on django, confident coverage of 73.4% and precision of 99.8%.

| duckgrep tier | freqtrade share | contains jedi target | exactly it | django share | contains jedi target | exactly it |
|---|---:|---:|---:|---:|---:|---:|
| import | 42.8% | 100% | 100% | 33.5% | 100% | 100% |
| module | 0.3% | 100% | 100% | 13.4% | 100% | 100% |
| self | 8.3% | 100% | 100% | 9.7% | 100% | 100% |
| local | 12.7% | 100% | 100% | 8.6% | 100% | 100% |
| qualified | 7.4% | 100% | 100% | 0.5% | 100% | 100% |
| name | 27.4% | 100% | 73.5% (1.7 candidates) | 22.6% | 100% | 69.1% (1.8 candidates) |
| ambiguous | 0.9% | 0% | 0% | 9.9% | 0% | 0% |
| unresolved | 0.2% | 0% | 0% | 1.9% | 0% | 0% |

- **freqtrade:** 1,623 of the 3,000 calls had an in-repo target. Confident tiers covered 71.4% of them, at 100% precision.
- **django:** 1,532 had an in-repo target. Confident tiers covered 65.7%, at 100% precision.
  - The calls that turned out to be in-repo once the stubs were off mostly fell to `name`, so coverage is lower than the earlier 73.4%.
- **Calls jedi resolved outside the repo:** duckgrep marked 98% (freqtrade) and 89% (django) of them `unresolved`. The rest were `ambiguous`, or `name` with a false in-repo candidate (0% and 2%).

### Typed receivers (2026-10-03)

The `typed` tier infers a Python receiver's class from syntax, resolves it through imports, and walks in-repo base classes (spec `docs/specs/2026-10-02-local-type-inference.md`). It was measured against jedi on the same checkouts as above: django 0ae93a0 and freqtrade f2ec745 with 3,000 sampled calls each, and requests 611c616 with 300. `main` is 60ddb8a (stubs off, stdlib fix in). The typed tier is the `feat/typed-tier` branch, after the final review's fixes.

| | django, main | django, typed | freqtrade, main | freqtrade, typed | requests, main | requests, typed |
|---|---:|---:|---:|---:|---:|---:|
| calls with an in-repo target | 1,555 | 1,569 | 1,631 | 1,647 | 148 | 161 |
| confident coverage | 66.8% | **87.8%** | 74.3% | **90.9%** | 67.6% | **90.1%** |
| confident precision | 100% | 99.9% | 100% | 100% | 100% | 100% |
| `typed` share (exactly jedi's target) | – | 21.6% (100%) | – | 18.0% (100%) | – | 16.8% (100%) |
| `name` share | 19.9% | 4.2% | 24.6% | 8.3% | 23.0% | 3.7% |
| `ambiguous` share | 11.7% | 6.4% | 1.0% | 0.4% | 5.4% | 2.5% |
| jedi-external calls given a false `name` candidate | 1% | 0% | 0% | 1% | 6% | 0% |

- **Coverage:** confident coverage rose 21.0 points on django, 16.6 on freqtrade and 22.5 on requests. Every `typed` edge in the samples was exactly jedi's target. django's 99.9% comes from a few existing `import` and `local` edges listing an extra candidate. Requests' 300-call sample is small, and its share varies from run to run.
- **Refusals:** where syntax can't settle it, the tier makes no edge:
  - a diamond with several definers;
  - an external or unnameable base listed before the in-repo definer;
  - a name bound on more than one line of its file;
  - a name a nested def or class also defines.

  These cost a point or two of coverage, and no precision.
- **External objects:** calls on receivers whose class is outside the repo are now `unresolved` instead of `name` guesses.
- **The samples:** `bench/accuracy.py`'s sample isn't fixed from run to run (it shuffles an unordered result), so the in-repo counts vary by a few percent between columns. The 20-point gap is far larger than that noise.
- **Latency on django** (`bench/latency.py`, `django/db/models/query.py`):
  - A full index takes 9–11 s on both.
  - Typical queries are unchanged.
  - Edge sync after a one-file edit recomputes 70,673 refs (63,609 on `main`) and takes 430–470 ms at a load average of about 7, against 336 ms for `main`.
  - Other builds were running on the machine (load average 7–38), so absolute times vary between runs. For the same 40,000 refs, the edge SQL takes about 185 ms against 150 ms on `main`.
- **Fixes made along the way:**
  - The method lookup is staged: the planner had joined every class's ancestry to every method first.
  - The dirty marking takes only re-exported imports, where it had marked the methods of 101 classes imported by `query.py`.
  - Edge sync reads dirty refs through `refs` by rowid, in batches, and falls back to a batched rebuild if it runs out of memory. A one-line edit to `django/db/models/fields/__init__.py` had exhausted the default 2 GB and left the index stuck; it now syncs in about a second.

## A/B evaluation: full run, repetition 1 (2026-10-02)

The pilot's question, asked at scale: does a Claude Code agent find code with fewer tool calls, tokens and round trips when it has duckgrep, without losing accuracy? The design is in `docs/specs/2026-09-30-ab-eval-full-run.md`. The tables are in the next section but one, "A/B evaluation: full".

**Setup.**
- **Tasks: 724.**
  - 162 Python localizations from SWE-bench Lite.
  - 68 Rust localizations: 15 from SWE-bench Multilingual, and 53 SWE-bench-Live issues, 21 of them after the model's training cutoff.
  - 237 Python and 257 Rust structural questions (callers, two-hop callers, importers) on 25 repos pinned at release commits, with keys from jedi and rust-analyzer.
- **Setups: five.** Each has Claude Code's Bash, Read, Grep and Glob.
  - The baseline has nothing more. `duckgrep` adds duckgrep's `query` tool, and `serena` adds Serena's navigation tools.
  - `duckgrep-hint` and `serena-hint` are those two, plus a sentence in the system prompt saying to use the tool before Grep, Glob or Read.
- **Runs:** one repetition of each task and setup, 3,620 runs in all, made on 2026-10-02.
  - Everything was pinned: harness c8264ef, Claude Code 2.1.287, Sonnet 5.5 at medium effort.
  - All 3,620 were recorded, for $124.70. One ended at the turn or budget cap.
  - The first invocation was stopped after 1,140 runs and resumed. The run it cut short was charged ($0.03) and redone, so the batch spent $124.73 in all.
  - The records were later rescored with the locate rule for queries pinned to one file. Only turns to locate changed, in 4 records.
- **Statistics:** each comparison pairs a setup with the baseline task by task. "Significant" below means it survives the Holm correction across all 160 comparisons.

**Findings.**
- **Structural questions: duckgrep is a clear win.** Every efficiency measure improves significantly, on both languages, with and without the hint, except Rust tokens without the hint (×0.96). Accuracy is unchanged: success is 93% to 97% in every setup, against the baseline's 94% and 96%.

  | vs baseline | Python, duckgrep | Python, duckgrep-hint | Rust, duckgrep | Rust, duckgrep-hint |
  |---|---|---|---|---|
  | tool calls | ×0.81 | ×0.82 | ×0.88 | ×0.84 |
  | round trips | ×0.83 | ×0.81 | ×0.92 | ×0.88 |
  | tokens | ×0.85 | ×0.82 | ×0.96 (not significant) | ×0.88 |
  | cost | ×0.82 | ×0.76 | ×0.86 | ×0.75 |
  | turns to locate | −0.45 | −0.56 | −0.46 | −0.61 |

  - Agents used duckgrep in 75% of structural runs unprompted, and in 99% with the hint.
  - As in the pilot, the saved round comes from duckgrep's rows naming the function each call sits in, which a grep hit doesn't.
- **Serena doesn't pay for itself.**
  - Unhinted, agents called it in 11% of structural runs. Its tool definitions add 47% to 58% tokens and 10% to 16% cost.
  - Hinted, it cut Python round trips (×0.84, significant). But it still added tokens (×1.23 to ×1.56) and, on Rust, cost (×1.12).
- **Localization: the hint works, but there is little to save.** Under the neutral prompt neither tool was used, as in the pilot (0 of 230 runs each). With the hint, agents used duckgrep in 73% of localization runs and Serena in 57%. The savings didn't follow, for three reasons:
  - **The tasks are short.** A SWE-bench issue usually names an error message, a function or a test, so one grep lands on the code.
    - The Python baseline needed a median of 2 tool calls, and identified the answer within 2 rounds in 85% of tasks. There's no structural chain for duckgrep to collapse.
    - Rust issues are longer: a median of 5 calls, and 45% identified within 2 rounds.
  - **Carrying the tool has a cost.** duckgrep's tool definition, with its schema, adds tokens to every request.
    - In the 162 Python runs where the unhinted agent never called duckgrep, a run used a median of 2,500 more tokens than the baseline. That's the +11% to +13% in the tokens rows, which is significant.
    - Those tokens are cached, so cost doesn't move (×1.00 to ×1.01).
  - **A query often adds a step instead of replacing one.**
    - On Python, hinted runs that used duckgrep made 3.0 calls against the baseline's 2.6 on the same tasks.
    - Most first queries were a `grep('…')` or a scan of `symbols`, the same search the agent would have done with Grep. Only 31 of 109 (28%) were `defs()`.
    - These within-setup splits are self-selected (an agent may skip the tool on easy tasks), so they explain the tables rather than estimate an effect.
- **Rust localization leans duckgrep's way, but one repetition can't tell.**
  - Cost ×0.89 [0.81, 0.97] and tool calls ×0.91 [0.83, 1.00] for the unhinted setup. Raw p is 0.025 and 0.059, neither significant after correction.
  - On the 21 post-cutoff issues, duckgrep-hint succeeded on 52% against 38% (+14 points [0, +29]). That's too few tasks to rest on.
- **Accuracy on localization is level.** Success differs from the baseline by +0 to −4 points, none significant.
  - The "located" rows dip with the hint (Python −4 points with duckgrep-hint, −5 with serena-hint). That's mostly agents answering without looking at the definition.
  - Turns to locate counts a function only when a result shows its definition line or names it in its file. Hinted agents more often answered from a search hit inside the function, naming the right function without ever displaying its `def` line.
  - So 11 of the 22 Python duckgrep-hint runs counted as "not located" still gave the right answer, and 13 of 23 for serena-hint, against 6 of 15 for the baseline. Runs that really didn't find the code are 11, 10 and 9.
  - A first version of this section blamed duckgrep rows filtered by file in the SQL, which the locate rule didn't credit. The rule now credits a query that pins one file (`locate.pinned_file`, read from DuckDB's own parse of the SQL). That changed 4 of the 3,620 records.
  - Success is the measure to read here.

**What this means.**
- duckgrep earns its place on what it was built for: questions about how code connects. There it cuts an agent's tool calls, round trips, tokens and cost by 8% to 25%, at no loss of accuracy, measured on 494 questions.
- It doesn't help an agent find where to fix a typical issue, where one grep already finds the spot. There it costs about 10% more tokens just by being present, while its cost per run is flat.

**Open.**
- **A second repetition** (about $125) would tighten the Rust localization intervals. The structural results don't need it.
- **The failure audit:** the spec's audit of failed runs on a sample hasn't been done yet.
- **Tool-definition tokens:** a smaller schema in the description, or one loaded on demand, would cut what every request pays.

### Replayed runs

The website replays two pairs of runs from repetition 1, Claude Code alone (`baseline`) against `duckgrep-hint`. They were chosen as typical, not as the best case:
- Across the 453 structural questions both setups answered correctly, the median cost ratio (duckgrep-hint ÷ baseline) is 0.762.
- `flask-callers-show_server_banner`: 0.773, rank 232 of 453. 3 calls, 26,661 tokens and $0.0182 alone; 2 calls, 22,740 tokens and $0.0141 with duckgrep.
- `ripgrep-two-hop-eprint_nothing_searched`: 0.781, rank 238 of 453. 3 calls, 27,623 tokens and $0.0204 alone; 2 calls, 23,049 tokens and $0.0159 with duckgrep.

`site/tools/extract_replay.py` renders them from the saved transcripts.

## A/B evaluation: pilot findings (2026-09-30)

The question: does a Claude Code agent find code with fewer tool calls, tokens and round trips when it has duckgrep, without losing accuracy? And how does Serena compare? The design is in `docs/specs/2026-09-29-ab-eval-harness-design.md` and the harness in `bench/eval/`.

**Setup.**
- **Tasks:** 40.
  - 10 Python issue localizations from SWE-bench Lite.
  - 10 Rust localizations: 5 from SWE-bench Multilingual and 5 SWE-bench-Live issues from after the model's training cutoff.
  - 10 Python and 10 Rust structural questions about callers, two-hop callers and importers, with keys from jedi and rust-analyzer.
- **Setups:** three, each with Claude Code's Bash, Read, Grep and Glob. The baseline has nothing more; the others add duckgrep's `query` tool or Serena's navigation tools.
- **Runs:** two repetitions of each task and setup, 240 runs in all, made on 2026-09-30 with Claude Code 2.1.285 and Sonnet 5.5 at medium effort. All 240 completed, at a total cost of $7.40.
- **Harness version:** main 407a30a, taken from the operator's notes, because the pilot predates the per-run record of the harness commit. The records were then rescored with this branch's code, and only turns to locate changed, in 31 records.
- **Turns to locate:** a function counts as located when a result identifies it, by either of two routes:
  - its definition line in its file;
  - its qualified name in its file, from a structured tool. A duckgrep row names the function that encloses a call, whereas a grep hit on the call gives only its file and line.

  A call, a docstring or a same-named token does not count. The first implementation counted them, and it also missed duckgrep's JSON-escaped rows; see `bench/eval/locate.py` and `python -m bench.eval rescore`.

**Findings.**
- **Structural questions: agents use duckgrep, and it saves round trips.** Agents called it in 75% of runs. Accuracy stayed level: 100% and 95% success, against the baseline's 100% and 90%. With duckgrep:
  - Python round trips fell from typically 3.8 to 3.0: ×0.83 [0.75, 0.91] on log(1 + n), better on 8 of 10 tasks and worse on none.
  - Rust round trips fell from typically 3.6 to 3.0: ×0.87 [0.77, 0.97], better on 5 tasks and tied on 5.
  - Tool calls (typically 2.7 against 3.4, and 2.4 against 2.9) and tokens (×0.87 and ×0.90) point the same way. But their intervals include no change, and differences of that size also appear where duckgrep went unused (see below).
- **What the saved round is.** In round 1, every setup showed a line of a gold caller (its file and line) in 32 of 40 runs. duckgrep's rows also name the function each call sits in, which a grep hit doesn't. So agents with duckgrep identified the gold callers about half a round sooner: turns to locate −0.45 [−0.75, −0.15] on Python and −0.50 [−0.75, −0.25] on Rust. This is the round-trip saving seen from another side, not separate evidence.
- **Localization: neither tool was used.** Given an issue, the agent used only the built-in tools (Grep, Read and Bash) in all 80 duckgrep and Serena runs, so those tables measure noise and the cost of carrying a tool.
  - The noise is large. With duckgrep unused, Rust localization still shows tool calls at ×0.87 [0.77, 1.00], and the post-cutoff subset shows turns to locate at −1.10 [−2.30, −0.20].
  - duckgrep's tool definition adds about 900 tokens to each request, some 3,000 per run, or about 10% of a Python localization run. That's too little for these tables to resolve: tokens came out at ×1.01 and ×0.93.
- **Serena costs more than it gives here.** Its tools were called in 2 of its 80 runs. Its tool definitions and instructions add about 4,300 tokens to each request. On a typical run that's 44% to 53% more tokens and 7% to 17% more cost, and Serena was cheaper than the baseline on at most a fifth of tasks.
- **Accuracy doesn't separate the setups.** Every failed run was checked against the fix and the source, and there was no scoring bug or wrong key.
  - The only accuracy intervals that exclude zero are Serena's on localization: −15 points of success on Python, and −0.16 F1 on the post-cutoff Rust issues. Neither comes from Serena, which those runs never called. Two of the Python misses (astropy, seaborn) have the same tool traces as successful runs, with a different final answer. The other two are the sympy miss the baseline also made.
  - 17 of the 34 failures come from keys stricter than the issue. pixi-6335 and rspack-14803 fail all 6 runs although every run named the core fix: their keys include edits the issue gives no reason for, such as visibility-only `pub(crate)` → `pub` hunks and an unrelated feature in pixi's PR. rust-analyzer-22751 and tokio-6603 lose runs that named an equivalent fix site.
  - The rest are agent misses: stopping at the symptom (sympy), misreading the issue (rust-analyzer), or passing over or misattributing a grep hit.
- **No comparison can pass the correction.** With 10 tasks per table the smallest possible Wilcoxon p is 0.002, and Holm multiplies it by 80. The intervals and win rates are the evidence, read against the noise above.

**For the full run.**
- **Add tasks, not repetitions.** Run-to-run variance is 24% to 39% of the total, so 2 repetitions are enough. From the pilot's paired spread (80% power after Holm correction over 80 tests; t-approximation with ×1.15 for the Wilcoxon test), detecting duckgrep's effects on structural questions needs:
  - its round-trip saving: about 22 tasks per table on Python and 51 on Rust;
  - tool calls: about 88;
  - tokens: 151 to 235.

  About 50 structural questions per language is the useful size.
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
| turns to locate | 10 | 1.6 | serena 1.8 | +0.20 [+0.00, +0.50] | 0.500 | 1.000 | 40% |
| located | 10 | 100% | duckgrep 100% | +0 pp [+0, +0] | 1.000 | 1.000 | 50% |
| located | 10 | 100% | serena 100% | +0 pp [+0, +0] | 1.000 | 1.000 | 50% |
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

<!-- eval:full -->
## A/B evaluation: full

3620 runs: 1 ended in an error (turn or budget cap). 211 left files in their worktree, all restored after the run: Python bytecode (159 runs), Cargo.lock (52 runs). Cost $124.70 at list rates (Claude Code billed $124.70). Claude Code 2.1.287; model claude-sonnet-5-5. 1 attempt that was not recorded spent $0.03 more, $124.73 in all.

How to read the tables: each row pairs a setup with the baseline task by task, a task's repetitions averaged first. Tokens and cost are compared as ratios of geometric means; tool calls and round trips as ratios on log(1 + n), that is of 1 + n; turns to locate, located, success and F1 as differences, shares in percentage points. Intervals are 95% bootstrap intervals over tasks. p is a Wilcoxon signed-rank test and p (Holm) corrects it across all 160 comparisons in the report.

### Python localization

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 162 | 2.4 | duckgrep 2.4 | ×0.99 [0.94, 1.04] | 0.599 | 1.000 | 52% |
| tool calls | 162 | 2.4 | duckgrep-hint 2.4 | ×1.00 [0.95, 1.06] | 0.817 | 1.000 | 51% |
| tool calls | 162 | 2.4 | serena 2.4 | ×0.99 [0.94, 1.04] | 0.945 | 1.000 | 50% |
| tool calls | 162 | 2.4 | serena-hint 2.3 | ×0.96 [0.91, 1.01] | 0.118 | 1.000 | 55% |
| round trips | 162 | 3.2 | duckgrep 3.2 | ×1.00 [0.96, 1.04] | 0.997 | 1.000 | 52% |
| round trips | 162 | 3.2 | duckgrep-hint 3.2 | ×1.00 [0.96, 1.04] | 0.688 | 1.000 | 51% |
| round trips | 162 | 3.2 | serena 3.1 | ×1.00 [0.96, 1.03] | 0.962 | 1.000 | 49% |
| round trips | 162 | 3.2 | serena-hint 2.9 | ×0.95 [0.91, 0.98] | 0.002 | 0.253 | 57% |
| tokens | 162 | 25,225 | duckgrep 28,110 | ×1.11 [1.05, 1.19] | 0.000 | 0.001 | 20% |
| tokens | 162 | 25,225 | duckgrep-hint 28,545 | ×1.13 [1.06, 1.21] | 0.000 | 0.003 | 26% |
| tokens | 162 | 25,225 | serena 38,326 | ×1.52 [1.44, 1.60] | 0.000 | 0.000 | 6% |
| tokens | 162 | 25,225 | serena-hint 36,271 | ×1.44 [1.36, 1.52] | 0.000 | 0.000 | 13% |
| cost ($) | 162 | 0.0267 | duckgrep 0.0270 | ×1.01 [0.97, 1.06] | 0.614 | 1.000 | 46% |
| cost ($) | 162 | 0.0267 | duckgrep-hint 0.0268 | ×1.00 [0.95, 1.06] | 0.589 | 1.000 | 48% |
| cost ($) | 162 | 0.0267 | serena 0.0294 | ×1.10 [1.05, 1.15] | 0.000 | 0.000 | 27% |
| cost ($) | 162 | 0.0267 | serena-hint 0.0288 | ×1.08 [1.03, 1.13] | 0.000 | 0.029 | 35% |
| turns to locate | 162 | 1.7 | duckgrep 1.9 | +0.23 [+0.02, +0.48] | 0.018 | 1.000 | 45% |
| turns to locate | 162 | 1.7 | duckgrep-hint 1.9 | +0.20 [-0.01, +0.44] | 0.127 | 1.000 | 48% |
| turns to locate | 162 | 1.7 | serena 1.9 | +0.16 [-0.02, +0.35] | 0.021 | 1.000 | 46% |
| turns to locate | 162 | 1.7 | serena-hint 1.8 | +0.12 [-0.02, +0.27] | 0.085 | 1.000 | 47% |
| located | 162 | 91% | duckgrep 88% | -3 pp [-8, +1] | 0.197 | 1.000 | 48% |
| located | 162 | 91% | duckgrep-hint 86% | -4 pp [-9, +1] | 0.090 | 1.000 | 48% |
| located | 162 | 91% | serena 88% | -2 pp [-7, +2] | 0.248 | 1.000 | 49% |
| located | 162 | 91% | serena-hint 86% | -5 pp [-9, -1] | 0.021 | 1.000 | 48% |
| success | 162 | 85% | duckgrep 85% | +0 pp [-4, +4] | 1.000 | 1.000 | 50% |
| success | 162 | 85% | duckgrep-hint 83% | -2 pp [-6, +2] | 0.366 | 1.000 | 49% |
| success | 162 | 85% | serena 85% | +0 pp [-4, +4] | 1.000 | 1.000 | 50% |
| success | 162 | 85% | serena-hint 83% | -2 pp [-6, +2] | 0.317 | 1.000 | 49% |
| F1 | 162 | 0.83 | duckgrep 0.82 | -0.01 [-0.05, +0.03] | 0.584 | 1.000 | 50% |
| F1 | 162 | 0.83 | duckgrep-hint 0.80 | -0.04 [-0.07, +0.00] | 0.063 | 1.000 | 48% |
| F1 | 162 | 0.83 | serena 0.83 | -0.01 [-0.05, +0.03] | 0.567 | 1.000 | 48% |
| F1 | 162 | 0.83 | serena-hint 0.82 | -0.01 [-0.05, +0.02] | 0.373 | 1.000 | 48% |

### Rust localization

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 68 | 4.8 | duckgrep 4.3 | ×0.91 [0.83, 1.00] | 0.059 | 1.000 | 62% |
| tool calls | 68 | 4.8 | duckgrep-hint 4.5 | ×0.94 [0.85, 1.04] | 0.377 | 1.000 | 54% |
| tool calls | 68 | 4.8 | serena 4.7 | ×0.98 [0.92, 1.04] | 0.513 | 1.000 | 51% |
| tool calls | 68 | 4.8 | serena-hint 4.3 | ×0.92 [0.84, 1.00] | 0.059 | 1.000 | 57% |
| round trips | 68 | 4.9 | duckgrep 4.6 | ×0.96 [0.89, 1.02] | 0.631 | 1.000 | 55% |
| round trips | 68 | 4.9 | duckgrep-hint 4.8 | ×0.99 [0.91, 1.07] | 0.915 | 1.000 | 49% |
| round trips | 68 | 4.9 | serena 4.8 | ×0.99 [0.94, 1.04] | 0.844 | 1.000 | 51% |
| round trips | 68 | 4.9 | serena-hint 4.5 | ×0.94 [0.88, 1.01] | 0.111 | 1.000 | 54% |
| tokens | 68 | 52,258 | duckgrep 50,749 | ×0.97 [0.85, 1.09] | 0.452 | 1.000 | 40% |
| tokens | 68 | 52,258 | duckgrep-hint 55,848 | ×1.07 [0.93, 1.22] | 0.100 | 1.000 | 40% |
| tokens | 68 | 52,258 | serena 70,705 | ×1.35 [1.25, 1.46] | 0.000 | 0.000 | 13% |
| tokens | 68 | 52,258 | serena-hint 66,516 | ×1.27 [1.13, 1.42] | 0.000 | 0.002 | 22% |
| cost ($) | 68 | 0.0561 | duckgrep 0.0499 | ×0.89 [0.81, 0.97] | 0.025 | 1.000 | 59% |
| cost ($) | 68 | 0.0561 | duckgrep-hint 0.0538 | ×0.96 [0.87, 1.06] | 0.700 | 1.000 | 50% |
| cost ($) | 68 | 0.0561 | serena 0.0568 | ×1.01 [0.96, 1.07] | 0.379 | 1.000 | 37% |
| cost ($) | 68 | 0.0561 | serena-hint 0.0553 | ×0.99 [0.90, 1.08] | 0.621 | 1.000 | 43% |
| turns to locate | 68 | 3.6 | duckgrep 3.1 | -0.49 [-1.40, +0.18] | 0.664 | 1.000 | 50% |
| turns to locate | 68 | 3.6 | duckgrep-hint 3.0 | -0.59 [-1.47, +0.13] | 0.158 | 1.000 | 58% |
| turns to locate | 68 | 3.6 | serena 3.4 | -0.19 [-0.81, +0.44] | 0.434 | 1.000 | 50% |
| turns to locate | 68 | 3.6 | serena-hint 3.1 | -0.54 [-1.50, +0.18] | 0.694 | 1.000 | 49% |
| located | 68 | 82% | duckgrep 84% | +1 pp [-4, +7] | 0.655 | 1.000 | 51% |
| located | 68 | 82% | duckgrep-hint 87% | +4 pp [-3, +12] | 0.257 | 1.000 | 52% |
| located | 68 | 82% | serena 87% | +4 pp [-3, +12] | 0.257 | 1.000 | 52% |
| located | 68 | 82% | serena-hint 81% | -1 pp [-7, +4] | 0.655 | 1.000 | 49% |
| success | 68 | 59% | duckgrep 57% | -1 pp [-7, +4] | 0.655 | 1.000 | 49% |
| success | 68 | 59% | duckgrep-hint 59% | +0 pp [-7, +7] | 1.000 | 1.000 | 50% |
| success | 68 | 59% | serena 54% | -4 pp [-10, +0] | 0.083 | 1.000 | 48% |
| success | 68 | 59% | serena-hint 57% | -1 pp [-7, +3] | 0.564 | 1.000 | 49% |
| F1 | 68 | 0.64 | duckgrep 0.63 | -0.02 [-0.09, +0.06] | 0.719 | 1.000 | 50% |
| F1 | 68 | 0.64 | duckgrep-hint 0.61 | -0.03 [-0.10, +0.04] | 0.271 | 1.000 | 46% |
| F1 | 68 | 0.64 | serena 0.59 | -0.05 [-0.10, +0.00] | 0.073 | 1.000 | 47% |
| F1 | 68 | 0.64 | serena-hint 0.69 | +0.04 [-0.01, +0.10] | 0.096 | 1.000 | 57% |

### Python structural questions

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 237 | 3.0 | duckgrep 2.2 | ×0.81 [0.77, 0.85] | 0.000 | 0.000 | 67% |
| tool calls | 237 | 3.0 | duckgrep-hint 2.3 | ×0.82 [0.78, 0.86] | 0.000 | 0.000 | 68% |
| tool calls | 237 | 3.0 | serena 3.0 | ×0.99 [0.96, 1.03] | 0.708 | 1.000 | 51% |
| tool calls | 237 | 3.0 | serena-hint 2.8 | ×0.95 [0.92, 0.99] | 0.017 | 1.000 | 55% |
| round trips | 237 | 3.6 | duckgrep 2.9 | ×0.83 [0.81, 0.86] | 0.000 | 0.000 | 71% |
| round trips | 237 | 3.6 | duckgrep-hint 2.8 | ×0.81 [0.79, 0.84] | 0.000 | 0.000 | 78% |
| round trips | 237 | 3.6 | serena 3.4 | ×0.96 [0.94, 0.99] | 0.010 | 1.000 | 55% |
| round trips | 237 | 3.6 | serena-hint 2.9 | ×0.84 [0.81, 0.87] | 0.000 | 0.000 | 74% |
| tokens | 237 | 27,366 | duckgrep 23,283 | ×0.85 [0.81, 0.90] | 0.000 | 0.000 | 54% |
| tokens | 237 | 27,366 | duckgrep-hint 22,491 | ×0.82 [0.78, 0.86] | 0.000 | 0.000 | 65% |
| tokens | 237 | 27,366 | serena 40,342 | ×1.47 [1.42, 1.53] | 0.000 | 0.000 | 9% |
| tokens | 237 | 27,366 | serena-hint 33,579 | ×1.23 [1.17, 1.28] | 0.000 | 0.000 | 24% |
| cost ($) | 237 | 0.0267 | duckgrep 0.0219 | ×0.82 [0.78, 0.86] | 0.000 | 0.000 | 68% |
| cost ($) | 237 | 0.0267 | duckgrep-hint 0.0203 | ×0.76 [0.72, 0.80] | 0.000 | 0.000 | 76% |
| cost ($) | 237 | 0.0267 | serena 0.0295 | ×1.10 [1.07, 1.14] | 0.000 | 0.000 | 27% |
| cost ($) | 237 | 0.0267 | serena-hint 0.0255 | ×0.96 [0.92, 0.99] | 0.117 | 1.000 | 53% |
| turns to locate | 237 | 1.6 | duckgrep 1.1 | -0.46 [-0.54, -0.38] | 0.000 | 0.000 | 71% |
| turns to locate | 237 | 1.6 | duckgrep-hint 1.0 | -0.56 [-0.64, -0.48] | 0.000 | 0.000 | 76% |
| turns to locate | 237 | 1.6 | serena 1.5 | -0.06 [-0.12, +0.00] | 0.065 | 1.000 | 53% |
| turns to locate | 237 | 1.6 | serena-hint 1.1 | -0.47 [-0.55, -0.39] | 0.000 | 0.000 | 72% |
| located | 237 | 100% | duckgrep 100% | +0 pp [-1, +1] | 1.000 | 1.000 | 50% |
| located | 237 | 100% | duckgrep-hint 100% | +0 pp [-1, +1] | 1.000 | 1.000 | 50% |
| located | 237 | 100% | serena 100% | +0 pp [-1, +1] | 1.000 | 1.000 | 50% |
| located | 237 | 100% | serena-hint 98% | -1 pp [-3, +0] | 0.180 | 1.000 | 49% |
| success | 237 | 94% | duckgrep 96% | +2 pp [-1, +5] | 0.197 | 1.000 | 51% |
| success | 237 | 94% | duckgrep-hint 96% | +2 pp [-1, +6] | 0.251 | 1.000 | 51% |
| success | 237 | 94% | serena 93% | -1 pp [-4, +3] | 0.617 | 1.000 | 50% |
| success | 237 | 94% | serena-hint 95% | +2 pp [-2, +5] | 0.317 | 1.000 | 51% |
| F1 | 237 | 0.96 | duckgrep 0.97 | +0.01 [-0.00, +0.02] | 0.044 | 1.000 | 52% |
| F1 | 237 | 0.96 | duckgrep-hint 0.97 | +0.00 [-0.01, +0.02] | 0.729 | 1.000 | 50% |
| F1 | 237 | 0.96 | serena 0.96 | -0.00 [-0.02, +0.01] | 0.446 | 1.000 | 49% |
| F1 | 237 | 0.96 | serena-hint 0.97 | +0.01 [-0.00, +0.02] | 0.134 | 1.000 | 51% |

### Rust structural questions

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 257 | 3.3 | duckgrep 2.7 | ×0.88 [0.84, 0.91] | 0.000 | 0.000 | 63% |
| tool calls | 257 | 3.3 | duckgrep-hint 2.6 | ×0.84 [0.81, 0.88] | 0.000 | 0.000 | 66% |
| tool calls | 257 | 3.3 | serena 3.5 | ×1.06 [1.02, 1.10] | 0.002 | 0.233 | 43% |
| tool calls | 257 | 3.3 | serena-hint 3.9 | ×1.14 [1.10, 1.18] | 0.000 | 0.000 | 31% |
| round trips | 257 | 3.7 | duckgrep 3.3 | ×0.92 [0.89, 0.94] | 0.000 | 0.000 | 63% |
| round trips | 257 | 3.7 | duckgrep-hint 3.1 | ×0.88 [0.85, 0.90] | 0.000 | 0.000 | 69% |
| round trips | 257 | 3.7 | serena 3.8 | ×1.02 [1.00, 1.05] | 0.030 | 1.000 | 47% |
| round trips | 257 | 3.7 | serena-hint 3.8 | ×1.02 [0.99, 1.04] | 0.474 | 1.000 | 45% |
| tokens | 257 | 29,396 | duckgrep 28,189 | ×0.96 [0.92, 1.00] | 0.046 | 1.000 | 47% |
| tokens | 257 | 29,396 | duckgrep-hint 25,966 | ×0.88 [0.85, 0.92] | 0.000 | 0.000 | 58% |
| tokens | 257 | 29,396 | serena 46,530 | ×1.58 [1.53, 1.63] | 0.000 | 0.000 | 4% |
| tokens | 257 | 29,396 | serena-hint 45,912 | ×1.56 [1.50, 1.62] | 0.000 | 0.000 | 6% |
| cost ($) | 257 | 0.0299 | duckgrep 0.0258 | ×0.86 [0.83, 0.90] | 0.000 | 0.000 | 63% |
| cost ($) | 257 | 0.0299 | duckgrep-hint 0.0226 | ×0.75 [0.72, 0.79] | 0.000 | 0.000 | 79% |
| cost ($) | 257 | 0.0299 | serena 0.0346 | ×1.16 [1.12, 1.19] | 0.000 | 0.000 | 21% |
| cost ($) | 257 | 0.0299 | serena-hint 0.0336 | ×1.12 [1.09, 1.16] | 0.000 | 0.000 | 28% |
| turns to locate | 257 | 1.7 | duckgrep 1.2 | -0.47 [-0.56, -0.39] | 0.000 | 0.000 | 71% |
| turns to locate | 257 | 1.7 | duckgrep-hint 1.1 | -0.61 [-0.70, -0.52] | 0.000 | 0.000 | 77% |
| turns to locate | 257 | 1.7 | serena 1.7 | +0.03 [-0.04, +0.09] | 0.187 | 1.000 | 48% |
| turns to locate | 257 | 1.7 | serena-hint 1.8 | +0.13 [+0.03, +0.23] | 0.005 | 0.564 | 44% |
| located | 257 | 98% | duckgrep 100% | +2 pp [+0, +3] | 0.046 | 1.000 | 51% |
| located | 257 | 98% | duckgrep-hint 100% | +2 pp [+0, +3] | 0.046 | 1.000 | 51% |
| located | 257 | 98% | serena 100% | +2 pp [+0, +3] | 0.046 | 1.000 | 51% |
| located | 257 | 98% | serena-hint 100% | +1 pp [+0, +3] | 0.083 | 1.000 | 51% |
| success | 257 | 96% | duckgrep 96% | +0 pp [-3, +4] | 0.808 | 1.000 | 50% |
| success | 257 | 96% | duckgrep-hint 96% | +0 pp [-3, +3] | 1.000 | 1.000 | 50% |
| success | 257 | 96% | serena 97% | +1 pp [-1, +3] | 0.480 | 1.000 | 50% |
| success | 257 | 96% | serena-hint 96% | +0 pp [-2, +3] | 0.782 | 1.000 | 50% |
| F1 | 257 | 0.97 | duckgrep 0.97 | -0.00 [-0.02, +0.01] | 0.922 | 1.000 | 51% |
| F1 | 257 | 0.97 | duckgrep-hint 0.97 | -0.01 [-0.02, +0.01] | 0.575 | 1.000 | 50% |
| F1 | 257 | 0.97 | serena 0.97 | -0.00 [-0.01, +0.01] | 0.819 | 1.000 | 50% |
| F1 | 257 | 0.97 | serena-hint 0.98 | +0.01 [-0.01, +0.02] | 0.414 | 1.000 | 51% |

### Rust localization, post-cutoff issues

| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |
|---|---:|---:|---|---|---:|---:|---:|
| tool calls | 21 | 6.3 | duckgrep 5.6 | ×0.90 [0.75, 1.06] | 0.256 | 1.000 | 62% |
| tool calls | 21 | 6.3 | duckgrep-hint 5.5 | ×0.89 [0.71, 1.08] | 0.346 | 1.000 | 55% |
| tool calls | 21 | 6.3 | serena 6.4 | ×1.00 [0.87, 1.15] | 0.845 | 1.000 | 45% |
| tool calls | 21 | 6.3 | serena-hint 5.5 | ×0.88 [0.77, 1.00] | 0.088 | 1.000 | 60% |
| round trips | 21 | 6.2 | duckgrep 5.7 | ×0.93 [0.79, 1.07] | 0.495 | 1.000 | 57% |
| round trips | 21 | 6.2 | duckgrep-hint 5.5 | ×0.91 [0.75, 1.09] | 0.360 | 1.000 | 50% |
| round trips | 21 | 6.2 | serena 6.4 | ×1.03 [0.93, 1.14] | 0.570 | 1.000 | 43% |
| round trips | 21 | 6.2 | serena-hint 5.7 | ×0.93 [0.84, 1.02] | 0.118 | 1.000 | 57% |
| tokens | 21 | 77,158 | duckgrep 70,450 | ×0.91 [0.68, 1.17] | 0.973 | 1.000 | 38% |
| tokens | 21 | 77,158 | duckgrep-hint 72,526 | ×0.94 [0.66, 1.30] | 0.973 | 1.000 | 52% |
| tokens | 21 | 77,158 | serena 106,236 | ×1.38 [1.17, 1.63] | 0.001 | 0.169 | 14% |
| tokens | 21 | 77,158 | serena-hint 93,826 | ×1.22 [1.00, 1.45] | 0.029 | 1.000 | 29% |
| cost ($) | 21 | 0.0772 | duckgrep 0.0655 | ×0.85 [0.68, 1.02] | 0.393 | 1.000 | 48% |
| cost ($) | 21 | 0.0772 | duckgrep-hint 0.0682 | ×0.88 [0.68, 1.13] | 0.473 | 1.000 | 57% |
| cost ($) | 21 | 0.0772 | serena 0.0820 | ×1.06 [0.95, 1.19] | 0.320 | 1.000 | 38% |
| cost ($) | 21 | 0.0772 | serena-hint 0.0756 | ×0.98 [0.84, 1.13] | 1.000 | 1.000 | 48% |
| turns to locate | 21 | 4.9 | duckgrep 4.0 | -0.95 [-3.62, +0.67] | 0.856 | 1.000 | 48% |
| turns to locate | 21 | 4.9 | duckgrep-hint 3.8 | -1.10 [-3.57, +0.76] | 0.506 | 1.000 | 60% |
| turns to locate | 21 | 4.9 | serena 5.8 | +0.86 [-0.33, +2.43] | 0.177 | 1.000 | 38% |
| turns to locate | 21 | 4.9 | serena-hint 4.0 | -0.90 [-3.62, +0.81] | 0.974 | 1.000 | 50% |
| located | 21 | 71% | duckgrep 71% | +0 pp [-14, +14] | 1.000 | 1.000 | 50% |
| located | 21 | 71% | duckgrep-hint 76% | +5 pp [+0, +14] | 0.317 | 1.000 | 52% |
| located | 21 | 71% | serena 71% | +0 pp [-14, +14] | 1.000 | 1.000 | 50% |
| located | 21 | 71% | serena-hint 71% | +0 pp [-14, +14] | 1.000 | 1.000 | 50% |
| success | 21 | 38% | duckgrep 43% | +5 pp [+0, +14] | 0.317 | 1.000 | 52% |
| success | 21 | 38% | duckgrep-hint 52% | +14 pp [+0, +29] | 0.083 | 1.000 | 57% |
| success | 21 | 38% | serena 33% | -5 pp [-14, +0] | 0.317 | 1.000 | 48% |
| success | 21 | 38% | serena-hint 43% | +5 pp [+0, +14] | 0.317 | 1.000 | 52% |
| F1 | 21 | 0.37 | duckgrep 0.43 | +0.06 [-0.06, +0.20] | 0.502 | 1.000 | 55% |
| F1 | 21 | 0.37 | duckgrep-hint 0.45 | +0.08 [-0.06, +0.24] | 0.312 | 1.000 | 52% |
| F1 | 21 | 0.37 | serena 0.32 | -0.05 [-0.17, +0.04] | 0.458 | 1.000 | 50% |
| F1 | 21 | 0.37 | serena-hint 0.50 | +0.13 [+0.01, +0.26] | 0.090 | 1.000 | 64% |

### Adoption

| setup | kind | runs | used its tool | its share of calls | calls (used / not) |
|---|---|---:|---:|---:|---|
| duckgrep | localization | 230 | 0% | 0% | – / 3.5 |
| duckgrep | structural | 494 | 75% | 53% | 2.9 / 2.3 |
| duckgrep-hint | localization | 230 | 73% | 34% | 3.8 / 2.9 |
| duckgrep-hint | structural | 494 | 99% | 83% | 2.7 / 1.0 |
| serena | localization | 230 | 0% | 0% | – / 3.8 |
| serena | structural | 494 | 11% | 4% | 4.3 / 3.5 |
| serena-hint | localization | 230 | 57% | 37% | 2.9 / 4.0 |
| serena-hint | structural | 494 | 82% | 32% | 4.0 / 2.2 |

### By repo

| kind | repo | setup | runs | tool calls | cost ($) | success |
|---|---|---|---:|---:|---:|---:|
| localization | apache/datafusion | baseline | 3 | 2.3 | 0.029 | 100% |
| localization | apache/datafusion | duckgrep | 3 | 1.7 | 0.025 | 67% |
| localization | apache/datafusion | duckgrep-hint | 3 | 1.7 | 0.026 | 100% |
| localization | apache/datafusion | serena | 3 | 1.7 | 0.027 | 100% |
| localization | apache/datafusion | serena-hint | 3 | 2.3 | 0.026 | 100% |
| localization | astral-sh/uv | baseline | 1 | 6.0 | 0.126 | 0% |
| localization | astral-sh/uv | duckgrep | 1 | 7.0 | 0.141 | 0% |
| localization | astral-sh/uv | duckgrep-hint | 1 | 5.0 | 0.133 | 0% |
| localization | astral-sh/uv | serena | 1 | 7.0 | 0.143 | 0% |
| localization | astral-sh/uv | serena-hint | 1 | 5.0 | 0.111 | 0% |
| localization | astropy/astropy | baseline | 4 | 2.0 | 0.025 | 100% |
| localization | astropy/astropy | duckgrep | 4 | 2.0 | 0.024 | 100% |
| localization | astropy/astropy | duckgrep-hint | 4 | 1.8 | 0.021 | 100% |
| localization | astropy/astropy | serena | 4 | 1.8 | 0.024 | 75% |
| localization | astropy/astropy | serena-hint | 4 | 1.8 | 0.024 | 75% |
| localization | Automattic/harper | baseline | 1 | 5.0 | 0.037 | 100% |
| localization | Automattic/harper | duckgrep | 1 | 4.0 | 0.028 | 100% |
| localization | Automattic/harper | duckgrep-hint | 1 | 8.0 | 0.083 | 0% |
| localization | Automattic/harper | serena | 1 | 7.0 | 0.040 | 100% |
| localization | Automattic/harper | serena-hint | 1 | 3.0 | 0.025 | 100% |
| localization | burntsushi/ripgrep | baseline | 2 | 3.5 | 0.062 | 100% |
| localization | burntsushi/ripgrep | duckgrep | 2 | 4.0 | 0.045 | 100% |
| localization | burntsushi/ripgrep | duckgrep-hint | 2 | 6.5 | 0.059 | 100% |
| localization | burntsushi/ripgrep | serena | 2 | 3.0 | 0.044 | 100% |
| localization | burntsushi/ripgrep | serena-hint | 2 | 3.0 | 0.046 | 100% |
| localization | DioxusLabs/dioxus | baseline | 1 | 3.0 | 0.022 | 0% |
| localization | DioxusLabs/dioxus | duckgrep | 1 | 3.0 | 0.024 | 0% |
| localization | DioxusLabs/dioxus | duckgrep-hint | 1 | 2.0 | 0.020 | 0% |
| localization | DioxusLabs/dioxus | serena | 1 | 3.0 | 0.028 | 0% |
| localization | DioxusLabs/dioxus | serena-hint | 1 | 2.0 | 0.024 | 0% |
| localization | django/django | baseline | 62 | 2.7 | 0.030 | 94% |
| localization | django/django | duckgrep | 62 | 2.9 | 0.033 | 94% |
| localization | django/django | duckgrep-hint | 62 | 2.7 | 0.030 | 94% |
| localization | django/django | serena | 62 | 2.8 | 0.033 | 95% |
| localization | django/django | serena-hint | 62 | 2.4 | 0.029 | 92% |
| localization | gfx-rs/wgpu | baseline | 1 | 4.0 | 0.046 | 0% |
| localization | gfx-rs/wgpu | duckgrep | 1 | 4.0 | 0.045 | 0% |
| localization | gfx-rs/wgpu | duckgrep-hint | 1 | 4.0 | 0.047 | 0% |
| localization | gfx-rs/wgpu | serena | 1 | 5.0 | 0.047 | 0% |
| localization | gfx-rs/wgpu | serena-hint | 1 | 3.0 | 0.044 | 0% |
| localization | gitbutlerapp/gitbutler | baseline | 1 | 8.0 | 0.081 | 0% |
| localization | gitbutlerapp/gitbutler | duckgrep | 1 | 5.0 | 0.049 | 0% |
| localization | gitbutlerapp/gitbutler | duckgrep-hint | 1 | 8.0 | 0.088 | 0% |
| localization | gitbutlerapp/gitbutler | serena | 1 | 6.0 | 0.065 | 0% |
| localization | gitbutlerapp/gitbutler | serena-hint | 1 | 5.0 | 0.059 | 0% |
| localization | GitoxideLabs/gitoxide | baseline | 1 | 12.0 | 0.250 | 100% |
| localization | GitoxideLabs/gitoxide | duckgrep | 1 | 3.0 | 0.070 | 0% |
| localization | GitoxideLabs/gitoxide | duckgrep-hint | 1 | 8.0 | 0.142 | 100% |
| localization | GitoxideLabs/gitoxide | serena | 1 | 10.0 | 0.157 | 100% |
| localization | GitoxideLabs/gitoxide | serena-hint | 1 | 3.0 | 0.074 | 0% |
| localization | gleam-lang/gleam | baseline | 7 | 9.6 | 0.095 | 29% |
| localization | gleam-lang/gleam | duckgrep | 7 | 8.1 | 0.081 | 29% |
| localization | gleam-lang/gleam | duckgrep-hint | 7 | 8.1 | 0.092 | 57% |
| localization | gleam-lang/gleam | serena | 7 | 10.3 | 0.104 | 29% |
| localization | gleam-lang/gleam | serena-hint | 7 | 8.6 | 0.091 | 29% |
| localization | h4ckf0r0day/obscura | baseline | 2 | 10.0 | 0.111 | 0% |
| localization | h4ckf0r0day/obscura | duckgrep | 2 | 5.5 | 0.072 | 0% |
| localization | h4ckf0r0day/obscura | duckgrep-hint | 2 | 5.5 | 0.073 | 0% |
| localization | h4ckf0r0day/obscura | serena | 2 | 5.0 | 0.075 | 0% |
| localization | h4ckf0r0day/obscura | serena-hint | 2 | 7.5 | 0.103 | 0% |
| localization | J-F-Liu/lopdf | baseline | 1 | 2.0 | 0.020 | 0% |
| localization | J-F-Liu/lopdf | duckgrep | 1 | 2.0 | 0.020 | 0% |
| localization | J-F-Liu/lopdf | duckgrep-hint | 1 | 2.0 | 0.021 | 0% |
| localization | J-F-Liu/lopdf | serena | 1 | 3.0 | 0.025 | 0% |
| localization | J-F-Liu/lopdf | serena-hint | 1 | 3.0 | 0.061 | 0% |
| localization | jj-vcs/jj | baseline | 1 | 6.0 | 0.043 | 100% |
| localization | jj-vcs/jj | duckgrep | 1 | 4.0 | 0.040 | 100% |
| localization | jj-vcs/jj | duckgrep-hint | 1 | 2.0 | 0.025 | 100% |
| localization | jj-vcs/jj | serena | 1 | 4.0 | 0.045 | 100% |
| localization | jj-vcs/jj | serena-hint | 1 | 4.0 | 0.043 | 100% |
| localization | jtroo/kanata | baseline | 1 | 6.0 | 0.079 | 100% |
| localization | jtroo/kanata | duckgrep | 1 | 7.0 | 0.084 | 100% |
| localization | jtroo/kanata | duckgrep-hint | 1 | 7.0 | 0.074 | 100% |
| localization | jtroo/kanata | serena | 1 | 6.0 | 0.091 | 0% |
| localization | jtroo/kanata | serena-hint | 1 | 6.0 | 0.078 | 100% |
| localization | lakehq/sail | baseline | 1 | 4.0 | 0.052 | 100% |
| localization | lakehq/sail | duckgrep | 1 | 2.0 | 0.033 | 100% |
| localization | lakehq/sail | duckgrep-hint | 1 | 3.0 | 0.029 | 100% |
| localization | lakehq/sail | serena | 1 | 3.0 | 0.051 | 100% |
| localization | lakehq/sail | serena-hint | 1 | 2.0 | 0.031 | 100% |
| localization | lance-format/lance | baseline | 2 | 24.5 | 0.457 | 50% |
| localization | lance-format/lance | duckgrep | 2 | 12.5 | 0.217 | 50% |
| localization | lance-format/lance | duckgrep-hint | 2 | 7.5 | 0.112 | 50% |
| localization | lance-format/lance | serena | 2 | 30.0 | 0.549 | 0% |
| localization | lance-format/lance | serena-hint | 2 | 11.5 | 0.224 | 50% |
| localization | matplotlib/matplotlib | baseline | 8 | 2.6 | 0.030 | 100% |
| localization | matplotlib/matplotlib | duckgrep | 8 | 1.9 | 0.024 | 88% |
| localization | matplotlib/matplotlib | duckgrep-hint | 8 | 2.8 | 0.028 | 88% |
| localization | matplotlib/matplotlib | serena | 8 | 2.4 | 0.032 | 88% |
| localization | matplotlib/matplotlib | serena-hint | 8 | 2.4 | 0.031 | 100% |
| localization | modelcontextprotocol/rust-sdk | baseline | 3 | 3.7 | 0.046 | 100% |
| localization | modelcontextprotocol/rust-sdk | duckgrep | 3 | 4.3 | 0.047 | 100% |
| localization | modelcontextprotocol/rust-sdk | duckgrep-hint | 3 | 5.0 | 0.065 | 67% |
| localization | modelcontextprotocol/rust-sdk | serena | 3 | 6.0 | 0.066 | 100% |
| localization | modelcontextprotocol/rust-sdk | serena-hint | 3 | 4.0 | 0.056 | 100% |
| localization | mwaskom/seaborn | baseline | 1 | 6.0 | 0.051 | 100% |
| localization | mwaskom/seaborn | duckgrep | 1 | 6.0 | 0.058 | 100% |
| localization | mwaskom/seaborn | duckgrep-hint | 1 | 3.0 | 0.051 | 100% |
| localization | mwaskom/seaborn | serena | 1 | 6.0 | 0.056 | 100% |
| localization | mwaskom/seaborn | serena-hint | 1 | 7.0 | 0.083 | 100% |
| localization | nushell/nushell | baseline | 3 | 3.7 | 0.036 | 67% |
| localization | nushell/nushell | duckgrep | 3 | 4.0 | 0.035 | 67% |
| localization | nushell/nushell | duckgrep-hint | 3 | 2.3 | 0.031 | 67% |
| localization | nushell/nushell | serena | 3 | 3.3 | 0.034 | 33% |
| localization | nushell/nushell | serena-hint | 3 | 4.7 | 0.042 | 67% |
| localization | NVIDIA/OpenShell | baseline | 1 | 2.0 | 0.040 | 0% |
| localization | NVIDIA/OpenShell | duckgrep | 1 | 6.0 | 0.052 | 0% |
| localization | NVIDIA/OpenShell | duckgrep-hint | 1 | 4.0 | 0.044 | 0% |
| localization | NVIDIA/OpenShell | serena | 1 | 2.0 | 0.035 | 0% |
| localization | NVIDIA/OpenShell | serena-hint | 1 | 4.0 | 0.042 | 0% |
| localization | oxc-project/oxc | baseline | 5 | 6.0 | 0.069 | 80% |
| localization | oxc-project/oxc | duckgrep | 5 | 4.4 | 0.058 | 60% |
| localization | oxc-project/oxc | duckgrep-hint | 5 | 6.8 | 0.078 | 60% |
| localization | oxc-project/oxc | serena | 5 | 4.8 | 0.057 | 80% |
| localization | oxc-project/oxc | serena-hint | 5 | 6.2 | 0.077 | 80% |
| localization | pallets/flask | baseline | 1 | 1.0 | 0.018 | 100% |
| localization | pallets/flask | duckgrep | 1 | 1.0 | 0.019 | 100% |
| localization | pallets/flask | duckgrep-hint | 1 | 2.0 | 0.022 | 100% |
| localization | pallets/flask | serena | 1 | 1.0 | 0.019 | 100% |
| localization | pallets/flask | serena-hint | 1 | 1.0 | 0.019 | 100% |
| localization | prefix-dev/pixi | baseline | 1 | 5.0 | 0.061 | 0% |
| localization | prefix-dev/pixi | duckgrep | 1 | 8.0 | 0.083 | 100% |
| localization | prefix-dev/pixi | duckgrep-hint | 1 | 5.0 | 0.071 | 100% |
| localization | prefix-dev/pixi | serena | 1 | 7.0 | 0.069 | 0% |
| localization | prefix-dev/pixi | serena-hint | 1 | 4.0 | 0.055 | 100% |
| localization | ProvableHQ/leo | baseline | 2 | 8.0 | 0.075 | 50% |
| localization | ProvableHQ/leo | duckgrep | 2 | 8.0 | 0.073 | 50% |
| localization | ProvableHQ/leo | duckgrep-hint | 2 | 5.5 | 0.055 | 50% |
| localization | ProvableHQ/leo | serena | 2 | 5.5 | 0.057 | 50% |
| localization | ProvableHQ/leo | serena-hint | 2 | 8.5 | 0.068 | 50% |
| localization | psf/requests | baseline | 2 | 3.0 | 0.026 | 100% |
| localization | psf/requests | duckgrep | 2 | 3.5 | 0.035 | 100% |
| localization | psf/requests | duckgrep-hint | 2 | 3.5 | 0.037 | 100% |
| localization | psf/requests | serena | 2 | 3.0 | 0.027 | 100% |
| localization | psf/requests | serena-hint | 2 | 2.5 | 0.026 | 100% |
| localization | pydata/xarray | baseline | 4 | 2.0 | 0.026 | 50% |
| localization | pydata/xarray | duckgrep | 4 | 2.0 | 0.027 | 50% |
| localization | pydata/xarray | duckgrep-hint | 4 | 2.5 | 0.029 | 50% |
| localization | pydata/xarray | serena | 4 | 2.2 | 0.033 | 50% |
| localization | pydata/xarray | serena-hint | 4 | 2.5 | 0.032 | 50% |
| localization | pylint-dev/pylint | baseline | 4 | 2.5 | 0.037 | 75% |
| localization | pylint-dev/pylint | duckgrep | 4 | 2.8 | 0.045 | 75% |
| localization | pylint-dev/pylint | duckgrep-hint | 4 | 3.5 | 0.053 | 100% |
| localization | pylint-dev/pylint | serena | 4 | 2.2 | 0.042 | 75% |
| localization | pylint-dev/pylint | serena-hint | 4 | 2.2 | 0.040 | 75% |
| localization | pytest-dev/pytest | baseline | 12 | 2.1 | 0.029 | 92% |
| localization | pytest-dev/pytest | duckgrep | 12 | 2.2 | 0.029 | 83% |
| localization | pytest-dev/pytest | duckgrep-hint | 12 | 2.1 | 0.027 | 83% |
| localization | pytest-dev/pytest | serena | 12 | 2.2 | 0.030 | 92% |
| localization | pytest-dev/pytest | serena-hint | 12 | 2.1 | 0.028 | 83% |
| localization | ruffle-rs/ruffle | baseline | 1 | 11.0 | 0.111 | 100% |
| localization | ruffle-rs/ruffle | duckgrep | 1 | 11.0 | 0.127 | 100% |
| localization | ruffle-rs/ruffle | duckgrep-hint | 1 | 10.0 | 0.108 | 100% |
| localization | ruffle-rs/ruffle | serena | 1 | 8.0 | 0.083 | 100% |
| localization | ruffle-rs/ruffle | serena-hint | 1 | 8.0 | 0.083 | 100% |
| localization | rust-lang/rust-analyzer | baseline | 7 | 4.3 | 0.059 | 29% |
| localization | rust-lang/rust-analyzer | duckgrep | 7 | 3.7 | 0.056 | 29% |
| localization | rust-lang/rust-analyzer | duckgrep-hint | 7 | 4.4 | 0.056 | 29% |
| localization | rust-lang/rust-analyzer | serena | 7 | 3.6 | 0.057 | 29% |
| localization | rust-lang/rust-analyzer | serena-hint | 7 | 4.3 | 0.063 | 29% |
| localization | rust-lang/rust-bindgen | baseline | 1 | 7.0 | 0.066 | 100% |
| localization | rust-lang/rust-bindgen | duckgrep | 1 | 5.0 | 0.073 | 100% |
| localization | rust-lang/rust-bindgen | duckgrep-hint | 1 | 8.0 | 0.084 | 100% |
| localization | rust-lang/rust-bindgen | serena | 1 | 8.0 | 0.076 | 100% |
| localization | rust-lang/rust-bindgen | serena-hint | 1 | 7.0 | 0.089 | 100% |
| localization | rust-lang/rustfmt | baseline | 1 | 6.0 | 0.087 | 100% |
| localization | rust-lang/rustfmt | duckgrep | 1 | 2.0 | 0.048 | 100% |
| localization | rust-lang/rustfmt | duckgrep-hint | 1 | 3.0 | 0.041 | 100% |
| localization | rust-lang/rustfmt | serena | 1 | 5.0 | 0.080 | 100% |
| localization | rust-lang/rustfmt | serena-hint | 1 | 2.0 | 0.039 | 100% |
| localization | scikit-learn/scikit-learn | baseline | 8 | 2.0 | 0.025 | 88% |
| localization | scikit-learn/scikit-learn | duckgrep | 8 | 2.1 | 0.024 | 88% |
| localization | scikit-learn/scikit-learn | duckgrep-hint | 8 | 2.0 | 0.025 | 88% |
| localization | scikit-learn/scikit-learn | serena | 8 | 2.4 | 0.027 | 88% |
| localization | scikit-learn/scikit-learn | serena-hint | 8 | 2.0 | 0.026 | 88% |
| localization | sharkdp/bat | baseline | 6 | 3.7 | 0.042 | 67% |
| localization | sharkdp/bat | duckgrep | 6 | 3.3 | 0.038 | 67% |
| localization | sharkdp/bat | duckgrep-hint | 6 | 3.3 | 0.041 | 67% |
| localization | sharkdp/bat | serena | 6 | 4.0 | 0.048 | 67% |
| localization | sharkdp/bat | serena-hint | 6 | 3.3 | 0.041 | 67% |
| localization | slatedb/slatedb | baseline | 1 | 2.0 | 0.032 | 0% |
| localization | slatedb/slatedb | duckgrep | 1 | 2.0 | 0.029 | 0% |
| localization | slatedb/slatedb | duckgrep-hint | 1 | 2.0 | 0.039 | 0% |
| localization | slatedb/slatedb | serena | 1 | 2.0 | 0.028 | 0% |
| localization | slatedb/slatedb | serena-hint | 1 | 2.0 | 0.037 | 0% |
| localization | sphinx-doc/sphinx | baseline | 15 | 2.6 | 0.030 | 87% |
| localization | sphinx-doc/sphinx | duckgrep | 15 | 2.7 | 0.033 | 100% |
| localization | sphinx-doc/sphinx | duckgrep-hint | 15 | 2.8 | 0.034 | 93% |
| localization | sphinx-doc/sphinx | serena | 15 | 2.6 | 0.031 | 80% |
| localization | sphinx-doc/sphinx | serena-hint | 15 | 2.5 | 0.030 | 87% |
| localization | sympy/sympy | baseline | 41 | 3.7 | 0.040 | 68% |
| localization | sympy/sympy | duckgrep | 41 | 3.4 | 0.040 | 68% |
| localization | sympy/sympy | duckgrep-hint | 41 | 3.5 | 0.039 | 61% |
| localization | sympy/sympy | serena | 41 | 3.6 | 0.043 | 73% |
| localization | sympy/sympy | serena-hint | 41 | 3.5 | 0.043 | 68% |
| localization | tokio-rs/tokio | baseline | 1 | 3.0 | 0.038 | 0% |
| localization | tokio-rs/tokio | duckgrep | 1 | 2.0 | 0.032 | 100% |
| localization | tokio-rs/tokio | duckgrep-hint | 1 | 3.0 | 0.038 | 0% |
| localization | tokio-rs/tokio | serena | 1 | 3.0 | 0.037 | 0% |
| localization | tokio-rs/tokio | serena-hint | 1 | 4.0 | 0.044 | 0% |
| localization | uutils/coreutils | baseline | 5 | 3.4 | 0.030 | 100% |
| localization | uutils/coreutils | duckgrep | 5 | 3.2 | 0.029 | 100% |
| localization | uutils/coreutils | duckgrep-hint | 5 | 3.4 | 0.035 | 100% |
| localization | uutils/coreutils | serena | 5 | 3.4 | 0.031 | 100% |
| localization | uutils/coreutils | serena-hint | 5 | 2.8 | 0.035 | 80% |
| localization | vercel/turborepo | baseline | 1 | 6.0 | 0.085 | 100% |
| localization | vercel/turborepo | duckgrep | 1 | 5.0 | 0.085 | 100% |
| localization | vercel/turborepo | duckgrep-hint | 1 | 5.0 | 0.093 | 100% |
| localization | vercel/turborepo | serena | 1 | 5.0 | 0.086 | 100% |
| localization | vercel/turborepo | serena-hint | 1 | 6.0 | 0.094 | 100% |
| localization | web-infra-dev/rspack | baseline | 3 | 6.0 | 0.119 | 67% |
| localization | web-infra-dev/rspack | duckgrep | 3 | 10.0 | 0.112 | 67% |
| localization | web-infra-dev/rspack | duckgrep-hint | 3 | 9.7 | 0.121 | 67% |
| localization | web-infra-dev/rspack | serena | 3 | 8.3 | 0.126 | 67% |
| localization | web-infra-dev/rspack | serena-hint | 3 | 8.0 | 0.146 | 67% |
| structural | ajeetdsouza/zoxide | baseline | 20 | 3.5 | 0.029 | 100% |
| structural | ajeetdsouza/zoxide | duckgrep | 20 | 2.6 | 0.022 | 95% |
| structural | ajeetdsouza/zoxide | duckgrep-hint | 20 | 2.9 | 0.023 | 100% |
| structural | ajeetdsouza/zoxide | serena | 20 | 4.0 | 0.033 | 100% |
| structural | ajeetdsouza/zoxide | serena-hint | 20 | 4.2 | 0.032 | 100% |
| structural | alacritty/alacritty | baseline | 20 | 3.1 | 0.033 | 90% |
| structural | alacritty/alacritty | duckgrep | 20 | 3.0 | 0.038 | 95% |
| structural | alacritty/alacritty | duckgrep-hint | 20 | 3.4 | 0.033 | 90% |
| structural | alacritty/alacritty | serena | 20 | 4.0 | 0.044 | 95% |
| structural | alacritty/alacritty | serena-hint | 20 | 4.5 | 0.043 | 100% |
| structural | BurntSushi/ripgrep | baseline | 20 | 3.5 | 0.035 | 95% |
| structural | BurntSushi/ripgrep | duckgrep | 20 | 2.7 | 0.030 | 100% |
| structural | BurntSushi/ripgrep | duckgrep-hint | 20 | 2.1 | 0.022 | 90% |
| structural | BurntSushi/ripgrep | serena | 20 | 3.4 | 0.037 | 95% |
| structural | BurntSushi/ripgrep | serena-hint | 20 | 4.2 | 0.039 | 90% |
| structural | casey/just | baseline | 17 | 3.9 | 0.036 | 100% |
| structural | casey/just | duckgrep | 17 | 3.5 | 0.031 | 100% |
| structural | casey/just | duckgrep-hint | 17 | 3.1 | 0.026 | 100% |
| structural | casey/just | serena | 17 | 3.6 | 0.038 | 94% |
| structural | casey/just | serena-hint | 17 | 4.8 | 0.042 | 100% |
| structural | clap-rs/clap | baseline | 20 | 3.1 | 0.030 | 95% |
| structural | clap-rs/clap | duckgrep | 20 | 2.9 | 0.028 | 100% |
| structural | clap-rs/clap | duckgrep-hint | 20 | 2.6 | 0.025 | 100% |
| structural | clap-rs/clap | serena | 20 | 3.6 | 0.037 | 95% |
| structural | clap-rs/clap | serena-hint | 20 | 4.3 | 0.040 | 90% |
| structural | dandavison/delta | baseline | 20 | 3.8 | 0.035 | 95% |
| structural | dandavison/delta | duckgrep | 20 | 2.9 | 0.029 | 95% |
| structural | dandavison/delta | duckgrep-hint | 20 | 2.8 | 0.024 | 80% |
| structural | dandavison/delta | serena | 20 | 4.5 | 0.045 | 100% |
| structural | dandavison/delta | serena-hint | 20 | 4.8 | 0.038 | 100% |
| structural | encode/httpx | baseline | 20 | 3.3 | 0.030 | 95% |
| structural | encode/httpx | duckgrep | 20 | 2.6 | 0.029 | 95% |
| structural | encode/httpx | duckgrep-hint | 20 | 2.5 | 0.025 | 100% |
| structural | encode/httpx | serena | 20 | 3.0 | 0.030 | 95% |
| structural | encode/httpx | serena-hint | 20 | 3.0 | 0.028 | 100% |
| structural | encode/starlette | baseline | 20 | 3.4 | 0.031 | 95% |
| structural | encode/starlette | duckgrep | 20 | 2.2 | 0.027 | 75% |
| structural | encode/starlette | duckgrep-hint | 20 | 2.4 | 0.024 | 80% |
| structural | encode/starlette | serena | 20 | 3.0 | 0.031 | 80% |
| structural | encode/starlette | serena-hint | 20 | 3.0 | 0.027 | 95% |
| structural | eza-community/eza | baseline | 20 | 3.8 | 0.035 | 90% |
| structural | eza-community/eza | duckgrep | 20 | 3.4 | 0.028 | 95% |
| structural | eza-community/eza | duckgrep-hint | 20 | 2.8 | 0.023 | 100% |
| structural | eza-community/eza | serena | 20 | 4.2 | 0.039 | 95% |
| structural | eza-community/eza | serena-hint | 20 | 4.7 | 0.039 | 95% |
| structural | marshmallow-code/marshmallow | baseline | 20 | 3.2 | 0.027 | 95% |
| structural | marshmallow-code/marshmallow | duckgrep | 20 | 2.1 | 0.020 | 95% |
| structural | marshmallow-code/marshmallow | duckgrep-hint | 20 | 2.5 | 0.026 | 95% |
| structural | marshmallow-code/marshmallow | serena | 20 | 3.4 | 0.033 | 95% |
| structural | marshmallow-code/marshmallow | serena-hint | 20 | 2.8 | 0.025 | 90% |
| structural | pallets/click | baseline | 20 | 3.0 | 0.026 | 100% |
| structural | pallets/click | duckgrep | 20 | 2.2 | 0.021 | 95% |
| structural | pallets/click | duckgrep-hint | 20 | 2.6 | 0.021 | 95% |
| structural | pallets/click | serena | 20 | 3.5 | 0.031 | 95% |
| structural | pallets/click | serena-hint | 20 | 3.1 | 0.025 | 90% |
| structural | pallets/flask | baseline | 20 | 3.0 | 0.027 | 90% |
| structural | pallets/flask | duckgrep | 20 | 2.1 | 0.020 | 100% |
| structural | pallets/flask | duckgrep-hint | 20 | 2.1 | 0.019 | 95% |
| structural | pallets/flask | serena | 20 | 3.1 | 0.030 | 100% |
| structural | pallets/flask | serena-hint | 20 | 3.2 | 0.030 | 95% |
| structural | pallets/jinja | baseline | 20 | 3.5 | 0.041 | 100% |
| structural | pallets/jinja | duckgrep | 20 | 2.2 | 0.022 | 100% |
| structural | pallets/jinja | duckgrep-hint | 20 | 2.4 | 0.021 | 100% |
| structural | pallets/jinja | serena | 20 | 3.4 | 0.030 | 100% |
| structural | pallets/jinja | serena-hint | 20 | 3.3 | 0.027 | 95% |
| structural | pallets/werkzeug | baseline | 20 | 3.7 | 0.036 | 100% |
| structural | pallets/werkzeug | duckgrep | 20 | 2.4 | 0.028 | 100% |
| structural | pallets/werkzeug | duckgrep-hint | 20 | 2.9 | 0.029 | 100% |
| structural | pallets/werkzeug | serena | 20 | 3.5 | 0.037 | 90% |
| structural | pallets/werkzeug | serena-hint | 20 | 3.7 | 0.038 | 100% |
| structural | psf/requests | baseline | 20 | 3.8 | 0.031 | 95% |
| structural | psf/requests | duckgrep | 20 | 2.1 | 0.023 | 100% |
| structural | psf/requests | duckgrep-hint | 20 | 2.0 | 0.020 | 100% |
| structural | psf/requests | serena | 20 | 3.1 | 0.029 | 100% |
| structural | psf/requests | serena-hint | 20 | 3.1 | 0.030 | 100% |
| structural | pytest-dev/pytest | baseline | 20 | 3.5 | 0.036 | 80% |
| structural | pytest-dev/pytest | duckgrep | 20 | 2.8 | 0.030 | 100% |
| structural | pytest-dev/pytest | duckgrep-hint | 20 | 2.5 | 0.023 | 100% |
| structural | pytest-dev/pytest | serena | 20 | 3.8 | 0.040 | 90% |
| structural | pytest-dev/pytest | serena-hint | 20 | 3.1 | 0.032 | 95% |
| structural | python-attrs/attrs | baseline | 18 | 3.6 | 0.030 | 100% |
| structural | python-attrs/attrs | duckgrep | 18 | 2.9 | 0.022 | 100% |
| structural | python-attrs/attrs | duckgrep-hint | 18 | 2.6 | 0.019 | 94% |
| structural | python-attrs/attrs | serena | 18 | 3.8 | 0.036 | 94% |
| structural | python-attrs/attrs | serena-hint | 18 | 2.7 | 0.024 | 100% |
| structural | sharkdp/bat | baseline | 20 | 3.9 | 0.032 | 100% |
| structural | sharkdp/bat | duckgrep | 20 | 3.2 | 0.031 | 100% |
| structural | sharkdp/bat | duckgrep-hint | 20 | 2.6 | 0.027 | 100% |
| structural | sharkdp/bat | serena | 20 | 3.6 | 0.034 | 100% |
| structural | sharkdp/bat | serena-hint | 20 | 4.2 | 0.039 | 95% |
| structural | sharkdp/fd | baseline | 20 | 3.5 | 0.030 | 100% |
| structural | sharkdp/fd | duckgrep | 20 | 2.6 | 0.022 | 100% |
| structural | sharkdp/fd | duckgrep-hint | 20 | 2.4 | 0.018 | 100% |
| structural | sharkdp/fd | serena | 20 | 3.1 | 0.033 | 100% |
| structural | sharkdp/fd | serena-hint | 20 | 3.9 | 0.032 | 95% |
| structural | sharkdp/hyperfine | baseline | 20 | 3.6 | 0.029 | 90% |
| structural | sharkdp/hyperfine | duckgrep | 20 | 2.8 | 0.026 | 90% |
| structural | sharkdp/hyperfine | duckgrep-hint | 20 | 2.5 | 0.022 | 100% |
| structural | sharkdp/hyperfine | serena | 20 | 3.8 | 0.033 | 90% |
| structural | sharkdp/hyperfine | serena-hint | 20 | 3.9 | 0.030 | 100% |
| structural | starship/starship | baseline | 20 | 3.6 | 0.033 | 95% |
| structural | starship/starship | duckgrep | 20 | 3.6 | 0.032 | 90% |
| structural | starship/starship | duckgrep-hint | 20 | 3.5 | 0.029 | 95% |
| structural | starship/starship | serena | 20 | 4.2 | 0.041 | 95% |
| structural | starship/starship | serena-hint | 20 | 3.6 | 0.030 | 95% |
| structural | Textualize/rich | baseline | 20 | 3.5 | 0.028 | 75% |
| structural | Textualize/rich | duckgrep | 20 | 2.5 | 0.026 | 90% |
| structural | Textualize/rich | duckgrep-hint | 20 | 2.6 | 0.025 | 90% |
| structural | Textualize/rich | serena | 20 | 3.0 | 0.029 | 80% |
| structural | Textualize/rich | serena-hint | 20 | 3.0 | 0.026 | 85% |
| structural | tokio-rs/tokio | baseline | 20 | 3.2 | 0.032 | 100% |
| structural | tokio-rs/tokio | duckgrep | 20 | 2.8 | 0.030 | 95% |
| structural | tokio-rs/tokio | duckgrep-hint | 20 | 2.5 | 0.025 | 100% |
| structural | tokio-rs/tokio | serena | 20 | 3.4 | 0.033 | 100% |
| structural | tokio-rs/tokio | serena-hint | 20 | 3.8 | 0.038 | 100% |
| structural | tqdm/tqdm | baseline | 19 | 2.9 | 0.026 | 100% |
| structural | tqdm/tqdm | duckgrep | 19 | 2.5 | 0.021 | 100% |
| structural | tqdm/tqdm | duckgrep-hint | 19 | 2.4 | 0.018 | 100% |
| structural | tqdm/tqdm | serena | 19 | 3.3 | 0.033 | 95% |
| structural | tqdm/tqdm | serena-hint | 19 | 3.3 | 0.028 | 100% |
| structural | XAMPPRocky/tokei | baseline | 20 | 3.9 | 0.036 | 100% |
| structural | XAMPPRocky/tokei | duckgrep | 20 | 3.5 | 0.033 | 100% |
| structural | XAMPPRocky/tokei | duckgrep-hint | 20 | 3.4 | 0.026 | 95% |
| structural | XAMPPRocky/tokei | serena | 20 | 4.5 | 0.042 | 100% |
| structural | XAMPPRocky/tokei | serena-hint | 20 | 4.5 | 0.039 | 95% |

### Variance

| setup | metric | within-task SD | between-task SD | within share of variance |
|---|---|---:|---:|---:|

### Setup costs (not included above)

| setup | worktrees | total seconds | total MB |
|---|---:|---:|---:|
| duckgrep | 255 | 4,972 | 10,696 |
| serena | 254 | 10,174 | 12,885 |
<!-- /eval:full -->
