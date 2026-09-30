# A/B evaluation harness: design

- **Date:** 2026-09-29
- **Status:** approved; implemented by `docs/plans/2026-09-29-ab-eval-harness.md`
- **Branch:** `eval/ab-harness`

## Goal

Measure, on real tasks, whether a Claude Code agent with duckgrep locates code with fewer tool calls, tokens and API round trips than the same agent with only built-in tools, and than one with Serena. It must be at least as accurate. This is the bar set when duckgrep was conceived: if it doesn't beat grep on turns-to-locate, the structure isn't earning its keep.

## Decisions made with the owner

| Topic | Decision |
|---|---|
| Tasks | Two kinds, reported separately: real issue localization (the headline) and structural questions (the targeted test). |
| Scale | Pilot first: 40 tasks × 3 setups × 2 repetitions = 240 runs. Then scale whatever shows a signal or is too noisy. |
| Billing | Run on the current Claude account now (it is on overage until 2026-10-02), capped at $1.50 per run. |
| Approach | A thin Python harness that drives the `claude` CLI. The Agent SDK was rejected: its token accounting differed from the CLI's in testing. Frameworks such as Inspect AI were rejected too, since they add indirection without solving isolation. |
| Setups | `baseline`, `duckgrep` and `serena`. Their prompts are identical, and there is no "use this tool" hint. |

These follow-ups were considered but left out of the pilot:
- a stock-macOS baseline (Bash and Read only);
- a hinted duckgrep setup;
- paraphrased issue prompts;
- questions on the owner's own repos;
- a model-graded quality judge.

## Setups and isolation

Every run uses the same model, CLI, built-in tools and prompt. The setups differ only in one MCP server and its allow entry.

| Setup | Extra MCP server | Allowed extra tools |
|---|---|---|
| `baseline` | none | none |
| `duckgrep` | `duckgrep mcp -C <task worktree>` from this repo's environment | `mcp__duckgrep__query` |
| `serena` | `serena-agent==1.7.0` via `uvx`, with context `eval-nav` | `mcp__serena__*` |

**Common settings:**
- Model `claude-sonnet-5-5` (the full ID, never an alias) with `--effort medium`. Each run records the CLI version (currently 2.1.285) and the resolved model.
- Built-in tools `--tools "Bash,Read,Grep,Glob"`:
  - Grep and Glob are named explicitly because the macOS default set lacks them.
  - There are no edit tools, since the tasks are locate-only.
  - There are no subagents or web fetch.

**The isolated command line.** The research phase verified it on this machine:

```
env -i HOME="$HOME" PATH="$PATH" USER="$USER" TMPDIR="$TMPDIR" \
    CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 ENABLE_TOOL_SEARCH=false \
  claude -p "$PROMPT" --model claude-sonnet-5-5 --effort medium \
    --output-format stream-json --verbose \
    --setting-sources "" --strict-mcp-config --disable-slash-commands \
    --tools "Bash,Read,Grep,Glob" --permission-mode dontAsk \
    --allowedTools "Bash,Read,Grep,Glob[,<setup's MCP allow>]" \
    [--mcp-config <setup's config>] \
    --no-session-persistence --max-turns 40 --max-budget-usd 1.50 \
  < /dev/null > run.jsonl
```

