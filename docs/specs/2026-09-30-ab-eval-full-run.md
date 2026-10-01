# A/B evaluation, full run: design

- **Date:** 2026-09-30
- **Status:** requested by the owner after the pilot ("move to hundreds of tasks to be able to measure savings; add hinting so that we actually measure localization; do the suite cleanup")
- **Builds on:** `docs/specs/2026-09-29-ab-eval-harness-design.md` (the harness) and the pilot's findings in `bench/RESULTS.md`

## Goal

Measure duckgrep's savings with enough tasks to resolve them, and measure localization with the tools actually in use.

The pilot showed three things:
- On structural questions, detecting the savings needs about 22–51 tasks per table for round trips and 150–235 for tokens.
- Under a neutral prompt, no agent used either tool on localization.
- Some answer keys demanded more than their issue asked for.

## Suite `full`

Built with `python -m bench.eval --suite full build`. Each choice below is a suite profile in `config.py`, so the pilot suite still rebuilds exactly as it was.

### Localization

- **Python.** Every SWE-bench Lite task passing the pilot's filters. There is no per-repo cap.
  - The filters: 1–3 functions in one file, a key that agrees with `edit_functions`, and an issue that doesn't name the answer.
  - About 164 tasks. django (about 62) and sympy (about 41) dominate, which the by-repo table shows.
- **Rust.** Every task from SWE-bench Multilingual and SWE-bench-Live that passes the filters, from every date.
  - The filters: at most 3 `.rs` files and 5 files in the patch, 1–3 functions, the excluded repos left out, and no leak.
  - About 75 tasks, in three strata:
    - `multilingual`;
    - `live-earlier`: Live issues before 2026-06-01, which the model may have seen;
    - `live`: Live issues from 2026-06-01 on.
  - The report keeps its post-cutoff table, on `live`.

### Key cleanup

These rules close the pilot audit's findings:
- **Visibility-only edits are not the fix.** A hunk whose only change is a Rust visibility modifier (`fn` ↔ `pub fn` ↔ `pub(crate) fn`) no longer makes its function a key. In pixi-6335, two of five key entries were such hunks.
  - Only the parser's modifiers count. `pub` inside a string or a macro's tokens (`quote!`) is text the function produces.
  - Once the modifiers are removed, the paired lines must match exactly. A spacing change inside a string still marks its function.
  - The rest of the block still counts: after `fn` → `pub fn`, a new body line marks the function.
- **At most 3 functions.** This matches Python's bound. Bigger fixes more often bundle work the issue doesn't ask for.
- **Curation of localization keys.** Before the run, every localization key is audited against its issue and patch. That's both languages: the Rust findings were mostly generic incidental edits.
  - A task is dropped when its fix bundles work the issue gives no reason for: an unrelated feature, a refactor, or a docs or style cleanup in the same pull request. pixi-6335 was the pilot's example.
  - A task is kept when the extra entries are part of the fix itself, such as plumbing for the chosen implementation or a parallel code path. In doubt, it's kept.
  - An auditor proposes each drop and an independent verifier confirms it.
  - Dropped ids and their reasons go in `bench/eval/suites/full-curation.jsonl`, which the builder applies.
  - Near-duplicates count once. Tasks of one repo whose fixes add an entry to the same registry, for the same kind of request, keep the first: harper's three phrase corrections share one `lint_group`, and two share a base commit.
  - The build refuses a curation file that names a task it didn't draw, such as a typo or a task a filter already rejects.
  - Result: 12 tasks are dropped.
    - The audit dropped 8 of 78 Rust tasks and 1 of 164 Python tasks, and all nine proposed drops were confirmed.
    - The independent review of this suite found one more incidental edit, in scikit-learn-15512: `predict` only gains an input check.
    - It also found the harper near-duplicates.
- **Recursion is stated.** Structural questions say that a function calling itself counts as its own caller ("including test functions and, if it calls itself, the function itself"). Keys already include self-recursion. In the pilot, one run left it out on purpose.
- **No duplicate questions.** A repo's question whose key equals an earlier question's key is dropped. The pilot's two requests two-hop questions had the same answer. A target also gets one question only, a callers or a two-hop one; 21 targets had both.
- **No question dispatch can answer differently.** Keys follow static resolution, so a target that dispatch can reach from a call resolved elsewhere isn't asked about.
  - Python:
    - a call that resolves to a base class's method of that name, such as `self.run()` in the base or `m.keys()` on a `typing.Mapping`;
    - a call whose receiver is somewhere assigned an instance of the target's class, or of a subclass that inherits the method. jedi types pytest's `rewrite_hook` by its first assignment, so two callers of `AssertionRewritingHook.mark_rewrite` were missing.
  - Rust: a call through a generic or `dyn`, which resolves to the trait's method, when the target implements that method.
  - A blunter rule would drop any target whose name some other class's call resolves to. It would have cut 60 of 189 Python callers and two-hop questions, most of them common-name ones. These rules keep a correctly typed call to a same-named method.
- **Python keys get a reference check.** Rust keys already require every reference of the target to be a counted call site. Python keys now do too, through jedi's references. A target that's also used as a value (a callback, `map(f, …)`) makes a "who calls it" question ambiguous, so the question is dropped.

### Suite as built

- **Localization: 230 tasks.**
  - Python: 162.
  - Rust: 68, in three strata: `multilingual` 15, `live-earlier` 32 and `live` 21.
- **Structural questions: 494.**
  - Python: 237, from 12 repos.
  - Rust: 257, from 13 repos.
  - Most repos yielded the maximum of 20 questions.
  - One question per target and the dispatch rules replaced 54 questions with other targets'. The counts are unchanged.
- **Total: 724 tasks.**

### Structural questions