What each part does:
- `env -i` removes the parent session's variables. `USER` is required for keychain authentication.
- `--setting-sources ""` removes the owner's global `CLAUDE.md`, plugins, hooks, skills and project settings.
- `--strict-mcp-config` removes every MCP server except the setup's own.
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` stops auto-memory.
- `ENABLE_TOOL_SEARCH=false` loads MCP tools directly, so the MCP setups don't pay an extra ToolSearch round trip.
- `--no-session-persistence` keeps transcripts out of `~/.claude`.
- Two built-in plugins, `cc-plugin-agents-md` and `cc-plugin-telemetry`, cannot be removed. They are identical across setups.

**Serena's `eval-nav` context:**
- Fixed tools: `initial_instructions`, `get_symbols_overview`, `find_symbol`, `find_referencing_symbols`, `find_declaration`, `find_implementations`, `search_for_pattern`.
- `single_project: true` and `structured_tool_output: false`.
- `base_modes: []` and `default_modes: [no-memories]`.
- The web dashboard, browser auto-open and GUI log window are all off.
- Each run gets its own copy of a template `SERENA_HOME`, because Serena rewrites its config file.
- `project_serena_folder_location` points outside the repo, so the worktree stays clean.

**Configuration check:**
- Before each batch, `check` runs every setup's exact command without `USER`. Authentication fails before any model call, which costs nothing, but the startup event still lists the tools, MCP servers, plugins, skills and memory paths.
- Each real run asserts from its own startup event that:
  - the tools are exactly the built-ins plus the setup's tools;
  - every MCP server reports `connected`;
  - there are no hooks, no plugin skills and no memory paths.
- A run that fails the check is discarded and retried once.

## Tasks and answer keys

### Localization: 20 pilot tasks, the headline

**Python, 10 tasks,** from SWE-bench Lite:
- The source is the Hugging Face dataset `czlll/SWE-bench_Lite`: 274 instances whose `edit_functions` field lists `path.py:Class.method`. Prompts and base commits come from `princeton-nlp/SWE-bench_Lite`.
- Filters:
  - The fix edits one file and 1–3 functions.
  - Drop an issue if its text contains a gold file path or function name, which would make locating it trivial.
  - At most 3 tasks per repo.

**Rust, 10 tasks:**
- 5 from `SWE-bench/SWE-bench_Multilingual`. Its Rust set has 43 instances (tokio, bat, axum, nushell, coreutils, ripgrep; ruff is excluded as too large).
- 5 from SWE-bench-Live MultiLang Rust with `created_at >= 2026-06-01`. They are newer than the model's training data, so they serve as a contamination check.
- Both kinds must touch at most 3 `.rs` files.
- The answer key is derived from the fix patch:
  - Map each removed line to its enclosing `fn` with tree-sitter-rust. For a pure insertion, use the preceding context line.
  - Name impl methods `Type.method`.
  - Drop `tests` modules and `#[cfg(test)]` code.
  - Exclude fixes that touch more than 5 files or 10 functions.

**Answer-key rules for both languages:**
- Nested functions roll up to their outermost definition.
- An edit at class level or module level becomes a file-only key: the file must be named.

**Prompt:**
- The issue's `problem_statement`, verbatim (not its hints), followed by a fixed instruction: find the functions that must change to resolve the issue, do not edit files, and end with a fenced JSON block `{"locations": ["path:Qualified.name", ...]}`.
- Paths are relative to the repo root.
- Rust qualified names use `Type.method`; the scorer also accepts `Type::method`.

### Structural questions: 20 pilot tasks, the targeted test

The repos are pinned:
- Python: `psf/requests` v2.32.3 (`0e322af`) and pytest 8.3.4.
- Rust: ripgrep 14.1.1 and fd v10.2.0.

The pilot has 10 Python and 10 Rust questions of three kinds:
- **Callers:** "List every function that calls `X`." Answer: functions.
- **Two-hop:** "Which functions call `X` directly or through one intermediate function?" Answer: functions.
- **Importers:** "Which files import `X`?" Answer: files.

**Answer keys:**
- Python: `jedi.Script.get_references(scope="project")`, with each reference mapped to its enclosing function.
- Rust: rust-analyzer's `scip` output, reading references and their enclosing symbols. The harness uses a pinned standalone rust-analyzer binary and never touches the owner's rustup toolchain.
- Every key is cross-checked against `git grep -w`: each gold reference must appear among grep's hits. Grep may report extra hits for same-named symbols. A question with a gold reference grep can't find is dropped.
- About a third of the questions deliberately use common names (`get`, `new`, `run`), where grep over-reports.

The answer format is the same JSON block: `path:Qualified.name` entries for function questions and bare `path` entries for file questions.

### Suites

A suite is a committed JSONL file in `bench/eval/suites/`. Each line holds:
- `id`, `kind` (`localization` or `structural`), `lang`, `repo` and `commit`;
- `prompt` and `gold`;
- `source`: the dataset, or the generator and its pinned tool versions.

Pilot tasks are drawn from the filtered pools with a fixed seed, stratified by repo. The same builders produce the pilot suite and, later, the full suites (about 80 Python and 40 Rust localization tasks, and 40 structural questions).

## Execution

**Preparation** is not timed; its cost is recorded separately:
- One partial clone per repo, and a detached `git worktree` per task at its commit, under `~/.cache/duckgrep-eval` (configurable, never inside `~/git`).
- `duckgrep -C <worktree> index` once per worktree, recording build time and index size.
- `serena project index <worktree>` once per worktree, recording its time.

**Running:**
- Every (task, setup, repetition) triple is shuffled with a fixed seed, so no setup systematically benefits from a warm prompt cache.
- Three runs execute in parallel.
- Results go to `bench/eval/runs/<suite>/results.jsonl`, keyed by task, setup and repetition. A restart skips finished runs.

**Guards:**
- `--max-turns 40` and `--max-budget-usd 1.50`, plus a 15-minute wall-clock kill.
- A capped or killed run counts as a failure and is flagged.
- Authentication failures are caught through `is_error` and `terminal_reason`, never through `subtype` alone, because a failed login still reports `subtype: success`.

## Per-run record and metrics

The raw stream is saved as `runs/<suite>/<task>/<setup>-<rep>.jsonl.gz`, alongside one results line with these fields:

**Identity:** task, setup, repetition, seed, model (resolved), CLI version, and the startup check's verdict.

**Outcome:**
- `terminal_reason`, `is_error` and `permission_denials`;
- the parsed answer (the last fenced JSON block);
- the score.

**Tool calls:**
- `tool_use` blocks counted by name, deduplicated by block ID, excluding denied calls.
- Also split into families: built-in search (Grep, Glob, Bash) versus Read versus the setup's MCP tools.

**API round trips:** the number of distinct assistant message IDs. Claude Code's `num_turns` counts tool calls + 1, so it is kept but not used as a metric.

**Tokens:** `input`, `cache_creation` (5-minute and 1-hour), `cache_read` and `output` (which includes thinking), taken from the result's `usage` and `modelUsage`, never summed per message.

**Cost:** computed from the token classes at fixed Sonnet 5.5 list rates. The rates were checked by reconstructing `total_cost_usd` exactly:

| Token class | Rate |
|---|---|
| input | $2 / M |
| output | $10 / M |
| cache read | $0.20 / M |
| 5-minute cache write | $2.50 / M |
| 1-hour cache write | $4.00 / M |

Claude Code's own `total_cost_usd` is recorded but not used, because it swings with cache warmth.

**Time:** wall time, plus per-tool latency from event timestamps.

**Adoption:** whether the run called its setup's MCP tool, and that tool's share of all calls.

**Turns-to-locate:** the index of the first round trip whose tool results contain a gold location. A match is the gold file together with the gold function's name, or the gold path alone for file keys.

Amended 2026-09-30, after the pilot: the gold function's name counts only where it identifies the function. That means its definition line, by line number in its file (or, in output without line numbers, as a definition in a call that names its file), or its qualified name in its file from duckgrep or Serena. A call, a docstring or a same-named token does not count. The pilot's transcripts showed the looser rule firing early on such mentions and missing duckgrep's escaped rows. The implementation is `bench/eval/locate.py`.

## Scoring

**Localization:**
- Success means every gold function was named. This is Agentless's superset rule.
- Function-level precision, recall and F1.
- File-level hit rate.

**Structural questions:** precision, recall and F1 of the listed functions or files.

**Normalisation:**
- Paths are made relative to the repo root.
- `::` is treated as `.`.
- Trait qualifiers such as `<T as Trait>::` are dropped.
- Names compare case-sensitively.
- A missing or unparsable answer scores 0 and is flagged.

## Analysis

The report is a markdown section written into `bench/RESULTS.md`.

**Method:**
- The unit of analysis is the task. Repetitions are averaged per setup and task.
- Each setup is compared per task against `baseline`, on:
  - the log of tool calls, tokens and cost;
  - round trips and turns-to-locate;
  - success and F1.