- **Repos:** 12 Python and 13 Rust, each pinned to a release commit in `config.STRUCTURAL_REPOS_FULL`.
  - **Python:** requests, pytest, flask, click, jinja, werkzeug, rich, httpx, attrs, starlette, marshmallow, tqdm.
  - **Rust:** ripgrep, fd, bat, hyperfine, just, zoxide, delta, tokei, alacritty, starship, eza, clap, tokio.
- **Questions per repo:** up to 8 callers questions (half on common names), 8 two-hop and 4 importers, with the pilot's completeness rules.
- **Target:** about 200 questions per language. The builder reports each repo's yield. If a language falls short, repos are added.

## Setups

There are five. The three from the pilot are unchanged. Two new ones append a hint to Claude Code's system prompt (`--append-system-prompt`) and are otherwise identical to their base setup.

| Setup | Base | Hint |
|---|---|---|
| `duckgrep-hint` | `duckgrep` | "The repository in the current directory is indexed by duckgrep. Its query tool answers questions about the code in one SQL query: where a symbol is defined, who calls it, what it calls, what imports a module, and text search that names the enclosing function. Use it first to find code, and read files once you know where to look." |
| `serena-hint` | `serena` | "Serena's tools navigate the repository in the current directory by symbol: find_symbol finds where a symbol is defined, find_referencing_symbols finds who uses it, get_symbols_overview lists what a file defines, and search_for_pattern searches text. Use them first to find code, and read files once you know where to look." |

- **Parity:** the hints are parallel in form and length. They name the tool's capabilities and ask for it to be used first. Neither names the evaluation or anything about the task.
- **Prompts:** the task prompt stays identical across all five setups.
- **Worktrees:** a hinted setup shares its base setup's worktree group, so the paths the agent sees still don't name a setup.

## Runs

- **Volume:** 2 repetitions of every task in every setup: 724 tasks, 5 setups and 7,240 runs.
- **Parallelism:** 8 runs at once. The machine has 64 GB of RAM and 16 cores; the pilot ran 3.
- **Spending cap:** the batch stops starting runs at $400. The pilot's rate of $0.031 per run projects about $225. The cap leaves room for longer runs on the Live tasks and in the hinted setups.
- **Cache:** `DUCKGREP_EVAL_CACHE=/Volumes/research/code-tasks`, an external SSD. The prepared full suite takes 78 GB, and the main disk has 18 GB free. Paths the agent sees contain "research" in every setup alike.
- **Robustness for a multi-hour batch:**
  - **API errors** are sorted by Claude Code's error categories:
    - Login, account, billing, model and credential errors stop the batch at once.
    - Rate limits, overload, server errors and unknown errors are retried up to 3 times, after waits of 1, 5 and 15 minutes. They stop the batch only if they persist.
    - A request too long even after compaction, or output cut off after the CLI's own recovery, is the run's own outcome: it's scored like any failed run.
    - Ten recorded runs in a row that end on an API error stop the batch. None of the streak is kept, because it is something else failing, such as a CLI or API change. Its runs, and those of it still in flight, are charged as unrecorded and redone on resume.
    - Without an error value, the result's text is read for login, API-key, credit and permission failures.
  - **Spend:**
    - A retry stops at the spending cap like any new run.
    - Every attempt that isn't recorded is charged: an API error, a discarded attempt, an interrupted run, or a crashed `claude`, which is retried rather than scored.
    - Its transcript is kept as `<setup>-<rep>.unrecorded-<n>.jsonl.gz`.
  - **Claude Code version:**
    - Every run sets `DISABLE_AUTOUPDATER=1`.
    - The batch runs the binary the `claude` link resolves to, because the installer moved the link to 2.1.286 during this work.
    - A resume on another version is refused before any paid run, naming the `--claude` path that pins the batch's version.
    - A run whose recorded version differs from the batch's first stops the batch.
  - **Starting:**
    - A preflight refuses a batch whose scheduled worktrees, duckgrep indexes or Serena projects are missing.
    - A lock refuses a second `run` on the same results.
    - A last results line cut off mid-write is removed and charged at the per-run cap.
  - **The cache's volume:**
    - macOS's privacy service stalled three times during this work. Each time, the external volume refused every access for a minute or two.
    - A watchdog probes the volume every second. A probe that fails, or that hangs for 10 s, is an outage. The batch waits an outage out, and a run that overlapped one is charged, set aside and redone, never recorded. An outage of 15 minutes stops the batch.
    - The suite build is redone if an outage overlapped it: one had silently cost 73 structural questions.
  - **Resources:**
    - Serena runs on the same Rust repo never overlap, because they share a `CARGO_TARGET_DIR`.
    - At most three rust-analyzers run at once, across runs and `prepare`'s warm-ups.
  - **`prepare`:**
    - It works on several repo/commit pairs at once. Clones, fetches and Rust warm-ups are serialised per repo.
    - A failure in one pair doesn't lose the others' rows, and Ctrl-C waits for the pairs in flight.
    - A worktree whose attributes renormalise a file (dioxus) is checked out byte for byte.

## Analysis

- **Comparisons:** every non-baseline setup against `baseline`, per table, with the pilot's statistics. The Holm family covers every comparison in the report.
- **Read first:** these contrasts answer the owner's questions, so the findings put them first:
  - `duckgrep` and `duckgrep-hint` against `baseline`, on tokens, tool calls, round trips, turns to locate and success, in each table;
  - `serena-hint` against `baseline`, on the same;
  - the hint's effect, read from the adoption table (use with and without the hint).
- **Strata:** the post-cutoff table covers the `live` stratum only.
- **Audit:** the accuracy audit of failed runs is repeated on a sample, since failures will number in the hundreds.