- 95% bootstrap confidence intervals over tasks (10,000 resamples).
- Wilcoxon signed-rank p-values with Holm correction across the two contrasts × metrics × tables.
- The per-task win rate.

**Tables:** Python localization, Rust localization, Python structural and Rust structural. Plus breakdowns by adoption, by repo, and for the post-cutoff Rust subset.

**Variance:** the pilot estimates run-to-run and task-to-task variance, which sets the repetitions and task counts for the full run. If task-to-task variance dominates, two or three repetitions are enough.

**Setup costs** (index build time and size, Serena warm-up) are reported next to the per-run numbers. They are not added in.

## Code structure

This is a new package, `bench/eval/`, run with `uv run --group eval python -m bench.eval <command>`.

| Module | Responsibility |
|---|---|
| `tasks/localization.py` | Load the datasets, apply the filters, derive the Rust answer keys, select the pilot, write the suite |
| `tasks/structural.py` | Generate the questions and their answer keys with jedi and rust-analyzer SCIP; cross-check with git grep; write the suite |
| `workspace.py` | The repo cache, per-task worktrees, duckgrep index builds, Serena warm-up, and the pinned rust-analyzer download |
| `setups.py` | The three setups: tool lists, MCP config files, allow lists, the scrubbed environment and the command line |
| `runner.py` | Shuffled scheduling, parallelism, spending and time caps, resuming, raw-stream capture |
| `stream.py` | Turn a stream-json file into the per-run record |
| `score.py` | Parse answers, normalise names, score |
| `report.py` | Paired statistics and the markdown report |
| `__main__.py` | The commands `build`, `prepare`, `check`, `run` and `report` |

A new `eval` dependency group holds datasets, jedi, unidiff, numpy and scipy, so ordinary duckgrep installs don't carry them. Raw runs go under the gitignored `bench/eval/runs/`.

## Testing

These tests run in the normal pytest suite and never call a model:
- `stream.py` against stream-json fixtures recorded in the research runs: tool counts, round trips, token classes, permission denials, a run that hit its turn cap, and an authentication failure.
- `score.py`: normalisation and scoring edge cases.
- Answer-key derivation on known instances: Python keys against `czlll` `edit_functions`, and a Rust patch with a test module and an impl method.
- `setups.py`: the generated command line and MCP configs must equal the verified recipe exactly.

Before the pilot, run these by hand, in order:
1. `check` for all setups, which is free.
2. A smoke run of 2 tasks × 3 setups × 1 repetition, costing about $1.

## Delivery

1. **PR 1:** the harness, its tests and the pilot suites.
2. **The pilot:** 240 runs, estimated at $50–150.
3. **PR 2:** the pilot report in `bench/RESULTS.md`, and the parameters for the full run.

## Risks and mitigations

- **Contamination.** SWE-bench Python issues are in the model's training data. That shrinks differences between all setups but leaves the paired comparison valid. Results are reported per source, and the post-cutoff Rust subset is the check.
- **Adoption versus capability.** An agent may ignore an available tool. Adoption is reported separately from effectiveness; a hinted setup is the follow-up experiment.
- **Cache and cost noise.** Cost is computed from token classes at fixed rates, and run order is shuffled. Tokens are heavy-tailed, so comparisons use log transforms and bootstrap intervals.
- **MCP overhead.** The per-turn schema tokens and Serena's `initial_instructions` call count against their setup, because they are part of that setup's real cost.
- **Serena's LSP limits.** Its language server starts fresh on every run (2–8 s, affecting wall time only). Pyright runs without the repos' dependencies installed, which limits its recall. These are properties of the tool as deployed, and they are reported.
- **Drift.** The CLI version, model ID, Serena and its language-server versions, and the dataset revisions are pinned and recorded.
- **Machine load.** Wall time is a secondary metric. Tool calls, tokens, round trips and accuracy are the primary ones.

## Out of scope

- Resolving issues (editing code and running tests).
- TypeScript, Go and other languages.
- The follow-ups listed under "Decisions made with the owner".
