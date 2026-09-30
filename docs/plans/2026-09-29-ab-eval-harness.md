# A/B evaluation harness: implementation plan

**Goal:** Build `bench/eval/`, which runs Claude Code on real code-localization tasks and on structural questions under three setups (built-in tools only, plus duckgrep, plus Serena). It reports paired, per-task differences in tool calls, tokens, cost, round trips and accuracy. Then build the pilot suites: 40 tasks, for the 240-run pilot.

**Architecture:** A thin Python package drives the `claude` CLI in a scrubbed environment and reads its stream-json output.
- Task builders draw tasks from pinned datasets and pinned repos. They derive each answer key from the fix patch (localization) or from static analysis (structural: jedi for Python, rust-analyzer's SCIP index for Rust), and write committed JSONL suites.
- `prepare` makes one worktree per task commit and setup, builds the duckgrep index, and warms Serena up.
- `run` executes the shuffled schedule three runs at a time, then scores and restores each run's worktree.
- `report` computes paired statistics.

**Tech stack:**
- Python 3.12, uv and pytest.
- pyarrow (already a dependency) reads the datasets, and unidiff reads the patches.
- tree-sitter-rust (already a dependency), jedi 0.20, and protobuf 7 with a vendored `scip_pb2.py`.
- numpy and scipy for the statistics.
- The tools under test: the `claude` CLI 2.1.285, `serena-agent==1.7.0` through `uvx`, and rust-analyzer release `2026-09-28`.

**Spec:** `docs/specs/2026-09-29-ab-eval-harness-design.md`. Read it with this plan; the plan implements it, with the refinements listed below.

## How this plan was checked

Every module was prototyped and run in a scratch directory before this plan was written. The code below is that prototype, formatted and linted with this repo's ruff settings, with its 129 tests passing (plus one opt-in network test). The prototype established:

- **Python answer keys** derived from the fix patch equal czlll's `edit_functions`, under the rules below, for all 274 SWE-bench Lite instances.
- **Pool sizes** after every filter: 164 Python tasks, 21 Rust tasks from SWE-bench Multilingual and 24 post-cutoff Rust tasks from SWE-bench-Live. Task 13 lists the pilot drawn with seed 20260929.
- **The structural generator** makes the 20 pilot questions in 14 s, once rust-analyzer's SCIP indexes exist (about a minute per repo to build).
- **rust-analyzer's SCIP index** resolves 1,800 of fd's 1,863 call sites, at the exact positions tree-sitter gives. All 63 others are behind `#[cfg(windows)]` or other cfgs inactive on macOS.
- **`check` works end to end** with the real CLI, and nothing is billed. Without `USER`, each setup lists exactly its intended tools, and both MCP servers connect, for Python (requests) and for Rust (fd).
- **The cost formula** reproduces Claude Code's own `total_cost_usd` exactly on all four recorded runs, at Haiku's rates.

## Refinements to the spec

Each of these follows from something the prototype measured.

1. **Datasets without the `datasets` library.**
   - Each dataset is one parquet file, fetched by URL at a pinned revision, checked against a pinned SHA-256, and read with pyarrow.
   - So the `eval` dependency group is jedi, unidiff, numpy, scipy and protobuf.
   - czlll's `problem_statement`, `base_commit` and `patch` equal princeton-nlp's for all 274 instances, so only czlll's file is read.
2. **Python keys are derived, then cross-checked.**
   - Python keys come from the fix patch, under the same rules as Rust.
   - A task is kept only when that key equals czlll's `edit_functions` under those rules. A listed class counts as a file-only key, and `Class.__init__` counts as the class.
   - This applies the spec's "Python keys against czlll `edit_functions`" check to every task, not only in tests.
3. **Key rules made precise:**
   - Hunks are located by their context, as `git apply` does, because SWE-bench's line numbers are often a few lines off.
   - Import statements (multi-line ones included), comments and blank lines are ignored, as LocAgent does.
   - Inserted code belongs to the function above it only if it is indented deeper than the `def` (Python) or lands before the closing brace (Rust).
   - A file-only key is dropped when the same file has a function key, since naming the function names the file.
4. **Seven Rust repos are excluded as too large.** Besides ruff, which the spec already excludes, these are biome, pnpm, lean-ctx, ironclaw, polars and servo: over 2,000 `.rs` files or 50,000 files.
5. **Structural keys must be complete.**
   - A question is kept only if every call site of the target's name resolves, to the target or to something else. The resolver is jedi's `goto` for Python and a SCIP occurrence for Rust.
   - Otherwise a caller the analyzer can't see (dynamic dispatch, code behind an inactive cfg) would count against a correct answer.
   - jedi searches the repo's own code and the standard library only, never an installed copy of the package.
   - Importers come from `ast` for Python, and from SCIP occurrences inside `use` declarations for Rust.
   - Questions say "including test functions" or "including test files", since the keys include them.
6. **Full clones, not partial ones.** `duckgrep index` reads `git log --numstat` over 5,000 commits. In a blobless clone each old blob is a separate network fetch, and indexing never finished.
7. **Nothing the agent can see names a setup.**
   - The agent sees its working directory, so worktrees live under `wt/t1`, `wt/t2` and `wt/t3`.
   - The default cache is `~/.cache/code-tasks`, and Serena's context description is neutral.
   - Each setup has its own worktree, so no run sees files another setup's tools wrote: `.duckgrep/`, Serena's data, cargo output.
8. **Serena and rust-analyzer details:**
   - The Serena warm-up pins the project language (`serena project index --language`). Auto-detection otherwise asks a question on stdin when a repo has files in a second language, as fd does with bash.
   - Everything that runs cargo (Serena's rust-analyzer and SCIP generation) gets `CARGO_HOME` and `CARGO_TARGET_DIR` inside the cache and `RUSTUP_TOOLCHAIN=stable`. Worktrees stay clean, and rustup never installs anything.
   - The rust-analyzer release tag is `2026-09-28`; the binary itself reports 2026-09-27.
9. **Rust test functions.** Keys name functions without inline module names (`it_works`, not `tests::it_works`), and the scorer accepts either spelling.
10. **Batch guards.** `run` stops starting new runs:
    - when Claude Code's billed total reaches `--max-total-usd` (default $200);
    - when free disk under the cache falls below 10 GB;
    - on an infrastructure error (login, rate limit);
    - on any harness fault.

    Every stop is resumable.
11. **`check` probes one task per language** for each setup.

## Global constraints

- Work on the branch `eval/ab-harness`. Commit through the pre-commit hook; never use `--no-verify`, and never commit on `main`.
- Every commit message ends with the two attribution lines shown in the commit steps.
- **Run settings, the same for every setup:**
  - model `claude-sonnet-5-5` (the full ID) with `--effort medium`;
  - `--tools "Bash,Read,Grep,Glob"`, `--max-turns 40` and `--max-budget-usd 1.50`;
  - a 15-minute wall-clock kill, 3 runs in parallel, 2 repetitions, seed `20260929`.
- Serena is `serena-agent==1.7.0` with the `eval-nav` context and its seven tools.
- Prompts are identical across setups and never name a tool.
- Tests never call a model. Only the opt-in `DUCKGREP_EVAL_NETWORK=1` test uses the network.
- **Cost** is computed at fixed Sonnet 5.5 rates:

  | Token class | Rate |
  |---|---|
  | input | $2 / M |
  | 5-minute cache write | $2.50 / M |
  | 1-hour cache write | $4 / M |
  | cache read | $0.20 / M |
  | output | $10 / M |

- **Statistics:** pairs at the task level, 10,000 bootstrap resamples, and Wilcoxon signed-rank tests with Holm correction across every comparison.
- The cache is never inside `~/git`. On this machine use `DUCKGREP_EVAL_CACHE=/Volumes/research/code-tasks`, because the main disk has about 16 GB free.
- Code must work on Python 3.10 (the repo's `requires-python`) and pass ruff (line length 120, the repo's rules). Comment as densely as `src/duckgrep` does.

## Review focus

These are the five failure modes most likely to bite that the spec implies but does not spell out. Each has a test in the task that owns the code.

1. **The disk fills during a batch** as Serena's rust-analyzer builds grow the cache. The batch should stop before its next run, with a message naming the cache. Test in Task 10.
2. **A run outlives the 15-minute limit** with MCP and language servers under it. The whole process group should die, and the run is recorded as killed. Test in Task 10.
3. **A harness fault mid-batch** (a missing worktree, a git failure). The batch should stop, rather than record a wrong result or silently lose a worker. Test in Task 10.
4. **A task commit the clone lacks.** SWE-bench-Live bases can be newer than the clone. The commit should be fetched by its SHA. Test in Task 8.
5. **Answers written the way agents write them.** Absolute paths from Read, `./` prefixes, `Type::method`, `<T as Trait>::m`, `impl T/m`, `tests::name` and trailing `()` should all be normalised. Tests in Task 2.

## Execution: roles, models and effort

| Role | Who | Model | Effort |
|---|---|---|---|
| Implement Tasks 1–12 | this session (native execution) | Opus 5.5 | session |
| *Alternative:* implement Tasks 1–12 | one `builder` agent per task, in the waves below | Sonnet 5.5 | high |
| Build and inspect the pilot suites (Task 13) | this session | Opus 5.5 | session |
| Review the whole branch before the PR (Task 14) | one fresh `builder` agent, told to review only | Opus 5.5 | high |
| The PR loop (checks, bot comments, merge) | this session | Opus 5.5 | session |

The code in this plan already passes its tests, so the implementer's job is mostly to place it in TDD order and verify it. Native execution is the cheaper choice. If agents do the work, they run in these waves:
- **Wave 1:** Task 1.
- **Wave 2:** Tasks 2, 3, 4, 5, 6 and 11.
- **Wave 3:** Tasks 7 and 8.
- **Wave 4:** Tasks 9 and 10.
- **Wave 5:** Task 12.

Each agent works in its own worktree, and the waves are merged in order.

## File map

| File | Responsibility |
|---|---|
| `bench/eval/config.py` | Every pinned parameter: model, caps, rates, datasets, repos, tool versions, paths |
| `bench/eval/suite.py` | The `Task` record; suite JSONL load and save |
| `bench/eval/score.py` | Parse the final JSON answer, normalise names, score against the key |
| `bench/eval/stream.py` | Turn a stream-json transcript into metrics, config checks and turns-to-locate |
| `bench/eval/setups.py` | The three setups: argv, scrubbed environment, MCP configs, Serena home, cargo environment |
| `bench/eval/serena/eval-nav.yml`, `serena_config.yml` | Serena's context and settings, rendered into each run's SERENA_HOME |
| `bench/eval/gold.py` | Answer keys from fix patches (Python ast, Rust tree-sitter) |
| `bench/eval/datasets.py` | Pinned parquet downloads, checked by SHA-256; single files at a commit |
| `bench/eval/prompts.py` | The exact prompt text |
| `bench/eval/tasks/localization.py` | Pools, filters, leak check and repo-stratified selection of issue tasks |
| `bench/eval/tasks/structural.py` | Callers, two-hop and importer questions from jedi and SCIP |
| `bench/eval/scip_pb2.py` | Vendored protobuf code for SCIP (generated, not linted) |
| `bench/eval/workspace.py` | Clones, per-setup worktrees, restore, disk guard, duckgrep index, Serena warm-up, rust-analyzer |
| `bench/eval/runner.py` | Schedule, the free probe, one run, the resumable batch |
| `bench/eval/report.py` | Paired statistics and the markdown report |
| `bench/eval/__main__.py` | `build`, `prepare`, `check`, `run`, `report` |
| `bench/eval/suites/pilot-*.jsonl` | The committed pilot suites |
| `tests/test_eval_*.py`, `tests/eval_helpers.py`, `tests/eval_runs/*.jsonl` | Tests, a repo helper and recorded streams |

---
### Task 1: Package skeleton, dependencies, pinned parameters and suites

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (by uv), `.pre-commit-config.yaml`, `.gitignore`
- Create: `bench/__init__.py`, `bench/eval/__init__.py`, `bench/eval/tasks/__init__.py`, `bench/eval/config.py`, `bench/eval/suite.py`
- Test: `tests/test_eval_suite.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Constants in `bench.eval.config`:
    - run settings: `MODEL`, `EFFORT`, `BUILTIN_TOOLS`, `MAX_TURNS`, `MAX_BUDGET_USD`, `WALL_LIMIT_S`, `PARALLEL`, `REPETITIONS`, `SEED`, `RATES` (keys `input`, `cache_write_5m`, `cache_write_1h`, `cache_read`, `output`);
    - tools: `SERENA`, `SERENA_TOOLS`, `RUST_ANALYZER_URL`, `RUST_ANALYZER_GZ_SHA256`, `RUST_ANALYZER_VERSION`;
    - data: `Dataset(repo, revision, path, sha256)` with `.url` and `.label`, `DATASETS` (keys `lite`, `multilingual`, `live`), `LIVE_SINCE`, `EXCLUDED_RUST_REPOS`, `PinnedRepo(repo, lang, tag, commit)`, `STRUCTURAL_REPOS`;
    - paths: `EVAL_DIR`, `SUITES_DIR`, `RUNS_DIR`, `RESULTS_MD`, `MIN_FREE_GB`, `cache_dir() -> Path`.
  - In `bench.eval.suite`: `Task(id, kind, lang, repo, commit, prompt, gold, source, answer="functions", stratum="")`, a frozen dataclass whose `gold` is a tuple; `load(path) -> list[Task]`; `save(path, tasks)`; `suite_files(name, suites_dir) -> list[Path]`; `load_suite(name, suites_dir) -> list[Task]`.

- [ ] **Step 1: Add the `eval` dependency group and settings**

Run:

```bash
uv add --group eval "jedi>=0.20.0" "numpy>=2.0" "protobuf>=7.35.1,<8" "scipy>=1.13" "unidiff>=1.0"
```

Then edit `pyproject.toml` so these sections read exactly:

```toml
[dependency-groups]
bench = [
    "jedi>=0.20.0",
]
dev = [
    "pre-commit>=4.6.2",
    "pytest>=9.1.1",
    "ruff>=0.16.9",
]
eval = [
    "jedi>=0.20.0",
    "numpy>=2.0",
    "protobuf>=7.35.1,<8",  # bench/eval/scip_pb2.py was generated for protobuf 7.35.1
    "scipy>=1.13",
    "unidiff>=1.0",
]

[tool.uv]
default-groups = ["dev", "eval"]  # the eval harness's tests run in the normal suite

[tool.ruff]
line-length = 120
extend-exclude = ["tests/fixture", "bench/eval/scip_pb2.py"]  # test data; generated protobuf code

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]  # tests import bench.eval
```

Keep the `dev` entries as they are if uv has since bumped them. Run `uv sync`; it must finish without errors.

- [ ] **Step 2: Run the hook's tests on bench/ too, and ignore raw runs**

In `.pre-commit-config.yaml`, change the pytest hook's `files` line to:

```yaml
        files: ^(src/|tests/|bench/|pyproject\.toml$|uv\.lock$)
```

Append to `.gitignore`:

```
bench/eval/runs/
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_eval_suite.py`:

````python
import pytest

from bench.eval.suite import Task, load, load_suite, save


def task(**kw):
    base = dict(
        id="t1",
        kind="localization",
        lang="python",
        repo="o/r",
        commit="c" * 40,
        prompt="p",
        gold=("a.py:f",),
        source="s",
    )
    return Task(**{**base, **kw})


def test_round_trip(tmp_path):
    tasks = [task(), task(id="t2", kind="structural", answer="files", gold=["a.py", "b.py"], stratum="importers")]
    save(tmp_path / "s.jsonl", tasks)
    assert load(tmp_path / "s.jsonl") == tasks
    assert load(tmp_path / "s.jsonl")[1].gold == ("a.py", "b.py")


def test_duplicate_ids_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        save(tmp_path / "s.jsonl", [task(), task()])


@pytest.mark.parametrize("bad", [{"kind": "other"}, {"lang": "go"}, {"answer": "lines"}, {"gold": ()}])
def test_invalid_tasks_are_rejected(bad):
    with pytest.raises(ValueError):
        task(**bad)


def test_load_suite_reads_every_kind(tmp_path):
    save(tmp_path / "pilot-localization.jsonl", [task()])
    save(tmp_path / "pilot-structural.jsonl", [task(id="s1", kind="structural")])
    assert [t.id for t in load_suite("pilot", tmp_path)] == ["t1", "s1"]
    with pytest.raises(FileNotFoundError):
        load_suite("missing", tmp_path)
````

- [ ] **Step 4: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_suite.py -q`

Expected: a collection error, because `bench.eval` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 5: Create the packages**

Create `bench/__init__.py`:

````python
"""Benchmarks of duckgrep, and the A/B evaluation harness in bench.eval."""
````

- [ ] **Step 6: Create the eval package**

Create `bench/eval/__init__.py`:

````python
"""The A/B evaluation harness: run `python -m bench.eval --help`, and see docs/specs/2026-09-29-ab-eval-harness-design.md."""
````

- [ ] **Step 7: Create the tasks package**

Create `bench/eval/tasks/__init__.py`:

````python
"""Task builders: SWE-bench issue localization and structural questions."""
````

- [ ] **Step 8: Write the pinned parameters**

Create `bench/eval/config.py`:

````python
"""Everything a run's result depends on, pinned in one place. Changing a value here changes what is measured."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MODEL = "claude-sonnet-5-5"  # the full ID, never an alias
EFFORT = "medium"
BUILTIN_TOOLS = ("Bash", "Read", "Grep", "Glob")  # Grep and Glob named: the macOS default set lacks them
MAX_TURNS = 40
MAX_BUDGET_USD = 1.50
WALL_LIMIT_S = 15 * 60
PARALLEL = 3
REPETITIONS = 2
SEED = 20260929

# Sonnet 5.5 list prices, $ per million tokens. At these rates the token classes reproduce total_cost_usd exactly.
RATES = {"input": 2.00, "cache_write_5m": 2.50, "cache_write_1h": 4.00, "cache_read": 0.20, "output": 10.00}

SERENA = "serena-agent==1.7.0"
SERENA_TOOLS = (
    "initial_instructions",
    "get_symbols_overview",
    "find_symbol",
    "find_referencing_symbols",
    "find_declaration",
    "find_implementations",
    "search_for_pattern",
)

# A standalone rust-analyzer for Serena and for the SCIP answer keys; the owner's rustup toolchain is never touched
RUST_ANALYZER_URL = (
    "https://github.com/rust-lang/rust-analyzer/releases/download/2026-09-28/rust-analyzer-aarch64-apple-darwin.gz"
)
RUST_ANALYZER_GZ_SHA256 = "54ec873d8996e2c127d758bf45d4eacb6d3371dae4f6f6d5d3f05cedbae5fd59"
RUST_ANALYZER_VERSION = "rust-analyzer 0.3.3065-standalone (03fcb77246 2026-09-27)"


@dataclass(frozen=True)
class Dataset:
    repo: str
    revision: str
    path: str
    sha256: str

    @property
    def url(self) -> str:
        return f"https://huggingface.co/datasets/{self.repo}/resolve/{self.revision}/{self.path}"

    @property
    def label(self) -> str:
        return f"{self.repo}@{self.revision[:12]}"


DATASETS = {
    # SWE-bench Lite plus edit_functions; its problem_statement, base_commit and patch equal princeton-nlp's
    "lite": Dataset(
        "czlll/SWE-bench_Lite",
        "97f9af8814fe7eb15dcd9b2abaa9010e1eadda63",
        "data/test-00000-of-00001.parquet",
        "008ac523001de665d3f1e4f4179587dcd68dd7b2f2d5bd878a5385e3e938b59d",
    ),
    "multilingual": Dataset(
        "SWE-bench/SWE-bench_Multilingual",
        "846e647b9f33c0b51b739d005d13d85493c9af09",
        "data/test-00000-of-00001.parquet",
        "92abca7cb527b41a9f66d03a26ce441ff7319e3a49f985998fd56be4bb9b08b2",
    ),
    "live": Dataset(
        "SWE-bench-Live/MultiLang",
        "3638632e8153a10ca422c1022bed79023084b5c9",
        "data/rust-00000-of-00001.parquet",
        "02ac78e2c51a84eb174ac393bb07b77478f1f96cf260af18835a711dd8074ebc",
    ),
}

LIVE_SINCE = "2026-06-01"  # issues after the model's training data
# Too large for the pilot: over 2,000 .rs files or 50,000 files at HEAD on 2026-09-29. ruff is excluded by the spec.
EXCLUDED_RUST_REPOS = frozenset(
    {
        "astral-sh/ruff",
        "biomejs/biome",
        "pnpm/pnpm",
        "yvgude/lean-ctx",
        "nearai/ironclaw",
        "pola-rs/polars",
        "servo/servo",
    }
)


@dataclass(frozen=True)
class PinnedRepo:
    repo: str
    lang: str
    tag: str
    commit: str


STRUCTURAL_REPOS = (
    PinnedRepo("psf/requests", "python", "v2.32.3", "0e322af87745eff34caffe4df68456ebc20d9068"),
    PinnedRepo("pytest-dev/pytest", "python", "8.3.4", "53f8b4e634c5066c4f797a87b20060edbb086240"),
    PinnedRepo("BurntSushi/ripgrep", "rust", "14.1.1", "4649aa9700619f94cf9c66876e9549d83420e16c"),
    PinnedRepo("sharkdp/fd", "rust", "v10.2.0", "b19136871310b01500b4f09eadd7387b8476be47"),
)

EVAL_DIR = Path(__file__).resolve().parent
SUITES_DIR = EVAL_DIR / "suites"
RUNS_DIR = EVAL_DIR / "runs"
RESULTS_MD = EVAL_DIR.parent / "RESULTS.md"
MIN_FREE_GB = 10.0  # Serena's rust-analyzer builds grow the cache during a batch


def cache_dir() -> Path:
    """Clones, worktrees, indexes and tool caches; DUCKGREP_EVAL_CACHE moves them to a bigger disk. The agent sees
    its worktree's path, so the default name mentions neither duckgrep nor evaluation."""
    return Path(os.environ.get("DUCKGREP_EVAL_CACHE") or Path.home() / ".cache" / "code-tasks").expanduser()
````

- [ ] **Step 9: Write the suite module**

Create `bench/eval/suite.py`:

````python
"""A suite is a JSONL file of tasks: one question, its repo at a commit, and its answer key."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

KINDS = ("localization", "structural")
LANGS = ("python", "rust")
ANSWERS = ("functions", "files")


@dataclass(frozen=True)
class Task:
    id: str
    kind: str  # localization | structural
    lang: str  # python | rust
    repo: str  # owner/name on GitHub
    commit: str  # full SHA
    prompt: str  # what the agent is asked, verbatim
    gold: tuple[str, ...]  # "path:Qualified.name", or "path" for a file-only key
    source: str  # the dataset and revision, or the generator and its pinned tools
    answer: str = "functions"  # functions | files
    stratum: str = ""  # a reporting subgroup: lite, multilingual, live, callers, two-hop, importers

    def __post_init__(self):
        if self.kind not in KINDS or self.lang not in LANGS or self.answer not in ANSWERS:
            raise ValueError(f"bad task {self.id}: kind={self.kind} lang={self.lang} answer={self.answer}")
        if not self.gold:
            raise ValueError(f"task {self.id} has an empty answer key")
        object.__setattr__(self, "gold", tuple(self.gold))


def load(path: str | Path) -> list[Task]:
    with open(path) as f:
        return [Task(**json.loads(line)) for line in f if line.strip()]


def save(path: str | Path, tasks: list[Task]) -> None:
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task ids")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for t in tasks:
            f.write(json.dumps({**asdict(t), "gold": list(t.gold)}, sort_keys=True) + "\n")


def suite_files(name: str, suites_dir: Path) -> list[Path]:
    """The files of a suite: <name>-localization.jsonl and <name>-structural.jsonl, whichever exist."""
    return [p for kind in KINDS if (p := suites_dir / f"{name}-{kind}.jsonl").exists()]


def load_suite(name: str, suites_dir: Path) -> list[Task]:
    files = suite_files(name, suites_dir)
    if not files:
        raise FileNotFoundError(f"no suite files named {name}-*.jsonl in {suites_dir}")
    return [t for p in files for t in load(p)]
````

- [ ] **Step 10: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_suite.py -q`

Expected: `7 passed`.

- [ ] **Step 11: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 12: Commit**

```bash
git add pyproject.toml uv.lock .pre-commit-config.yaml .gitignore bench/__init__.py bench/eval/__init__.py bench/eval/tasks/__init__.py bench/eval/config.py bench/eval/suite.py tests/test_eval_suite.py
git commit -F - <<'EOF'
feat: eval package skeleton, pinned parameters and task suites

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 2: Answer parsing and scoring

**Files:**
- Create: `bench/eval/score.py`
- Test: `tests/test_eval_score.py`

**Interfaces:**
- Consumes: nothing.
- Produces, in `bench.eval.score`:
  - `parse_answer(text) -> list[str] | None`: the `locations` of the last fenced JSON block that has them.
  - `normalize(loc, roots=()) -> tuple[str, str]`: (path relative to the repo root, qualified name, or `""` for a bare path).
  - `Score(parsed, success, precision, recall, f1, file_recall)` with `.as_dict()`, and `UNPARSED`.
  - `score(answer, gold, kind, roots=()) -> Score`, where `kind` is the task's answer type (`functions` or `files`).

Two matching rules:
- A predicted function matches a key entry in the same file when its name equals the key's, or starts with the key's name plus `.`, since keys roll up to the outermost function.
- In `.rs` files, leading lower-case (module) components of the predicted name are ignored.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_score.py`:

````python
import pytest

from bench.eval.score import normalize, parse_answer, score

ANSWER = """I looked around.

```json
{"locations": ["old.py:f"]}
```

Final answer:

```json
{"locations": ["src/a.py:Session.request", "src/b.py:helper"]}
```
"""


def test_parse_answer_takes_the_last_block_with_locations():
    assert parse_answer(ANSWER) == ["src/a.py:Session.request", "src/b.py:helper"]


def test_parse_answer_skips_a_trailing_block_that_is_not_an_answer():
    text = ANSWER + '\n```json\n{"note": "no locations here"}\n```\n'
    assert parse_answer(text) == ["src/a.py:Session.request", "src/b.py:helper"]


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "no block at all",
        "```json\n{not json}\n```",
        '```json\n{"locations": "a.py"}\n```',
        "```\n[1, 2]\n```",
    ],
)
def test_parse_answer_returns_none_without_a_valid_block(text):
    assert parse_answer(text) is None


def test_parse_answer_accepts_an_unlabelled_fence():
    assert parse_answer('```\n{"locations": ["a.py:f"]}\n```') == ["a.py:f"]


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("src/a.py:Session.request", ("src/a.py", "Session.request")),
        ("./src/a.py:Session.request", ("src/a.py", "Session.request")),
        ("/wt/repo/src/a.py:Session.request", ("src/a.py", "Session.request")),
        ("`src/a.py:f`", ("src/a.py", "f")),
        ("src/a.py", ("src/a.py", "")),
        ("src/a.py:12", ("src/a.py", "")),
        ("src/a.py:f()", ("src/a.py", "f")),
        ("src/requests/sessions.py:requests.sessions.Session.request", ("src/requests/sessions.py", "Session.request")),
        ("src/walk.rs:WorkerState::new", ("src/walk.rs", "WorkerState.new")),
        ("src/walk.rs::WorkerState::new", ("src/walk.rs", "WorkerState.new")),
        ("src/walk.rs:<WorkerState as Drop>::drop", ("src/walk.rs", "WorkerState.drop")),
        ("src/walk.rs:<Walker<'a> as Iterator<Item = X>>::next", ("src/walk.rs", "Walker.next")),
        ("src/walk.rs:impl WorkerState/new", ("src/walk.rs", "WorkerState.new")),
        ("src/walk.rs:crate::walk::WorkerState::new", ("src/walk.rs", "WorkerState.new")),
        ("src/walk.rs:Cache<K, V>::get", ("src/walk.rs", "Cache.get")),
    ],
)
def test_normalize(entry, expected):
    assert normalize(entry, roots=("/wt/repo",)) == expected


def test_normalize_is_case_sensitive():
    assert normalize("src/a.py:session.Request") == ("src/a.py", "session.Request")


GOLD = ("src/a.py:Session.request", "src/a.py:merge", "src/b.py")  # src/b.py: a class-level edit


def test_success_needs_every_function_and_every_file_only_key():
    s = score(["src/a.py:Session.request", "src/a.py:merge", "src/b.py:Thing"], GOLD, "functions")
    assert s.parsed and s.success
    assert s.precision == 1.0 and s.recall == 1.0 and s.f1 == 1.0 and s.file_recall == 1.0


def test_missing_file_only_key_fails_success_but_not_function_recall():
    s = score(["src/a.py:Session.request", "src/a.py:merge"], GOLD, "functions")
    assert not s.success
    assert s.recall == 1.0 and s.file_recall == 0.5


def test_nested_function_counts_for_its_outermost_key():
    s = score(["src/a.py:Session.request.inner", "src/a.py:merge", "src/b.py"], GOLD, "functions")
    assert s.success and s.precision == 1.0


def test_precision_and_recall():
    s = score(["src/a.py:Session.request", "src/a.py:other", "src/c.py:x", "src/b.py"], GOLD, "functions")
    assert not s.success
    assert s.precision == pytest.approx(1 / 3)
    assert s.recall == pytest.approx(1 / 2)
    assert s.f1 == pytest.approx(2 * (1 / 3) * (1 / 2) / (1 / 3 + 1 / 2))


def test_duplicates_count_once():
    s = score(["src/a.py:merge", "./src/a.py:merge", "src/a.py:merge()"], ("src/a.py:merge",), "functions")
    assert s.precision == 1.0 and s.success


def test_unparsed_answer_scores_zero():
    s = score(None, GOLD, "functions")
    assert not s.parsed and not s.success and s.f1 == 0.0 and s.file_recall == 0.0


def test_empty_answer_scores_zero():
    s = score([], ("src/a.py:f",), "functions")
    assert s.parsed and not s.success and s.precision == 0.0 and s.recall == 0.0


def test_files_answer():
    s = score(["a.py", "b.py:ignored_name", "c.py"], ("a.py", "b.py", "d.py"), "files")
    assert s.precision == pytest.approx(2 / 3) and s.recall == pytest.approx(2 / 3) and not s.success
    assert score(["a.py", "b.py"], ("a.py", "b.py"), "files").success


def test_only_file_only_keys():
    s = score(["src/b.py"], ("src/b.py",), "functions")
    assert s.success and s.precision is None and s.recall is None


def test_rust_inline_module_prefix_is_accepted():
    s = score(
        ["src/lib.rs:tests::it_works", "src/lib.rs:imp::Walker::next"],
        ("src/lib.rs:it_works", "src/lib.rs:Walker.next"),
        "functions",
    )
    assert s.success and s.precision == 1.0


def test_inline_module_prefix_is_rust_only():
    assert not score(["src/a.py:helpers.run"], ("src/a.py:run",), "functions").success
````

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_score.py -q`

Expected: a collection error, because `bench.eval.score` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 3: Write the scorer**

Create `bench/eval/score.py`:

````python
"""Parse an agent's final answer and score it against the task's answer key."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n(.*?)```", re.S)
AS_TRAIT = re.compile(r"<\s*([^<>]+?)\s+as\s+[^<>]+>")  # <Type as Trait> -> Type
GENERIC_ARGS = re.compile(r"(?<=\w)<[^<>]*>")  # Type<T> -> Type
CALL_PARENS = re.compile(r"\(.*\)$")


def parse_answer(text: str | None) -> list[str] | None:
    """The `locations` list of the last fenced JSON block that has one, or None."""
    for block in reversed(FENCE.findall(text or "")):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        locs = obj.get("locations") if isinstance(obj, dict) else None
        if isinstance(locs, list) and all(isinstance(x, str) for x in locs):
            return locs
    return None


def _module_prefixes(path: str) -> list[str]:
    """Dotted module paths a name in `path` may be written under, longest first: src.pkg.mod., pkg.mod., mod."""
    stem, dot, ext = path.rpartition(".")
    if not dot or ext not in ("py", "rs"):
        return []
    parts = [p for p in stem.split("/") if p]
    if ext == "rs" and parts and parts[-1] in ("mod", "lib", "main"):
        parts = parts[:-1]
    return [".".join(parts[i:]) + "." for i in range(len(parts))]


def normalize(loc: str, roots: tuple[str, ...] = ()) -> tuple[str, str]:
    """(path relative to the repo root, qualified name) from an answer entry; the name is "" for a bare path.

    Rust spellings fold to the Python form: `Type::method`, `<Type as Trait>::method`, `impl Type/method` and
    `Type<T>::method` all become `Type.method`."""
    loc = loc.strip().strip("`").strip()
    path, _, name = loc.partition(":")
    path = path.strip()
    for root in sorted(roots, key=len, reverse=True):
        root = root.rstrip("/") + "/"
        if root != "/" and path.startswith(root):
            path = path[len(root) :]
            break
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    name = name.strip().lstrip(":").strip()
    if name.startswith("impl "):
        name = name[len("impl ") :].strip()
    previous = None
    while previous != name:
        previous = name
        name = AS_TRAIT.sub(r"\1", name)
        name = GENERIC_ARGS.sub("", name)
    name = CALL_PARENS.sub("", name.replace("::", ".").replace("/", ".")).strip(". ")
    if name.startswith("crate."):
        name = name[len("crate.") :]
    for prefix in _module_prefixes(path):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    if name.isdigit():  # "path:123" is a line number, not a name
        name = ""
    return path, name


def _key(entry: str) -> tuple[str, str]:
    path, _, name = entry.partition(":")
    return path, name


def _hits(gold: tuple[str, str], pred: tuple[str, str]) -> bool:
    """A named function matches its key, and so does a function nested in it (keys roll up to the outermost).
    In Rust, a name may also carry inline module prefixes the key leaves out: `tests.it_works`, `imp.Type.m`."""
    if gold[0] != pred[0]:
        return False
    names = [pred[1]]
    if pred[0].endswith(".rs"):
        parts = pred[1].split(".")
        while len(parts) > 1 and parts[0].islower():
            parts = parts[1:]
            names.append(".".join(parts))
    return any(n == gold[1] or n.startswith(gold[1] + ".") for n in names)


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


@dataclass(frozen=True)
class Score:
    parsed: bool
    success: bool  # every key entry was named: Agentless's superset rule
    precision: float | None
    recall: float | None
    f1: float | None
    file_recall: float | None  # share of the key's files that were named

    def as_dict(self) -> dict:
        return dict(self.__dict__)


UNPARSED = Score(parsed=False, success=False, precision=0.0, recall=0.0, f1=0.0, file_recall=0.0)


def score(answer: list[str] | None, gold: tuple[str, ...] | list[str], kind: str, roots: tuple[str, ...] = ()) -> Score:
    """Score `answer` (from parse_answer) against `gold`. `kind` is the task's answer type: functions or files."""
    if answer is None:
        return UNPARSED
    preds = list(dict.fromkeys(normalize(a, roots) for a in answer if a.strip()))
    keys = [_key(g) for g in gold]
    gold_files = list(dict.fromkeys(p for p, _ in keys))
    named_files = {p for p, _ in preds}
    file_recall = sum(f in named_files for f in gold_files) / len(gold_files)

    if kind == "files":
        pred_files = list(dict.fromkeys(p for p, _ in preds))
        tp = sum(f in set(gold_files) for f in pred_files)
        precision = tp / len(pred_files) if pred_files else 0.0
        recall = file_recall
        return Score(True, recall == 1.0, precision, recall, _f1(precision, recall), file_recall)

    func_keys = [k for k in keys if k[1]]
    file_only = {p for p, n in keys if not n}
    named = [p for p in preds if p[1]]
    covered = all(any(_hits(k, p) for p in named) for k in func_keys) and file_only <= named_files
    if not func_keys:
        return Score(True, covered, None, None, None, file_recall)
    # a named entry in a file whose key is file-only (a class- or module-level edit) is neither right nor wrong
    scored = [p for p in named if p[0] not in file_only or any(_hits(k, p) for k in func_keys)]
    precision = sum(any(_hits(k, p) for k in func_keys) for p in scored) / len(scored) if scored else 0.0
    recall = sum(any(_hits(k, p) for p in named) for k in func_keys) / len(func_keys)
    return Score(True, covered, precision, recall, _f1(precision, recall), file_recall)
````

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_score.py -q`

Expected: `36 passed`.

- [ ] **Step 5: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 6: Commit**

```bash
git add bench/eval/score.py tests/test_eval_score.py
git commit -F - <<'EOF'
feat: eval answer parsing and scoring

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 3: Stream parsing and run metrics

**Files:**
- Create: `bench/eval/stream.py`, `tests/eval_runs/{plain,mcp,denied,max_turns,auth_failure}.jsonl`
- Test: `tests/test_eval_stream.py`

**Interfaces:**
- Consumes: `config.MODEL`, `config.RATES`.
- Produces, in `bench.eval.stream`:
  - `Call(id, name, round, input, result, is_error, seconds)` and `Transcript(init, calls, denied, rounds, hooks, result, api_error)`;
  - `read(lines) -> Transcript`;
  - `tokens(result) -> dict[str, int]`, with the same keys as `RATES`;
  - `cost(tokens, rates=RATES) -> float`;
  - `config_problems(tr, tools, servers, model=MODEL) -> list[str]`;
  - `infrastructure_error(tr) -> bool`;
  - `metrics(tr) -> dict`;
  - `turns_to_locate(tr, gold) -> int | None`.
- `metrics` returns these keys, which the runner and the report rely on:
  - identity and outcome: `model`, `cli_version`, `terminal_reason`, `is_error`, `api_error`, `num_turns`;
  - calls: `rounds`, `tool_calls`, `tool_counts`, `search_calls`, `read_calls`, `mcp_calls`, `adopted`, `mcp_share`, `denied`, `denied_tools`;
  - cost and time: `tokens`, `tokens_total`, `cost_usd`, `cli_cost_usd`, `models`, `duration_ms`, `tool_seconds`;
  - the answer: `final_text`.

**Stream facts this relies on** (Claude Code 2.1.285):
- One API round trip is one assistant `message.id`, and it can carry several `tool_use` blocks.
- A login failure arrives as a synthetic assistant message (`model: "<synthetic>"`, `error: "authentication_failed"`), then a result with `subtype: "success"`, `is_error: true` and `terminal_reason: "api_error"`.
- Denied calls are listed in the result's `permission_denials`.

- [ ] **Step 1: Add the recorded streams**

These are real runs recorded while researching the spec: Haiku 4.5 on a two-file repo, sanitised to `/work/proj`. `auth_failure.jsonl` is a free probe made without `USER`. Copy them in and check them:

```bash
mkdir -p tests/eval_runs
cp /private/tmp/claude-501/-Users-ccf-git-duckgrep/942c1d79-b7dc-4ffe-92a4-a352d55aef56/scratchpad/eval-fixtures/*.jsonl tests/eval_runs/
shasum -a 256 tests/eval_runs/*.jsonl
```

| file | SHA-256 |
|---|---|
| `auth_failure.jsonl` | `877a9dcc109b38175dde6679899c5b590c0acd7690e3147d7a01b60bf889a4cf` |
| `denied.jsonl` | `eea36fe4315fed5ff23f9e12d799737821acb2ebcbd04e0adec937cb21b7100f` |
| `max_turns.jsonl` | `cd531438408d67d46bf923be8e2201c73fdacd0f201baf69fe119db7eb9f4f27` |
| `mcp.jsonl` | `7b370a3374fd840b6b8b7605a9c055ed8151d5a3dac0b4c4788164b323844a45` |
| `plain.jsonl` | `5b9fbf96ba463946f3e53a35cc6f9a953587b1a2deb59c4158346d52bc81ab31` |

If the scratch copies are gone, re-record them:
- **`auth_failure.jsonl`:** run the baseline command from `setups.command` in any git repo, under `env -i HOME="$HOME" PATH="$PATH" TMPDIR="$TMPDIR"` (no `USER`), and replace the repo's path with `/work/proj`. This is free.
- **The other four:** recording costs about $0.05 in total. The tests pin their exact numbers, so after re-recording, update those numbers from `stream.metrics` output.

- [ ] **Step 2: Write the failing test**

Create `tests/test_eval_stream.py`:

````python
"""Recorded stream-json runs (Claude Code 2.1.285, Haiku 4.5, paths sanitised) against the transcript parser."""

import os

import pytest

from bench.eval import stream

RUNS = os.path.join(os.path.dirname(__file__), "eval_runs")
HAIKU = "claude-haiku-4-5-20251001"
HAIKU_RATES = {"input": 1.00, "cache_write_5m": 1.25, "cache_write_1h": 2.00, "cache_read": 0.10, "output": 5.00}
BUILTINS = {"Bash", "Glob", "Grep", "Read"}


def run(name):
    with open(os.path.join(RUNS, f"{name}.jsonl")) as f:
        return stream.read(f)


def test_plain_run():
    tr = run("plain")
    m = stream.metrics(tr)
    assert tr.rounds == 3 and m["num_turns"] == 3
    assert m["tool_counts"] == {"Grep": 1, "Read": 1}
    assert (m["search_calls"], m["read_calls"], m["mcp_calls"]) == (1, 1, 0)
    assert not m["adopted"] and m["mcp_share"] == 0.0
    assert m["tokens"] == {
        "input": 26,
        "cache_write_5m": 419,
        "cache_write_1h": 1949,
        "cache_read": 26946,
        "output": 356,
    }
    assert m["tokens_total"] == 29696
    assert m["terminal_reason"] == "completed" and not m["is_error"] and m["api_error"] is None
    assert m["final_text"].startswith("`needle_fn` is defined in src/a.py:4")
    assert m["tool_seconds"] == {"Grep": 0.03, "Read": 0.013}


def test_cost_formula_reproduces_claude_codes_own_total():
    for name in ("plain", "mcp", "denied", "max_turns"):
        tr = run(name)
        assert stream.cost(stream.tokens(tr.result), HAIKU_RATES) == pytest.approx(
            tr.result["total_cost_usd"], abs=1e-12
        )


def test_cost_at_sonnet_rates():
    assert stream.metrics(run("plain"))["cost_usd"] == pytest.approx(0.017845, abs=1e-6)


def test_mcp_run_counts_the_mcp_tool_and_its_round():
    m = stream.metrics(run("mcp"))
    assert m["tool_counts"] == {"mcp__tiny__ping": 1, "Grep": 1, "Read": 1}
    assert m["rounds"] == 4 and m["mcp_calls"] == 1 and m["adopted"]
    assert m["mcp_share"] == pytest.approx(1 / 3)


def test_denied_call_is_excluded_and_counted_apart():
    tr = run("denied")
    m = stream.metrics(tr)
    assert m["tool_counts"] == {"Grep": 1, "Read": 1}
    assert m["denied"] == 1 and m["denied_tools"] == ["mcp__tiny__ping"]
    assert tr.rounds == 2  # the three calls of the first message are one round trip


def test_turn_cap_is_an_error_not_an_infrastructure_failure():
    tr = run("max_turns")
    m = stream.metrics(tr)
    assert m["is_error"] and m["terminal_reason"] == "max_turns" and m["final_text"] == ""
    assert not stream.infrastructure_error(tr)


def test_auth_failure_reports_success_but_is_an_infrastructure_error():
    tr = run("auth_failure")
    assert tr.result["subtype"] == "success"  # why the subtype alone is never trusted
    m = stream.metrics(tr)
    assert m["is_error"] and tr.api_error == "authentication_failed"
    assert stream.infrastructure_error(tr)
    assert tr.rounds == 0 and m["tool_calls"] == 0 and m["cost_usd"] == 0.0


def test_truncated_stream_is_an_error():
    with open(os.path.join(RUNS, "plain.jsonl")) as f:
        lines = f.readlines()[:-1] + ['{"type": "resu']
    m = stream.metrics(stream.read(lines))
    assert m["is_error"] and m["terminal_reason"] is None and m["tool_calls"] == 2


def test_config_check_passes_for_the_intended_setup():
    assert stream.config_problems(run("plain"), BUILTINS, set(), model=HAIKU) == []
    assert stream.config_problems(run("mcp"), BUILTINS | {"mcp__tiny__ping"}, {"tiny"}, model=HAIKU) == []


def test_config_check_catches_wrong_tools_model_and_servers():
    tr = run("mcp")
    problems = stream.config_problems(tr, BUILTINS, set())
    assert any("model is" in p for p in problems)
    assert any("mcp__tiny__ping" in p for p in problems)
    assert any("MCP servers" in p for p in problems)


def test_config_check_catches_a_failed_server_hooks_and_skills():
    tr = run("mcp")
    tr.init = {**tr.init, "mcp_servers": [{"name": "tiny", "status": "failed"}], "skills": ["x"]}
    tr.hooks = 2
    problems = stream.config_problems(tr, BUILTINS | {"mcp__tiny__ping"}, {"tiny"}, model=HAIKU)
    assert "MCP server tiny is failed" in problems
    assert "skills is not empty" in problems
    assert "2 hook events" in problems


def test_config_check_on_the_free_probe_sees_the_setup_before_the_login_fails():
    tr = run("auth_failure")
    assert stream.config_problems(tr, BUILTINS | {"mcp__tiny__ping"}, {"tiny"}, model="claude-sonnet-5-5") == []


def test_turns_to_locate():
    assert stream.turns_to_locate(run("plain"), ["src/a.py:needle_fn"]) == 1  # grep content shows path and name
    assert stream.turns_to_locate(run("mcp"), ["src/a.py:needle_fn"]) == 3  # file list first, then the read
    assert stream.turns_to_locate(run("mcp"), ["src/a.py"]) == 2  # a file-only key needs the path alone
    assert stream.turns_to_locate(run("plain"), ["src/a.py:Other.needle"]) is None
    assert stream.turns_to_locate(run("auth_failure"), ["src/a.py"]) is None
````

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_stream.py -q`

Expected: a collection error, because `bench.eval.stream` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 4: Write the stream parser**

Create `bench/eval/stream.py`:

````python
"""Turn a Claude Code stream-json transcript into one run's measurements."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from . import config

SEARCH_TOOLS = frozenset({"Grep", "Glob", "Bash"})
BUILTIN_PLUGINS = frozenset({"cc-plugin-agents-md", "cc-plugin-telemetry"})  # cannot be removed; same in every setup


@dataclass
class Call:
    id: str
    name: str
    round: int  # the 1-based API round trip that issued it
    input: dict
    result: str = ""
    is_error: bool = False
    seconds: float | None = None  # from the tool_use event to its tool_result event


@dataclass
class Transcript:
    init: dict = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)  # issue order; denied calls excluded
    denied: list[str] = field(default_factory=list)  # names of the calls the permission mode refused
    rounds: int = 0  # distinct assistant message IDs; synthetic error messages excluded
    hooks: int = 0
    result: dict = field(default_factory=dict)  # the final result event, {} if the stream was cut off
    api_error: str | None = None  # e.g. "authentication_failed", from a synthetic assistant message


def _time(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def read(lines: Iterable[str]) -> Transcript:
    tr = Transcript()
    round_of: dict[str, int] = {}
    calls: dict[str, Call] = {}
    issued: dict[str, datetime | None] = {}
    for raw in lines:
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue  # blank, or the last line of a killed run
        kind = e.get("type")
        if kind == "system":
            sub = str(e.get("subtype", ""))
            if sub == "init":
                tr.init = e
            elif sub.startswith("hook"):
                tr.hooks += 1
        elif kind == "assistant":
            msg = e.get("message") or {}
            if e.get("error") or msg.get("model") == "<synthetic>":
                tr.api_error = tr.api_error or e.get("error") or "synthetic"
                continue
            mid = msg.get("id")
            round_of.setdefault(mid, len(round_of) + 1)
            for b in msg.get("content") or []:
                if b.get("type") == "tool_use" and b.get("id") not in calls:
                    calls[b["id"]] = Call(b["id"], b.get("name", ""), round_of[mid], b.get("input") or {})
                    issued[b["id"]] = _time(e.get("timestamp"))
        elif kind == "user":
            content = (e.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                call = calls.get(b.get("tool_use_id")) if isinstance(b, dict) else None
                if call is None or b.get("type") != "tool_result":
                    continue
                call.result = _text(b.get("content"))
                call.is_error = bool(b.get("is_error"))
                t0, t1 = issued.get(call.id), _time(e.get("timestamp"))
                if t0 and t1:
                    call.seconds = (t1 - t0).total_seconds()
        elif kind == "result":
            tr.result = e
    denied_ids = {d.get("tool_use_id") for d in tr.result.get("permission_denials") or []}
    tr.denied = [c.name for c in calls.values() if c.id in denied_ids]
    tr.calls = [c for c in calls.values() if c.id not in denied_ids]
    tr.rounds = len(round_of)
    return tr


def tokens(result: dict) -> dict[str, int]:
    """The session's token classes from the result event (never summed per message)."""
    u = result.get("usage") or {}
    split = u.get("cache_creation") or {}
    write = int(u.get("cache_creation_input_tokens") or 0)
    write_1h = int(split.get("ephemeral_1h_input_tokens") or 0)
    return {
        "input": int(u.get("input_tokens") or 0),
        "cache_write_5m": write - write_1h,
        "cache_write_1h": write_1h,
        "cache_read": int(u.get("cache_read_input_tokens") or 0),
        "output": int(u.get("output_tokens") or 0),  # includes thinking
    }


def cost(tok: dict[str, int], rates: dict[str, float] = config.RATES) -> float:
    return sum(tok[k] * rates[k] for k in rates) / 1e6


def config_problems(tr: Transcript, tools: set[str], servers: set[str], model: str = config.MODEL) -> list[str]:
    """Why the session was not configured as the setup requires; empty when it was."""
    init = tr.init
    if not init:
        return ["no init event"]
    problems = []
    if init.get("model") != model:
        problems.append(f"model is {init.get('model')!r}, not {model!r}")
    got = set(init.get("tools") or [])
    if got != tools:
        problems.append(f"tools differ: extra {sorted(got - tools)}, missing {sorted(tools - got)}")
    status = {s.get("name"): s.get("status") for s in init.get("mcp_servers") or []}
    if set(status) != servers:
        problems.append(f"MCP servers are {sorted(status)}, not {sorted(servers)}")
    problems += [f"MCP server {n} is {s}" for n, s in sorted(status.items()) if s != "connected"]
    plugins = {p.get("name") for p in init.get("plugins") or []} - BUILTIN_PLUGINS
    if plugins:
        problems.append(f"plugins loaded: {sorted(plugins)}")
    for key in ("skills", "slash_commands", "memory_paths"):
        if init.get(key):
            problems.append(f"{key} is not empty")
    if init.get("permissionMode") != "dontAsk":
        problems.append(f"permission mode is {init.get('permissionMode')!r}")
    if tr.hooks:
        problems.append(f"{tr.hooks} hook events")
    return problems


def infrastructure_error(tr: Transcript) -> bool:
    """The run failed for reasons outside the agent (login, rate limit, API outage): rerun it, never score it."""
    return tr.api_error is not None or tr.result.get("terminal_reason") == "api_error"


def metrics(tr: Transcript) -> dict:
    counts = Counter(c.name for c in tr.calls)
    total = sum(counts.values())
    mcp = sum(n for name, n in counts.items() if name.startswith("mcp__"))
    tok = tokens(tr.result)
    seconds: Counter[str] = Counter()
    for c in tr.calls:
        seconds[c.name] += c.seconds or 0.0
    res = tr.result
    return {
        "model": tr.init.get("model"),
        "cli_version": tr.init.get("claude_code_version"),
        "terminal_reason": res.get("terminal_reason"),
        "is_error": bool(res.get("is_error", True)),  # no result event: the run was cut off
        "api_error": tr.api_error,
        "num_turns": res.get("num_turns"),  # Claude Code's count (tool calls + 1); kept, not a metric
        "rounds": tr.rounds,
        "tool_calls": total,
        "tool_counts": dict(counts),
        "search_calls": sum(n for name, n in counts.items() if name in SEARCH_TOOLS),
        "read_calls": counts.get("Read", 0),
        "mcp_calls": mcp,
        "adopted": mcp > 0,
        "mcp_share": mcp / total if total else 0.0,
        "denied": len(tr.denied),
        "denied_tools": tr.denied,
        "tokens": tok,
        "tokens_total": sum(tok.values()),
        "cost_usd": round(cost(tok), 6),
        "cli_cost_usd": res.get("total_cost_usd"),  # swings with cache warmth; recorded, not used
        "models": sorted(res.get("modelUsage") or {}),
        "duration_ms": res.get("duration_ms"),
        "tool_seconds": {k: round(v, 3) for k, v in seconds.items()},
        "final_text": res.get("result") or "",
    }


def turns_to_locate(tr: Transcript, gold: tuple[str, ...] | list[str]) -> int | None:
    """The first round whose tool results show a key location: the key's path (in the call or its result) with
    the function's name in the result, or the path alone for a file-only key. None if none ever did."""
    keys = []
    for entry in gold:
        path, _, qual = entry.partition(":")
        last = qual.split(".")[-1] if qual else ""
        keys.append((path, re.compile(rf"(?<!\w){re.escape(last)}(?!\w)") if last else None))
    for call in sorted(tr.calls, key=lambda c: c.round):
        seen = json.dumps(call.input) + "\n" + call.result
        for path, name in keys:
            if path in seen and (name is None or name.search(call.result)):
                return call.round
    return None
````

- [ ] **Step 5: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_stream.py -q`

Expected: `13 passed`.

- [ ] **Step 6: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 7: Commit**

```bash
git add bench/eval/stream.py tests/eval_runs tests/test_eval_stream.py
git commit -F - <<'EOF'
feat: eval stream-json parsing and run metrics

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 4: The three setups

**Files:**
- Create: `bench/eval/setups.py`, `bench/eval/serena/eval-nav.yml`, `bench/eval/serena/serena_config.yml`
- Test: `tests/test_eval_setups.py`

**Interfaces:**
- Consumes: `config.BUILTIN_TOOLS`, `MODEL`, `EFFORT`, `MAX_TURNS`, `MAX_BUDGET_USD`, `SERENA`, `SERENA_TOOLS`.
- Produces, in `bench.eval.setups`:
  - `Setup(name, servers, tools, allow)` with `.expected_tools`, and `SETUPS` (keys `baseline`, `duckgrep`, `serena`);
  - `environment(with_user=True) -> dict`;
  - `command(setup, prompt, mcp_config, claude="claude") -> list[str]`;
  - `serena_home(dest, cache) -> Path`;
  - `serena_argv(worktree) -> list[str]`;
  - `mcp_config(setup, worktree, repo, cache, home=None) -> dict | None`;
  - `rust_env(cache, repo) -> dict`, which Task 8's Serena warm-up and Task 9's SCIP build use as well.

The command line and environment are the recipe verified by hand, which the spec records. `environment()` is the whole environment of a run, as `env -i` would give it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_setups.py`:

````python
import sys

import pytest

from bench.eval import config, setups

VERIFIED = [  # the recipe verified by hand on Claude Code 2.1.285
    "claude",
    "-p",
    "PROMPT",
    "--model",
    "claude-sonnet-5-5",
    "--effort",
    "medium",
    "--output-format",
    "stream-json",
    "--verbose",
    "--setting-sources",
    "",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--tools",
    "Bash,Read,Grep,Glob",
    "--permission-mode",
    "dontAsk",
]
TAIL = ["--no-session-persistence", "--max-turns", "40", "--max-budget-usd", "1.50"]


def test_baseline_command():
    argv = setups.command(setups.SETUPS["baseline"], "PROMPT", None)
    assert argv == VERIFIED + ["--allowedTools", "Bash,Read,Grep,Glob"] + TAIL


@pytest.mark.parametrize("name, allow", [("duckgrep", "mcp__duckgrep__query"), ("serena", "mcp__serena__*")])
def test_mcp_commands_differ_only_in_the_server_and_its_allow_entry(tmp_path, name, allow):
    argv = setups.command(setups.SETUPS[name], "PROMPT", tmp_path / "mcp.json")
    assert (
        argv
        == VERIFIED
        + ["--allowedTools", f"Bash,Read,Grep,Glob,{allow}", "--mcp-config", str(tmp_path / "mcp.json")]
        + TAIL
    )


def test_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    env = setups.environment()
    assert set(env) == {"HOME", "PATH", "USER", "TMPDIR", "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "ENABLE_TOOL_SEARCH"}
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and env["ENABLE_TOOL_SEARCH"] == "false"
    assert "USER" not in setups.environment(with_user=False)


def test_expected_tools():
    assert setups.SETUPS["baseline"].expected_tools == {"Bash", "Read", "Grep", "Glob"}
    assert setups.SETUPS["duckgrep"].expected_tools == {"Bash", "Read", "Grep", "Glob", "mcp__duckgrep__query"}
    assert len(setups.SETUPS["serena"].expected_tools) == 4 + len(config.SERENA_TOOLS)


def test_duckgrep_server_runs_this_environments_duckgrep(tmp_path):
    cfg = setups.mcp_config(setups.SETUPS["duckgrep"], tmp_path / "wt", "o/r", tmp_path)
    assert cfg == {
        "mcpServers": {
            "duckgrep": {"command": sys.executable, "args": ["-m", "duckgrep", "-C", str(tmp_path / "wt"), "mcp"]}
        }
    }
    assert setups.mcp_config(setups.SETUPS["baseline"], tmp_path, "o/r", tmp_path) is None


def test_serena_server(tmp_path):
    home = setups.serena_home(tmp_path / "home", tmp_path / "cache")
    cfg = setups.mcp_config(setups.SETUPS["serena"], tmp_path / "wt", "Owner/Repo", tmp_path / "cache", home)
    server = cfg["mcpServers"]["serena"]
    assert server["args"] == [
        "--from",
        "serena-agent==1.7.0",
        "serena",
        "start-mcp-server",
        "--context",
        "eval-nav",
        "--project",
        str(tmp_path / "wt"),
        "--enable-web-dashboard",
        "false",
        "--open-web-dashboard",
        "false",
        "--enable-gui-log-window",
        "false",
    ]
    env = server["env"]
    assert env["SERENA_HOME"] == str(home) and env["RUSTUP_TOOLCHAIN"] == "stable"
    assert env["CARGO_HOME"] == str(tmp_path / "cache" / "cargo-home")
    assert env["CARGO_TARGET_DIR"] == str(tmp_path / "cache" / "cargo-target" / "owner__repo")
    assert env["PATH"].startswith(str(tmp_path / "cache" / "bin"))
    with pytest.raises(ValueError):
        setups.mcp_config(setups.SETUPS["serena"], tmp_path, "o/r", tmp_path)


def test_serena_home_is_rendered(tmp_path):
    home = setups.serena_home(tmp_path / "home", tmp_path / "cache")
    text = (home / "serena_config.yml").read_text()
    assert (
        f'project_serena_folder_location: "{tmp_path / "cache" / "serena-projects"}/$projectFolderName/.serena"' in text
    )
    assert "projects: []" in text and "@PROJECTS@" not in text
    context = (home / "contexts" / "eval-nav.yml").read_text()
    assert all(f"  - {tool}" in context for tool in config.SERENA_TOOLS)
````

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_setups.py -q`

Expected: a collection error, because `bench.eval.setups` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 3: Write Serena's context**

Create `bench/eval/serena/eval-nav.yml`:

````yaml
description: Navigation only; a single project, and no editing, memory, onboarding or shell tools
prompt: |
  Serena's code-intelligence tools are available for locating and reading code symbolically
  (get_symbols_overview, find_symbol, find_referencing_symbols, find_declaration, find_implementations).
fixed_tools:
  - initial_instructions
  - get_symbols_overview
  - find_symbol
  - find_referencing_symbols
  - find_declaration
  - find_implementations
  - search_for_pattern
single_project: true
structured_tool_output: false
````

- [ ] **Step 4: Write Serena's settings (Serena 1.7.0's generated file, with comments stripped and the projects list emptied)**

Create `bench/eval/serena/serena_config.yml`:

````yaml
# Serena 1.7.0 settings for the A/B runs; @PROJECTS@ becomes <cache>/serena-projects.
language_backend: LSP
gui_log_window: false
web_dashboard: false
web_dashboard_open_on_launch: false
log_level: 20
tool_timeout: 240
base_modes: []
default_modes:
- no-memories
project_serena_folder_location: "@PROJECTS@/$projectFolderName/.serena"
projects: []
excluded_tools: []
included_optional_tools: []
fixed_tools: []
symbol_info_budget: 10.0
read_only_memory_patterns: []
ignored_memory_patterns: []
ls_specific_settings: {}
trace_lsp_communication: false
web_dashboard_interface:
web_dashboard_listen_address: 127.0.0.1
web_dashboard_trusted_hosts:
- 127.0.0.1
- localhost
jetbrains_plugin_server_address: 127.0.0.1
jetbrains_launch_command:
token_count_estimator: CHAR_COUNT
default_max_tool_answer_chars: 150000
ignored_paths: []
trusted_project_path_patterns:
- '**'
ls_priorities:
line_ending: native
````

- [ ] **Step 5: Write the setups module**

Create `bench/eval/setups.py`:

````python
"""The three setups. Same model, CLI, built-in tools and prompt; they differ only in one MCP server."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config

SERENA_DIR = Path(__file__).resolve().parent / "serena"


@dataclass(frozen=True)
class Setup:
    name: str
    servers: frozenset[str]  # MCP servers the init event must list, each connected
    tools: frozenset[str]  # MCP tools the init event must list besides the built-ins
    allow: tuple[str, ...]  # extra --allowedTools entries

    @property
    def expected_tools(self) -> set[str]:
        return set(config.BUILTIN_TOOLS) | set(self.tools)


SETUPS = {
    "baseline": Setup("baseline", frozenset(), frozenset(), ()),
    "duckgrep": Setup(
        "duckgrep", frozenset({"duckgrep"}), frozenset({"mcp__duckgrep__query"}), ("mcp__duckgrep__query",)
    ),
    "serena": Setup(
        "serena",
        frozenset({"serena"}),
        frozenset(f"mcp__serena__{t}" for t in config.SERENA_TOOLS),
        ("mcp__serena__*",),
    ),
}


def environment(with_user: bool = True) -> dict[str, str]:
    """A run's entire environment, as `env -i` would give it: nothing of the parent session leaks in. USER is
    what the keychain login needs; without it a run fails at login, before any model call."""
    env = {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"], "TMPDIR": os.environ.get("TMPDIR", "/tmp")}
    if with_user:
        env["USER"] = os.environ["USER"]
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env["ENABLE_TOOL_SEARCH"] = "false"  # MCP tools load directly, not through an extra ToolSearch call
    return env


def command(setup: Setup, prompt: str, mcp_config: Path | None, claude: str = "claude") -> list[str]:
    argv = [
        claude,
        "-p",
        prompt,
        "--model",
        config.MODEL,
        "--effort",
        config.EFFORT,
        "--output-format",
        "stream-json",
        "--verbose",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--tools",
        ",".join(config.BUILTIN_TOOLS),
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ",".join(config.BUILTIN_TOOLS + setup.allow),
    ]
    if mcp_config is not None:
        argv += ["--mcp-config", str(mcp_config)]
    argv += [
        "--no-session-persistence",
        "--max-turns",
        str(config.MAX_TURNS),
        "--max-budget-usd",
        f"{config.MAX_BUDGET_USD:.2f}",
    ]
    return argv


def serena_home(dest: Path, cache: Path) -> Path:
    """A fresh SERENA_HOME: Serena rewrites its config file, so runs never share one. Project data (the symbol
    cache the warm-up fills) lives outside it, under the cache, and outside the worktree."""
    (dest / "contexts").mkdir(parents=True, exist_ok=True)
    shutil.copy(SERENA_DIR / "eval-nav.yml", dest / "contexts" / "eval-nav.yml")
    text = (SERENA_DIR / "serena_config.yml").read_text()
    (dest / "serena_config.yml").write_text(text.replace("@PROJECTS@", str(cache / "serena-projects")))
    return dest


def serena_argv(worktree: Path) -> list[str]:
    return [
        "--from",
        config.SERENA,
        "serena",
        "start-mcp-server",
        "--context",
        "eval-nav",
        "--project",
        str(worktree),
        "--enable-web-dashboard",
        "false",
        "--open-web-dashboard",
        "false",
        "--enable-gui-log-window",
        "false",
    ]


def mcp_config(setup: Setup, worktree: Path, repo: str, cache: Path, home: Path | None = None) -> dict | None:
    """The --mcp-config document for a run, or None for the baseline."""
    if setup.name == "baseline":
        return None
    if setup.name == "duckgrep":
        server = {"command": sys.executable, "args": ["-m", "duckgrep", "-C", str(worktree), "mcp"]}
        return {"mcpServers": {"duckgrep": server}}
    if home is None:
        raise ValueError("the serena setup needs a SERENA_HOME")
    env = {"SERENA_HOME": str(home), **rust_env(cache, repo)}
    server = {"command": shutil.which("uvx") or "uvx", "args": serena_argv(worktree), "env": env}
    return {"mcpServers": {"serena": server}}


def rust_env(cache: Path, repo: str) -> dict[str, str]:
    """Variables for anything that runs cargo: its caches live in the eval cache, the worktree stays clean, and
    the installed stable toolchain is used whatever the repo pins, so rustup never installs anything."""
    return {
        "PATH": f"{cache / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "CARGO_HOME": str(cache / "cargo-home"),
        "CARGO_TARGET_DIR": str(cache / "cargo-target" / repo.lower().replace("/", "__")),
        "RUSTUP_TOOLCHAIN": "stable",
    }
````

- [ ] **Step 6: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_setups.py -q`

Expected: `8 passed`.

- [ ] **Step 7: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 8: Commit**

```bash
git add bench/eval/setups.py bench/eval/serena tests/test_eval_setups.py
git commit -F - <<'EOF'
feat: eval setups: command line, environment and MCP servers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 5: Answer keys from fix patches

**Files:**
- Create: `bench/eval/gold.py`
- Test: `tests/test_eval_gold.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. It uses `unidiff`, `ast` and `tree_sitter_rust`.
- Produces, in `bench.eval.gold`:
  - `Unit(qualname, start, end, kind, head)`, where `kind` is `function`, `class`, `test-fn` or `test-mod`;
  - `python_units(src)`, `python_import_lines(src)`, `rust_parser()`, `rust_type_name(node, src)`, `rust_units(src)`, `rust_use_lines(src)`;
  - `FUNCTION`, `TEST`, and `outermost(units, line, kinds=FUNCTION) -> Unit | None`;
  - `is_test_path(path, lang)`;
  - `Key(entries, files, functions)` and `derive(patch, read, lang) -> Key`, where `read(path)` returns the base file's text or None;
  - `normalize_listed(entries, read) -> tuple[str, ...]` and `same_key(derived, listed) -> bool`.

The rules are those in the module docstring and in refinement 3 above. The tests build their patches with `difflib`, and shift hunk headers to reproduce the stale line numbers found in SWE-bench.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_gold.py`:

````python
import difflib
import re

import pytest

from bench.eval import gold

BASE_PY = """import os
from .util import (
    a,
    b)

X = 1


class Session:
    retries = 3

    def request(self, url):
        def inner():
            return url
        return inner()

    @property
    def closed(self):
        return False


def helper(x):
    y = x + 1
    return y
"""

BASE_RS = """use std::fmt;

pub struct Walker<T> {
    items: Vec<T>,
}

impl<T> Walker<T> {
    pub fn new() -> Self {
        Walker { items: Vec::new() }
    }
}

impl<T> fmt::Display for Walker<T> {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "walker")
    }
}

pub fn run() -> u32 {
    1
}

#[cfg(test)]
mod tests {
    #[test]
    fn it_works() {
        assert_eq!(super::run(), 1);
    }
}
"""


def patch(path, before, after, shift=0):
    """A unified diff like SWE-bench's; `shift` moves every hunk header, as stale line numbers do."""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{path}", f"b/{path}"
    )
    text = f"diff --git a/{path} b/{path}\n" + "".join(lines)
    if shift:
        text = re.sub(r"@@ -(\d+)", lambda m: f"@@ -{int(m.group(1)) + shift}", text)
    return text


def key(path, before, after, lang="python", shift=0):
    return gold.derive(patch(path, before, after, shift), {path: before}.get, lang).entries


def test_nested_function_rolls_up():
    assert key("pkg/s.py", BASE_PY, BASE_PY.replace("return url", "return url.strip()")) == (
        "pkg/s.py:Session.request",
    )


def test_class_level_edit_is_a_file_only_key():
    assert key("pkg/s.py", BASE_PY, BASE_PY.replace("retries = 3", "retries = 4")) == ("pkg/s.py",)


def test_a_function_key_makes_the_file_only_key_redundant():
    after = BASE_PY.replace("retries = 3", "retries = 4").replace("return False", "return True")
    assert key("pkg/s.py", BASE_PY, after) == ("pkg/s.py:Session.closed",)


def test_imports_even_multiline_and_comments_are_ignored():
    after = BASE_PY.replace("    b)", "    b, c)").replace("import os", "import os  # os\nimport re")
    assert key("pkg/s.py", BASE_PY, after) == ()


def test_insertion_at_the_end_of_a_body_belongs_to_the_function():
    assert key("pkg/s.py", BASE_PY, BASE_PY.replace("    return y\n", "    return y\n    print(y)\n")) == (
        "pkg/s.py:helper",
    )


def test_new_top_level_function_is_a_file_only_key():
    after = BASE_PY + "\n\ndef added():\n    return 1\n"
    assert key("pkg/s.py", BASE_PY, after) == ("pkg/s.py",)


def test_decorator_edit_belongs_to_the_function():
    after = BASE_PY.replace("@property", "@functools.cached_property")
    assert key("pkg/s.py", BASE_PY, after) == ("pkg/s.py:Session.closed",)


def test_stale_hunk_line_numbers_are_located_by_context():
    after = BASE_PY.replace("y = x + 1", "y = x + 2")
    assert key("pkg/s.py", BASE_PY, after, shift=4) == key("pkg/s.py", BASE_PY, after) == ("pkg/s.py:helper",)


def test_a_hunk_that_does_not_apply_is_an_error():
    with pytest.raises(ValueError):
        gold.derive(
            patch("pkg/s.py", BASE_PY, BASE_PY.replace("X = 1", "X = 2")), {"pkg/s.py": "other\n"}.get, "python"
        )


def test_test_files_are_ignored_and_new_files_are_file_only_keys():
    diff = patch("tests/test_s.py", "def test_a():\n    pass\n", "def test_a():\n    assert 1\n")
    diff += "diff --git a/pkg/new.py b/pkg/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    k = gold.derive(diff, {}.get, "python")
    assert k.entries == ("pkg/new.py",) and k.files == ("pkg/new.py",)


def test_rust_impl_and_trait_methods_are_type_dot_method():
    after = BASE_RS.replace("Vec::new()", "Vec::with_capacity(4)").replace('"walker"', '"w"')
    assert key("src/walk.rs", BASE_RS, after, "rust") == ("src/walk.rs:Walker.fmt", "src/walk.rs:Walker.new")


def test_rust_test_code_and_use_lines_are_ignored():
    after = BASE_RS.replace("super::run(), 1", "super::run(), 2").replace("use std::fmt;", "use std::fmt::{self};")
    assert key("src/walk.rs", BASE_RS, after, "rust") == ()


def test_rust_insertion_after_a_closing_brace_is_outside_the_function():
    after = BASE_RS.replace("    1\n}\n", "    1\n}\n\npub fn added() {}\n")
    assert key("src/walk.rs", BASE_RS, after, "rust") == ("src/walk.rs",)
    assert key("src/walk.rs", BASE_RS, BASE_RS.replace("    1\n}", "    let x = 1;\n    x\n}"), "rust") == (
        "src/walk.rs:run",
    )


def test_rust_units_mark_test_code():
    kinds = {u.qualname: u.kind for u in gold.rust_units(BASE_RS)}
    assert kinds["Walker.new"] == "function" and kinds["run"] == "function"
    assert kinds["it_works"] == "test-fn" and kinds["tests"] == "test-mod"


def test_listed_key_normalisation():
    read = {"pkg/s.py": BASE_PY}.get
    assert gold.normalize_listed(["pkg/s.py:Session", "pkg/s.py:Session.request.inner"], read) == (
        "pkg/s.py:Session.request",
    )
    assert gold.normalize_listed(["pkg/s.py:Session"], read) == ("pkg/s.py",)
    with pytest.raises(ValueError):
        gold.normalize_listed(["pkg/s.py:missing"], read)


def test_same_key_treats_a_class_as_its_init():
    assert gold.same_key(("pkg/s.py:Session.__init__",), ("pkg/s.py",))
    assert gold.same_key(
        ("pkg/s.py:Session.__init__", "pkg/s.py:Session.request"), ("pkg/s.py", "pkg/s.py:Session.request")
    )
    assert not gold.same_key(("pkg/s.py:Session.request",), ("pkg/s.py:Session.closed",))
````

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_gold.py -q`

Expected: a collection error, because `bench.eval.gold` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 3: Write the key derivation**

Create `bench/eval/gold.py`:

````python
"""Answer keys from a fix patch: each changed line of the base file maps to its outermost enclosing function.

Rules (both languages):
- nested functions roll up to the outermost one;
- a change outside any function (class or module level) makes the file a file-only key, unless the same file
  already has a function key, which names the file anyway;
- blank lines, comments and import statements (`use` in Rust) are ignored, and so is test code: Python test
  files; Rust files under tests/, benches/ and examples/, `#[cfg(test)]` or `tests` modules and `#[test]` functions;
- Rust methods are `Type.method`, whatever trait they implement.

Hunks are located in the base file by their context, as `git apply` does, because SWE-bench line numbers are
often off by a few lines.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass

from unidiff import PatchSet

TEST_ATTR = re.compile(r"#\[\s*(?:cfg\s*\(\s*test\s*\)|(?:\w+::)*test)\s*\]")


@dataclass(frozen=True)
class Unit:
    qualname: str
    start: int  # first line, decorators and attributes included (1-based)
    end: int  # last line
    kind: str  # function | class | test-fn (a Rust test function) | test-mod (a Rust test module or impl)
    head: int = 0  # the `def`/`fn` line


def python_units(src: str) -> list[Unit]:
    out: list[Unit] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = prefix + child.name
                start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                out.append(Unit(q, start, child.end_lineno or child.lineno, kind, child.lineno))
                walk(child, q + ".")
            else:
                walk(child, prefix)

    walk(ast.parse(src), "")
    return out


def python_import_lines(src: str) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


_rust_parser = None


def rust_parser():
    global _rust_parser
    if _rust_parser is None:
        import tree_sitter_rust
        from tree_sitter import Language, Parser

        _rust_parser = Parser(Language(tree_sitter_rust.language()))
    return _rust_parser


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def rust_type_name(node, src: bytes) -> str:
    """The bare type an impl block is for: Foo for `Foo<T>`, `&'a Foo`, `crate::m::Foo` or `dyn Foo`."""
    if node is None:
        return "?"
    if node.type in ("generic_type", "reference_type", "pointer_type"):
        return rust_type_name(node.child_by_field_name("type"), src)
    if node.type == "scoped_type_identifier":
        return _text(node.child_by_field_name("name"), src)
    if node.type == "dynamic_type":
        return rust_type_name(node.child_by_field_name("trait"), src)
    return _text(node, src)


def _attributes(node) -> list:
    """The attribute items directly above an item (tree-sitter-rust makes them preceding siblings)."""
    attrs = []
    prev = node.prev_named_sibling
    while prev is not None and prev.type == "attribute_item":
        attrs.append(prev)
        prev = prev.prev_named_sibling
    return attrs


def rust_units(src: str) -> list[Unit]:
    data = src.encode()
    out: list[Unit] = []

    def walk(node, prefix: str, in_test: bool) -> None:
        for c in node.children:
            if c.type not in ("function_item", "impl_item", "trait_item", "mod_item"):
                walk(c, prefix, in_test)
                continue
            attrs = _attributes(c)
            test = in_test or any(TEST_ATTR.search(_text(a, data)) for a in attrs)
            start = min([a.start_point[0] for a in attrs] + [c.start_point[0]]) + 1
            end = c.end_point[0] + 1
            if c.type == "function_item":
                name = _text(c.child_by_field_name("name"), data)
                out.append(Unit(prefix + name, start, end, "test-fn" if test else "function", c.start_point[0] + 1))
                walk(c, prefix + name + ".", test)
            elif c.type == "impl_item":
                owner = rust_type_name(c.child_by_field_name("type"), data)
                if test:
                    out.append(Unit(owner, start, end, "test-mod"))
                walk(c, owner + ".", test)
            elif c.type == "trait_item":
                walk(c, _text(c.child_by_field_name("name"), data) + ".", test)
            else:  # mod_item: module names don't qualify, the file path already names the module
                name = _text(c.child_by_field_name("name"), data)
                test = test or name in ("tests", "test")
                if test:
                    out.append(Unit(name, start, end, "test-mod"))
                walk(c, "", test)

    walk(rust_parser().parse(data).root_node, "", False)
    return out


def rust_use_lines(src: str) -> set[int]:
    lines: set[int] = set()
    stack = [rust_parser().parse(src.encode()).root_node]
    while stack:
        n = stack.pop()
        if n.type in ("use_declaration", "extern_crate_declaration"):
            lines.update(range(n.start_point[0] + 1, n.end_point[0] + 2))
        else:
            stack.extend(n.children)
    return lines


FUNCTION = ("function",)
TEST = ("test-fn", "test-mod")


def outermost(units: list[Unit], line: int, kinds: tuple[str, ...] = FUNCTION) -> Unit | None:
    """The outermost unit of one of `kinds` that contains `line`."""
    best = None
    for u in units:
        if u.kind in kinds and u.start <= line <= u.end and (best is None or u.start < best.start):
            best = u
    return best


def is_test_path(path: str, lang: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if lang == "python":
        return (
            any(p in ("tests", "test", "testing") for p in parts[:-1])
            or name.startswith("test_")
            or name.endswith("_test.py")
            or name == "conftest.py"
        )
    return any(p in ("tests", "benches", "examples") for p in parts[:-1])


def _blank_or_comment(line: str, lang: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#" if lang == "python" else "//")


def _locate(hunk, base: list[str]) -> int:
    """How far the hunk's old lines sit from where its header says (0 when the header is right)."""
    old = [ln.value.rstrip("\r\n") for ln in hunk if ln.is_context or ln.is_removed]
    if not old:
        return 0
    start = hunk.source_start - 1
    for same in (lambda a, b: a.rstrip() == b.rstrip(), lambda a, b: a.strip() == b.strip()):
        for delta in sorted(range(-len(base), len(base) + 1), key=abs):
            s = start + delta
            if 0 <= s and s + len(old) <= len(base) and all(same(base[s + k], old[k]) for k in range(len(old))):
                return delta
    raise ValueError(f"hunk at line {hunk.source_start} does not apply")


def _apply(base: list[str], hunks: list) -> tuple[list[str], dict[tuple[int, int], int]]:
    """The patched file, and the new line number of each added line, keyed by (hunk, line in hunk)."""
    new: list[str] = []
    where: dict[tuple[int, int], int] = {}
    pos = 0
    for hi, (hunk, delta) in enumerate(hunks):
        first = (hunk.source_start - 1 if hunk.source_length else hunk.source_start) + delta
        new.extend(base[pos:first])
        pos = first
        for li, ln in enumerate(hunk):
            if ln.is_context:
                new.append(base[pos])
                pos += 1
            elif ln.is_removed:
                pos += 1
            elif ln.is_added:
                new.append(ln.value.rstrip("\r\n"))
                where[(hi, li)] = len(new)
    new.extend(base[pos:])
    return new, where


def _insertion_owner(units: list[Unit], before: int, added: list[str], base: list[str], lang: str) -> Unit | None:
    """The function that code inserted after line `before` belongs to, or None when it lands outside one."""
    f = outermost(units, before) if before else None
    if f is None:
        return None
    if lang == "rust":
        return f if before < f.end else None  # after the closing brace is outside
    first = next(v for v in added if v.strip())
    head = base[f.head - 1]
    return f if len(first) - len(first.lstrip()) > len(head) - len(head.lstrip()) else None


@dataclass(frozen=True)
class Key:
    entries: tuple[str, ...]  # sorted: "path:Qual.name" and "path"
    files: tuple[str, ...]  # the non-test source files the patch changes
    functions: int


def _finish(funcs: set[str], file_level: set[str]) -> tuple[str, ...]:
    """Drop file-only keys that a function key in the same file already implies."""
    named = {f.partition(":")[0] for f in funcs}
    return tuple(sorted(funcs | (file_level - named)))


def derive(patch: str, read: Callable[[str], str | None], lang: str) -> Key:
    """The answer key of `patch`; `read(path)` returns a file's text at the base commit (None if absent)."""
    ext = ".py" if lang == "python" else ".rs"
    units_of = python_units if lang == "python" else rust_units
    imports_of = python_import_lines if lang == "python" else rust_use_lines
    funcs: set[str] = set()
    file_level: set[str] = set()
    files: list[str] = []
    for pf in PatchSet(patch):
        path = pf.path
        if not path.endswith(ext) or is_test_path(path, lang):
            continue
        files.append(path)
        if pf.is_added_file or pf.is_removed_file:
            file_level.add(path)
            continue
        src = read(pf.source_file[2:] if pf.source_file.startswith("a/") else pf.source_file)
        if src is None:
            raise ValueError(f"{path} is missing at the base commit")
        base = src.split("\n")
        units = units_of(src)
        old_imports = imports_of(src)
        hunks = [(h, _locate(h, base)) for h in pf]
        new, where = _apply(base, hunks)
        try:
            new_imports = imports_of("\n".join(new))
        except SyntaxError:
            new_imports = set()

        def mark(f: Unit | None, path: str = path) -> None:
            if f:
                funcs.add(f"{path}:{f.qualname}")
            else:
                file_level.add(path)

        for hi, (hunk, delta) in enumerate(hunks):
            items = list(hunk)
            for i, ln in enumerate(items):
                if ln.is_removed:
                    line = ln.source_line_no + delta
                    if _blank_or_comment(ln.value, lang) or line in old_imports or outermost(units, line, TEST):
                        continue
                    mark(outermost(units, line))
                elif ln.is_added and (i == 0 or not items[i - 1].is_added):  # the first line of an insertion
                    run = []
                    for j in range(i, len(items)):
                        if not items[j].is_added:
                            break
                        if not (_blank_or_comment(items[j].value, lang) or where[(hi, j)] in new_imports):
                            run.append(items[j].value.rstrip("\r\n"))
                    if not run:
                        continue
                    before = next((x.source_line_no for x in reversed(items[:i]) if x.source_line_no), None)
                    before = (before if before is not None else hunk.source_start - 1) + delta
                    if before and outermost(units, before, TEST):
                        continue
                    mark(_insertion_owner(units, before, run, base, lang))
    return Key(_finish(funcs, file_level), tuple(dict.fromkeys(files)), len(funcs))


def normalize_listed(entries: list[str], read: Callable[[str], str | None]) -> tuple[str, ...]:
    """A Python key as czlll/SWE-bench_Lite's edit_functions lists it, under the rules above: classes become
    file-only keys and nested functions roll up. Raises ValueError for a name that isn't in the file."""
    funcs, file_level = set(), set()
    units_of: dict[str, list[Unit]] = {}
    for entry in entries:
        path, _, qual = entry.partition(":")
        if path not in units_of:
            src = read(path)
            if src is None:
                raise ValueError(f"{path} is missing at the base commit")
            units_of[path] = python_units(src)
        unit = next((u for u in units_of[path] if u.qualname == qual), None)
        if unit is None:
            raise ValueError(f"{entry} is not defined in the base file")
        if unit.kind == "class":
            file_level.add(path)
        else:
            funcs.add(f"{path}:{outermost(units_of[path], unit.head).qualname}")
    return _finish(funcs, file_level)


def same_key(derived: tuple[str, ...], listed: tuple[str, ...]) -> bool:
    """Equal up to one naming convention: czlll lists a class where the edit is in its __init__."""

    def canon(entries):
        funcs = {e for e in entries if ":" in e and not e.endswith(".__init__")}
        files = {e.partition(":")[0] for e in entries if ":" not in e or e.endswith(".__init__")}
        return _finish(funcs, files)

    return canon(derived) == canon(listed)
````

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_gold.py -q`

Expected: `16 passed`.

- [ ] **Step 5: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 6: Commit**

```bash
git add bench/eval/gold.py tests/test_eval_gold.py
git commit -F - <<'EOF'
feat: eval answer keys from fix patches

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 6: Pinned downloads

**Files:**
- Create: `bench/eval/datasets.py`
- Test: `tests/test_eval_datasets.py`

**Interfaces:**
- Consumes: `config.DATASETS`, `config.cache_dir()`.
- Produces, in `bench.eval.datasets`:
  - `sha256(path)`;
  - `download(url, dest, expected_sha256=None) -> Path`, which fetches once and verifies every time;
  - `rows(name, cache=None) -> list[dict]`;
  - `fetch_file(repo, commit, path, cache=None) -> str | None`, cached; a 404 is remembered;
  - `RAW_BASE`, which tests point at a local directory.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_datasets.py`:

````python
import hashlib
import urllib.error

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bench.eval import config, datasets


def test_download_verifies_the_checksum(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    good = hashlib.sha256(b"payload").hexdigest()
    dest = datasets.download(src.as_uri(), tmp_path / "cache" / "f.bin", good)
    assert dest.read_bytes() == b"payload"
    dest.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256"):
        datasets.download(src.as_uri(), dest, good)


def test_rows_reads_the_pinned_parquet(tmp_path, monkeypatch):
    src = tmp_path / "d.parquet"
    pq.write_table(pa.table({"instance_id": ["a", "b"], "n": [1, 2]}), src)
    ds = config.Dataset("o/d", "rev1", "data/d.parquet", datasets.sha256(src))
    monkeypatch.setitem(config.DATASETS, "tiny", ds)
    monkeypatch.setattr(config.Dataset, "url", property(lambda self: src.as_uri()))
    assert datasets.rows("tiny", tmp_path / "cache") == [{"instance_id": "a", "n": 1}, {"instance_id": "b", "n": 2}]


def test_fetch_file_caches_and_remembers_missing_files(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    (origin / "o/r/c1/pkg").mkdir(parents=True)
    (origin / "o/r/c1/pkg/a.py").write_text("x = 1\n")
    monkeypatch.setattr(datasets, "RAW_BASE", origin.as_uri())
    cache = tmp_path / "cache"
    assert datasets.fetch_file("o/r", "c1", "pkg/a.py", cache) == "x = 1\n"
    (origin / "o/r/c1/pkg/a.py").unlink()
    assert datasets.fetch_file("o/r", "c1", "pkg/a.py", cache) == "x = 1\n"  # served from the cache

    def not_found(url, dest, sha=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(datasets, "download", not_found)
    assert datasets.fetch_file("o/r", "c1", "pkg/gone.py", cache) is None
    monkeypatch.setattr(datasets, "download", lambda *a: pytest.fail("a known-missing file is not refetched"))
    assert datasets.fetch_file("o/r", "c1", "pkg/gone.py", cache) is None
````

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_datasets.py -q`

Expected: a collection error, because `bench.eval.datasets` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 3: Write the download module**

Create `bench/eval/datasets.py`:

````python
"""Pinned inputs: dataset files checked by SHA-256, and single source files fetched at a commit."""

from __future__ import annotations

import hashlib
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq

from . import config

USER_AGENT = "duckgrep-eval"
RAW_BASE = "https://raw.githubusercontent.com"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, expected_sha256: str | None = None) -> Path:
    """Fetch `url` to `dest` once; verify the checksum on every call so a corrupt cache is never used."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f"{dest.name}.{threading.get_ident()}.part")  # concurrent fetches never share one
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.rename(dest)
    if expected_sha256 and sha256(dest) != expected_sha256:
        raise ValueError(f"{dest} does not match its pinned SHA-256; delete it and retry")
    return dest


def rows(name: str, cache: Path | None = None) -> list[dict]:
    """Every row of a pinned dataset (config.DATASETS) as a dict."""
    ds = config.DATASETS[name]
    cache = cache or config.cache_dir()
    local = download(ds.url, cache / "datasets" / ds.repo.replace("/", "__") / ds.revision / ds.path, ds.sha256)
    return pq.read_table(local).to_pylist()


def fetch_file(repo: str, commit: str, path: str, cache: Path | None = None) -> str | None:
    """`path` at `commit` from GitHub, cached on disk; None if the file doesn't exist there."""
    cache = cache or config.cache_dir()
    local = cache / "raw" / repo.lower().replace("/", "__") / commit / path
    missing = local.with_name(local.name + ".missing")
    if missing.exists():
        return None
    if not local.exists():
        url = f"{RAW_BASE}/{repo}/{commit}/{path}"
        try:
            download(url, local)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            missing.parent.mkdir(parents=True, exist_ok=True)
            missing.touch()
            return None
    return local.read_text(encoding="utf-8", errors="replace")
````

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_datasets.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 6: Commit**

```bash
git add bench/eval/datasets.py tests/test_eval_datasets.py
git commit -F - <<'EOF'
feat: eval pinned dataset and source downloads

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 7: Localization tasks

**Files:**
- Create: `bench/eval/prompts.py`, `bench/eval/tasks/localization.py`
- Test: `tests/test_eval_localization.py`, `tests/test_eval_lite_keys.py` (opt-in, network)

**Interfaces:**
- Consumes: `gold.derive`, `gold.normalize_listed`, `gold.same_key`; `datasets.rows`, `datasets.fetch_file`; `suite.Task`; `config.DATASETS`, `LIVE_SINCE`, `EXCLUDED_RUST_REPOS`, `SEED`.
- Produces:
  - in `bench.eval.prompts`: `answer_format(answer, lang)`, `localization(problem_statement, lang)`, `structural(question, answer, lang)`;
  - in `bench.eval.tasks.localization`: `leaks(text, key)`, `select(candidates, n, per_repo, seed, accept)`, `python_tasks(n, per_repo, seed, cache=None)`, `rust_tasks(name, n, per_repo, seed, cache=None)` (with `name` either `multilingual` or `live`), and `build(seed=SEED, n_python=10, n_multilingual=5, n_live=5, per_repo_python=3, per_repo_rust=2, cache=None)`.

**The leak rule.** An issue leaks its answer if it names a key's file (also without a leading `src/` or `lib/`), a qualified key name (`Class.m` or `Type::m`), a code-like function name (one with an underscore or capitals), or a plain one followed by `(`. A plain English word such as `write` alone is not a leak.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_localization.py`:

````python
import pytest

from bench.eval import datasets
from bench.eval.suite import Task
from bench.eval.tasks import localization as loc

KEY = ("src/pkg/sessions.py:Session.merge_environment_settings", "src/pkg/sessions.py:write")


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("The bug is in src/pkg/sessions.py.", True),
        ("See pkg/sessions.py line 3.", True),  # the path without src/
        ("Session.merge_environment_settings ignores proxies", True),
        ("merge_environment_settings ignores proxies", True),  # a code-like name
        ("Please write the docs.", False),  # a plain word
        ("Calling s.write(data) fails", True),  # a plain name used as a call
        ("nothing relevant here", False),
    ],
)
def test_leaks(text, leaked):
    assert loc.leaks(text, KEY) == leaked


def test_leaks_rust_spelling_and_dunders():
    assert loc.leaks("Walker::new panics", ("src/walk.rs:Walker.new",))
    assert not loc.leaks("__init__ is slow", ("pkg/a.py:Thing.__init__",))
    assert loc.leaks("Thing.__init__ is slow", ("pkg/a.py:Thing.__init__",))


def rows(repo_sizes):
    return [{"instance_id": f"{r}-{i}", "repo": r} for r, n in repo_sizes.items() for i in range(n)]


def fake_task(row):
    return Task(row["instance_id"], "localization", "python", row["repo"], "c", "p", ("a.py:f",), "s")


def test_select_is_deterministic_round_robin_and_capped():
    cands = rows({"a/x": 5, "b/y": 5, "c/z": 1})
    picked = loc.select(cands, 5, 2, seed=7, accept=fake_task)
    again = loc.select(list(reversed(cands)), 5, 2, seed=7, accept=fake_task)
    assert [t.id for t in picked] == [t.id for t in again]
    repos = [t.repo for t in picked]
    assert len(picked) == 5 and max(repos.count(r) for r in set(repos)) == 2 and "c/z" in repos


def test_select_skips_rejected_candidates():
    cands = rows({"a/x": 3})
    picked = loc.select(cands, 2, 3, seed=1, accept=lambda r: None if r["instance_id"] == "a/x-0" else fake_task(r))
    assert len(picked) == 2 and "a/x-0" not in [t.id for t in picked]


BASE = "def merge_helper(x):\n    return x\n\n\ndef other():\n    return 2\n"
PATCH = (
    "diff --git a/pkg/m.py b/pkg/m.py\n--- a/pkg/m.py\n+++ b/pkg/m.py\n"
    "@@ -1,2 +1,2 @@\n def merge_helper(x):\n-    return x\n+    return x + 1\n"
)


def lite_row(iid, statement, listed):
    return {
        "instance_id": iid,
        "repo": "o/r",
        "base_commit": "c" * 40,
        "patch": PATCH,
        "problem_statement": statement,
        "edit_functions": listed,
    }


def test_python_tasks_cross_check_and_leak_filter(monkeypatch, tmp_path):
    table = [
        lite_row("o__r-1", "Values are off by one.", ["pkg/m.py:merge_helper"]),
        lite_row("o__r-2", "merge_helper returns the wrong value", ["pkg/m.py:merge_helper"]),  # leaks
        lite_row("o__r-3", "Values are off by one.", ["pkg/m.py:other"]),  # the two derivations disagree
    ]
    monkeypatch.setattr(datasets, "rows", lambda name, cache=None: table)
    monkeypatch.setattr(datasets, "fetch_file", lambda repo, commit, path, cache=None: BASE)
    tasks = loc.python_tasks(10, 3, seed=1, cache=tmp_path)
    assert [t.id for t in tasks] == ["o__r-1"]
    t = tasks[0]
    assert t.gold == ("pkg/m.py:merge_helper",) and t.stratum == "lite" and t.lang == "python"
    assert t.prompt.startswith("<issue>\nValues are off by one.\n</issue>") and '"locations"' in t.prompt


def test_rust_tasks_filter_on_date_repo_and_size(monkeypatch, tmp_path):
    base = "pub fn run() -> u32 {\n    1\n}\n"
    patch = (
        "diff --git a/src/lib.rs b/src/lib.rs\n--- a/src/lib.rs\n+++ b/src/lib.rs\n"
        "@@ -1,3 +1,3 @@\n pub fn run() -> u32 {\n-    1\n+    2\n }\n"
    )

    def row(iid, repo, created):
        return {
            "instance_id": iid,
            "repo": repo,
            "base_commit": "c" * 40,
            "patch": patch,
            "problem_statement": "It returns the wrong number.",
            "created_at": created,
        }

    table = [
        row("new-1", "o/r", "2026-07-01T00:00:00Z"),
        row("old-1", "o/r", "2025-01-01T00:00:00Z"),
        row("ruff-1", "astral-sh/ruff", "2026-07-01T00:00:00Z"),
    ]
    monkeypatch.setattr(datasets, "rows", lambda name, cache=None: table)
    monkeypatch.setattr(datasets, "fetch_file", lambda repo, commit, path, cache=None: base)
    live = loc.rust_tasks("live", 10, 2, seed=1, cache=tmp_path)
    assert [t.id for t in live] == ["new-1"] and live[0].gold == ("src/lib.rs:run",) and live[0].stratum == "live"
    assert sorted(t.id for t in loc.rust_tasks("multilingual", 10, 2, seed=1, cache=tmp_path)) == ["new-1", "old-1"]
````

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_localization.py -q`

Expected: a collection error, because `bench.eval.prompts` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 3: Write the prompts**

Create `bench/eval/prompts.py`:

````python
"""The exact text every setup's agent receives. Identical across setups; nothing names a tool."""

from __future__ import annotations

STYLE = {  # example path, example method, how to name a method
    "python": ("src/package/module.py", "Class.method", "Name a method by its class, as in `Class.method`."),
    "rust": ("src/module.rs", "Type.method", "Name a method by the type its impl block is for, as in `Type.method`."),
}


def answer_format(answer: str, lang: str) -> str:
    path, method, rule = STYLE[lang]
    if answer == "files":
        other = path.replace("module", "other")
        return (
            "End your reply with a fenced JSON block that lists each file by its path relative to the "
            f'repository root:\n\n```json\n{{"locations": ["{path}", "{other}"]}}\n```'
        )
    return (
        'End your reply with a fenced JSON block that lists each function as "path:Qualified.name", with the '
        f"path relative to the repository root. {rule}\n\n"
        f'```json\n{{"locations": ["{path}:{method}", "{path}:function"]}}\n```'
    )


def localization(problem_statement: str, lang: str) -> str:
    return (
        f"<issue>\n{problem_statement.strip()}\n</issue>\n\n"
        "Find the functions that must change to resolve this issue. Search the repository in the current "
        "directory, and do not edit any files.\n\n" + answer_format("functions", lang)
    )


def structural(question: str, answer: str, lang: str) -> str:
    return (
        f"{question}\n\nSearch the repository in the current directory, and do not edit any files.\n\n"
        + answer_format(answer, lang)
    )
````

- [ ] **Step 4: Write the localization builder**

Create `bench/eval/tasks/localization.py`:

````python
"""Issue-localization tasks: SWE-bench issues whose fix touches few functions, with keys derived from the fix."""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

from unidiff import PatchSet

from .. import config, datasets, gold, prompts
from ..suite import Task


def leaks(text: str, key: tuple[str, ...]) -> bool:
    """Would the issue text give the answer away? True if it names a key's file, a qualified key name, a
    code-like function name (with an underscore or capitals), or a plain one followed by `(`."""
    for entry in key:
        path, _, qual = entry.partition(":")
        short = path.split("/", 1)[1] if path.split("/", 1)[0] in ("src", "lib") and "/" in path else path
        if path in text or short in text:
            return True
        if not qual:
            continue
        last = qual.split(".")[-1]
        if "." in qual and (qual in text or qual.replace(".", "::") in text):
            return True
        dunder = last.startswith("__") and last.endswith("__")
        if not dunder and ("_" in last.strip("_") or last != last.lower()):
            if re.search(rf"(?<!\w){re.escape(last)}(?!\w)", text):
                return True
        elif re.search(rf"(?<![\w.]){re.escape(last)}\s*\(", text) or re.search(rf"\.{re.escape(last)}\s*\(", text):
            return True
    return False


def select(
    candidates: list[dict], n: int, per_repo: int, seed: int, accept: Callable[[dict], Task | None]
) -> list[Task]:
    """Up to `n` tasks, taking one acceptable candidate from each repo in turn (a seeded order) and at most
    `per_repo` from any repo. `accept` does the expensive checks and returns None to reject."""
    rng = random.Random(seed)
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for c in sorted(candidates, key=lambda c: c["instance_id"]):
        by_repo[c["repo"].lower()].append(c)
    repos = sorted(by_repo)
    rng.shuffle(repos)
    for r in repos:
        rng.shuffle(by_repo[r])
    picked: list[Task] = []
    taken: Counter[str] = Counter()
    while len(picked) < n and any(by_repo[r] and taken[r] < per_repo for r in repos):
        for r in repos:
            while by_repo[r] and taken[r] < per_repo:
                task = accept(by_repo[r].pop(0))
                if task is not None:
                    picked.append(task)
                    taken[r] += 1
                    break
            if len(picked) == n:
                break
    return picked


def _task(row: dict, lang: str, key: gold.Key, source: str, stratum: str) -> Task:
    return Task(
        id=row["instance_id"],
        kind="localization",
        lang=lang,
        repo=row["repo"],
        commit=row["base_commit"],
        prompt=prompts.localization(row["problem_statement"], lang),
        gold=key.entries,
        source=source,
        stratum=stratum,
    )


def python_tasks(n: int, per_repo: int, seed: int, cache: Path | None = None) -> list[Task]:
    ds = config.DATASETS["lite"]
    pool = [r for r in datasets.rows("lite", cache) if 1 <= len(r["edit_functions"]) <= 3]

    def accept(row: dict) -> Task | None:
        read = lambda p: datasets.fetch_file(row["repo"], row["base_commit"], p, cache)  # noqa: E731
        try:
            key = gold.derive(row["patch"], read, "python")
            listed = gold.normalize_listed(list(row["edit_functions"]), read)
        except (ValueError, SyntaxError):
            return None
        if not gold.same_key(key.entries, listed):
            return None  # the two derivations disagree: the key is uncertain
        if len(key.files) != 1 or not 1 <= key.functions <= 3 or leaks(row["problem_statement"], key.entries):
            return None
        return _task(row, "python", key, ds.label, "lite")

    return select(pool, n, per_repo, seed, accept)


def rust_tasks(name: str, n: int, per_repo: int, seed: int, cache: Path | None = None) -> list[Task]:
    """`name` is "multilingual" or "live" (post-cutoff issues only)."""
    ds = config.DATASETS[name]
    excluded = {r.lower() for r in config.EXCLUDED_RUST_REPOS}
    pool = [
        r
        for r in datasets.rows(name, cache)
        if r["repo"].lower() not in excluded and (name != "live" or r["created_at"] >= config.LIVE_SINCE)
    ]

    def accept(row: dict) -> Task | None:
        files = [p.path for p in PatchSet(row["patch"])]
        if not 1 <= sum(f.endswith(".rs") for f in files) <= 3 or len(files) > 5:
            return None
        read = lambda p: datasets.fetch_file(row["repo"], row["base_commit"], p, cache)  # noqa: E731
        try:
            key = gold.derive(row["patch"], read, "rust")
        except ValueError:
            return None
        if not 1 <= key.functions <= 10 or leaks(row["problem_statement"], key.entries):
            return None
        return _task(row, "rust", key, ds.label, name)

    return select(pool, n, per_repo, seed, accept)


def build(
    seed: int = config.SEED,
    n_python: int = 10,
    n_multilingual: int = 5,
    n_live: int = 5,
    per_repo_python: int = 3,
    per_repo_rust: int = 2,
    cache: Path | None = None,
) -> list[Task]:
    return (
        python_tasks(n_python, per_repo_python, seed, cache)
        + rust_tasks("multilingual", n_multilingual, per_repo_rust, seed, cache)
        + rust_tasks("live", n_live, per_repo_rust, seed, cache)
    )
````

- [ ] **Step 5: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_localization.py -q`

Expected: `12 passed`.

- [ ] **Step 6: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 7: Add the opt-in network check of the key rules against all 274 Lite instances**

It passed in the prototype and takes about a minute with a cold cache. Create `tests/test_eval_lite_keys.py`:

````python
"""Opt-in, needs the network: DUCKGREP_EVAL_NETWORK=1 uv run pytest tests/test_eval_lite_keys.py

Downloads SWE-bench Lite (czlll's copy) and each instance's edited file at its base commit, then checks the key
rules against czlll's edit_functions on every instance."""

import os

import pytest

from bench.eval import datasets, gold

pytestmark = pytest.mark.skipif(not os.environ.get("DUCKGREP_EVAL_NETWORK"), reason="set DUCKGREP_EVAL_NETWORK=1")


def test_derived_python_keys_match_czlll_on_every_lite_instance():
    disagree = []
    for row in datasets.rows("lite"):

        def read(path, row=row):
            return datasets.fetch_file(row["repo"], row["base_commit"], path)

        derived = gold.derive(row["patch"], read, "python").entries
        if not gold.same_key(derived, gold.normalize_listed(list(row["edit_functions"]), read)):
            disagree.append(row["instance_id"])
    assert disagree == []
````

Run `uv run pytest tests/test_eval_lite_keys.py -q` (expected: `1 skipped`), then `DUCKGREP_EVAL_NETWORK=1 DUCKGREP_EVAL_CACHE=/Volumes/research/code-tasks uv run pytest tests/test_eval_lite_keys.py -q` (expected: `1 passed`).

- [ ] **Step 8: Commit**

```bash
git add bench/eval/prompts.py bench/eval/tasks/localization.py tests/test_eval_localization.py tests/test_eval_lite_keys.py
git commit -F - <<'EOF'
feat: eval localization tasks from SWE-bench

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 8: Workspaces

**Files:**
- Create: `bench/eval/workspace.py`, `tests/eval_helpers.py`
- Test: `tests/test_eval_workspace.py`

**Interfaces:**
- Consumes: `config`, `datasets.download`, `setups.serena_home`, `setups.rust_env`.
- Produces, in `bench.eval.workspace`:
  - git: `git_env()`, `git(*args, cwd=None, check=True) -> str` (decoded leniently), `slug(repo)`;
  - clones and worktrees: `clone(repo, cache, url=None)`, `GROUPS`, `worktree_path(cache, setup, repo, commit)`, `worktree(repo, commit, setup, cache, url=None)`;
  - restoring: `changes(path)`, `restore(path) -> list[str]`;
  - disk: `free_gb(path)`, `require_space(path, minimum=MIN_FREE_GB)`;
  - preparing: `index_duckgrep(path) -> dict`, `rust_analyzer(cache) -> Path`, `serena_warmup(path, repo, lang, cache) -> dict`, `serena_project_file(cache, path)`, `prepare(tasks, setup_names, cache, log=print) -> list[dict]`.
- In `tests/eval_helpers.py`: `origin(tmp_path, files, name="origin") -> (Path, commit)`, a committed local repo that stands in for GitHub.

- [ ] **Step 1: Write the test helper**

Create `tests/eval_helpers.py`:

````python
"""Small git repos standing in for GitHub in the eval tests."""

import subprocess

from bench.eval import workspace


def origin(tmp_path, files, name="origin"):
    """A committed repo at tmp_path/name; returns (path, commit)."""
    root = tmp_path / name
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(root)]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    return root, workspace.git("rev-parse", "HEAD", cwd=root).strip()
````
- [ ] **Step 2: Write the failing test**

Create `tests/test_eval_workspace.py`:

````python
import pytest
from eval_helpers import origin

from bench.eval import workspace
from bench.eval.suite import Task

FILES = {"pkg/__init__.py": "", "pkg/a.py": "def f():\n    return 1\n"}


def test_each_setup_gets_its_own_neutrally_named_worktree(tmp_path):
    src, commit = origin(tmp_path, FILES)
    cache = tmp_path / "cache"
    paths = {s: workspace.worktree("o/r", commit, s, cache, url=str(src)) for s in ("baseline", "duckgrep", "serena")}
    assert len(set(paths.values())) == 3
    for p in paths.values():
        assert (p / "pkg/a.py").read_text() == FILES["pkg/a.py"]
        assert not {"baseline", "duckgrep", "serena", "eval"} & set(str(p.relative_to(cache)).replace("/", " ").split())
    assert workspace.worktree("o/r", commit, "baseline", cache, url=str(src)) == paths["baseline"]  # made once


def test_restore_reverts_what_a_run_changed_but_keeps_ignored_files(tmp_path):
    src, commit = origin(tmp_path, {**FILES, ".gitignore": ".cache/\n"})
    wt = workspace.worktree("o/r", commit, "baseline", tmp_path / "cache", url=str(src))
    (wt / "pkg/a.py").write_text("changed\n")
    (wt / "notes.txt").write_text("scratch\n")
    (wt / ".cache").mkdir()
    (wt / ".cache/x").write_text("kept\n")
    assert sorted(workspace.restore(wt)) == [" M pkg/a.py", "?? notes.txt"]
    assert (wt / "pkg/a.py").read_text() == FILES["pkg/a.py"] and not (wt / "notes.txt").exists()
    assert (wt / ".cache/x").exists() and workspace.restore(wt) == []


def test_require_space(tmp_path):
    workspace.require_space(tmp_path, 0.001)
    with pytest.raises(RuntimeError, match="DUCKGREP_EVAL_CACHE"):
        workspace.require_space(tmp_path, 10**9)


def test_prepare_builds_the_duckgrep_index_once(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, FILES)
    real = workspace.worktree
    monkeypatch.setattr(workspace, "worktree", lambda repo, c, s, cache: real(repo, c, s, cache, url=str(src)))
    task = Task("t", "localization", "python", "o/r", commit, "p", ("pkg/a.py:f",), "s")
    built = workspace.prepare([task], ["baseline", "duckgrep"], tmp_path / "cache", log=lambda _: None)
    assert [b["setup"] for b in built] == ["duckgrep"] and built[0]["index_mb"] > 0
    wt = workspace.worktree_path(tmp_path / "cache", "duckgrep", "o/r", commit)
    assert (wt / ".duckgrep" / "index.duckdb").exists() and workspace.changes(wt) == []
    assert workspace.prepare([task], ["baseline", "duckgrep"], tmp_path / "cache", log=lambda _: None) == []


def test_a_commit_the_clone_lacks_is_fetched_by_id(tmp_path):
    src, first = origin(tmp_path, FILES)
    cache = tmp_path / "cache"
    workspace.worktree("o/r", first, "baseline", cache, url=str(src))  # clones
    (src / "pkg/a.py").write_text("def f():\n    return 2\n")
    git = ["-c", "user.name=t", "-c", "user.email=t@t"]
    workspace.git(*git, "commit", "-q", "-am", "second", cwd=src)
    second = workspace.git("rev-parse", "HEAD", cwd=src).strip()
    wt = workspace.worktree("o/r", second, "baseline", cache, url=str(src))
    assert (wt / "pkg/a.py").read_text() == "def f():\n    return 2\n"
````

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_workspace.py -q`

Expected: a collection error, because `bench.eval.workspace` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 4: Write the workspace module**

Create `bench/eval/workspace.py`:

````python
"""Local copies of the task repos: one full clone per repo, one worktree per setup and commit, the duckgrep
index and Serena warm-up for each worktree, and the pinned rust-analyzer."""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import config, datasets, setups


def git_env() -> dict[str, str]:
    """The environment without GIT_* variables: a commit hook exports GIT_DIR, which would redirect every git call."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, env=git_env(), capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8", "replace")  # repos hold files in other encodings


def slug(repo: str) -> str:
    return repo.lower().replace("/", "__")


def clone(repo: str, cache: Path, url: str | None = None) -> Path:
    """A full bare clone of `repo`, made once. Not a partial clone: duckgrep indexes `git log --numstat`, and an
    agent may read history too, and in a blobless clone each old blob is a separate network fetch."""
    dest = cache / "repos" / f"{slug(repo)}.git"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        if tmp.exists():
            shutil.rmtree(tmp)
        git("clone", "--bare", url or f"https://github.com/{repo}.git", str(tmp))
        tmp.rename(dest)
    return dest


# The agent sees its working directory, so the path to a worktree must not name the setup or the tool under test.
GROUPS = {"baseline": "t1", "duckgrep": "t2", "serena": "t3"}


def worktree_path(cache: Path, setup: str, repo: str, commit: str) -> Path:
    return cache / "wt" / GROUPS.get(setup, setup) / f"{slug(repo)}@{commit[:12]}"


def worktree(repo: str, commit: str, setup: str, cache: Path, url: str | None = None) -> Path:
    """A detached worktree of `repo` at `commit` for one setup, so no setup sees files another's tools wrote."""
    path = worktree_path(cache, setup, repo, commit)
    if (path / ".git").exists():
        return path
    bare = clone(repo, cache, url)
    if git("cat-file", "-t", commit, cwd=bare, check=False).strip() != "commit":
        git("fetch", "origin", commit, cwd=bare)
    path.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "--detach", "--force", str(path), commit, cwd=bare)
    return path


def changes(path: Path) -> list[str]:
    """Tracked or untracked changes, ignored files excepted (so .duckgrep/ is not a change)."""
    return [line for line in git("status", "--porcelain", "--untracked-files=all", cwd=path).splitlines() if line]


def restore(path: Path) -> list[str]:
    """Put a worktree back to its commit after a run; return what the run had changed."""
    found = changes(path)
    if found:
        git("checkout", "--force", "HEAD", "--", ".", cwd=path)
        git("clean", "-fd", cwd=path)
    return found


def free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1e9


def require_space(path: Path, minimum: float = config.MIN_FREE_GB) -> None:
    free = free_gb(path)
    if free < minimum:
        raise RuntimeError(f"only {free:.1f} GB free under {path}; need {minimum} GB (set DUCKGREP_EVAL_CACHE)")


def index_duckgrep(path: Path) -> dict:
    """Build the duckgrep index of a worktree; report the build time and index size."""
    t = time.monotonic()
    subprocess.run(
        [sys.executable, "-m", "duckgrep", "-C", str(path), "index"], check=True, env=git_env(), capture_output=True
    )
    db = path / ".duckgrep" / "index.duckdb"
    return {"index_seconds": round(time.monotonic() - t, 1), "index_mb": round(db.stat().st_size / 1e6, 1)}


def rust_analyzer(cache: Path) -> Path:
    """The pinned standalone rust-analyzer, plus a `rustup` stub beside it that makes Serena use it rather than
    asking rustup (whose proxy on this machine is broken, and must not be changed)."""
    bindir = cache / "bin"
    exe = bindir / "rust-analyzer"
    if not exe.exists():
        gz = datasets.download(
            config.RUST_ANALYZER_URL, cache / "downloads" / "rust-analyzer.gz", config.RUST_ANALYZER_GZ_SHA256
        )
        bindir.mkdir(parents=True, exist_ok=True)
        with gzip.open(gz) as src, open(exe, "wb") as dst:
            shutil.copyfileobj(src, dst)
        exe.chmod(0o755)
        stub = bindir / "rustup"
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)
    version = subprocess.run([str(exe), "--version"], capture_output=True, text=True).stdout.strip()
    if version != config.RUST_ANALYZER_VERSION:
        raise RuntimeError(f"unexpected rust-analyzer: {version!r}")
    return exe


def serena_warmup(path: Path, repo: str, lang: str, cache: Path) -> dict:
    """Create the Serena project with its language pinned (auto-detection asks a question on stdin when a repo
    has files in a second language) and fill its symbol cache, which lives outside the worktree."""
    (cache / "tmp").mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(dir=cache / "tmp") as tmp:
        env = {**git_env(), "SERENA_HOME": str(setups.serena_home(Path(tmp) / "home", cache))}
        env.update(setups.rust_env(cache, repo))
        argv = [shutil.which("uvx") or "uvx", "--from", config.SERENA, "serena", "project", "index", str(path)]
        subprocess.run(
            argv + ["--language", lang, "--log-level", "WARNING"],
            env=env,
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=3600,
        )
    return {"serena_seconds": round(time.monotonic() - started, 1)}


def serena_project_file(cache: Path, path: Path) -> Path:
    return cache / "serena-projects" / path.name / ".serena" / "project.yml"


def prepare(tasks, setup_names: list[str], cache: Path, log=print) -> list[dict]:
    """Worktrees for every (repo, commit, setup), with the duckgrep index or the Serena warm-up. Returns what was
    newly built, with its cost; what exists already is left alone."""
    built = []
    todo = sorted({(t.repo, t.commit, t.lang) for t in tasks})
    if "serena" in setup_names and any(lang == "rust" for *_, lang in todo):
        rust_analyzer(cache)
    for repo, commit, lang in todo:
        for setup in setup_names:
            require_space(cache)
            path = worktree(repo, commit, setup, cache)
            row: dict = {}
            if setup == "duckgrep" and not (path / ".duckgrep" / "index.duckdb").exists():
                row = index_duckgrep(path)
            elif setup == "serena" and not serena_project_file(cache, path).exists():
                row = serena_warmup(path, repo, lang, cache)
            leftover = changes(path)
            if leftover:
                raise RuntimeError(f"preparing {path} changed it: {leftover[:5]}")
            if row:
                built.append({"setup": setup, "repo": repo, "commit": commit, **row})
                log(f"prepared {setup} {repo}@{commit[:12]}: {row}")
    return built
````

- [ ] **Step 5: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_workspace.py -q`

Expected: `5 passed`.

- [ ] **Step 6: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 7: Commit**

```bash
git add bench/eval/workspace.py tests/eval_helpers.py tests/test_eval_workspace.py
git commit -F - <<'EOF'
feat: eval workspaces: clones, worktrees, indexes, Serena warm-up

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 9: Structural questions

**Files:**
- Create: `bench/eval/scip_pb2.py` (vendored), `bench/eval/tasks/structural.py`
- Test: `tests/test_eval_structural.py`

**Interfaces:**
- Consumes:
  - from `gold`: `python_units`, `rust_units`, `rust_parser`, `outermost`, `is_test_path`;
  - from `workspace`: `git`, `worktree`, `slug`, `rust_analyzer`;
  - `setups.rust_env`, `prompts.structural`, `suite.Task`;
  - from `config`: `STRUCTURAL_REPOS`, `RUST_ANALYZER_VERSION`, `SEED`.
- Produces `build(seed=SEED, cache=None) -> list[Task]`: per pinned repo, callers (1 common name, 1 unique), two-hop (1 common, 1 unique) and importers (1). The pieces behind it:
  - records: `Def`, `Site`, `Module`, `Analysis` (with `.callers(d)` and `.def_of(path, qualname)`);
  - analysis: `python_analysis(root)`, `python_module(path)`, `scip_index(root, repo, commit, cache)`, `rust_analysis(root, index)`;
  - questions: `callers_question`, `two_hop_question`, `importers_question`, `common_names`, `pick`, `repo_tasks`;
  - checks: `grep_hits`, `grep_confirms`.

- [ ] **Step 1: Vendor the SCIP protobuf module**

`scip_pb2.py` is protoc output for SCIP's `scip.proto`, for Protobuf Python 7.35.1. It is copied from the research directory and not linted:

```bash
cp /private/tmp/claude-501/-Users-ccf-git-duckgrep/942c1d79-b7dc-4ffe-92a4-a352d55aef56/scratchpad/research/tasks-and-methodology/ra/scip_pb2.py bench/eval/scip_pb2.py
shasum -a 256 bench/eval/scip_pb2.py   # 70c8e8d5884ea4fb29993bced28c1922cd780ad617793981366cc2ce9f0250a9
```

If the copy is gone, regenerate it from the same `scip.proto` (SHA-256 `b38021b65ef90cbbf6af9c829ff75192859ad9b5da05439ef154bea4ceb2bf03`, from github.com/scip-code/scip) with a protoc whose Python output targets protobuf 7.35.1, and keep its generated header.

- [ ] **Step 2: Write the failing test**

Create `tests/test_eval_structural.py`:

````python
import subprocess

from bench.eval import scip_pb2
from bench.eval.tasks import structural as st

CORE = """class Store:
    def get(self, key):
        return key


def load(store: Store):
    return store.get("a")


def run():
    s = Store()
    return s.get("b")
"""

OTHER = """from pkg.core import Store, run


class Cache:
    def get(self, key):
        return None


def warm(c: Cache):
    return c.get("x")


def main():
    return run()
"""

TEST = """from pkg import core


def test_run():
    assert core.run() == "b"
"""


def repo(tmp_path, files):
    root = tmp_path / "repo"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def python_repo(tmp_path, extra=""):
    return repo(
        tmp_path,
        {"pkg/__init__.py": "", "pkg/core.py": CORE + extra, "pkg/other.py": OTHER, "tests/test_core.py": TEST},
    )


def defn(a, qualname):
    return next(d for d in a.defs if d.qualname == qualname)


def test_python_callers_resolve_through_jedi(tmp_path):
    a = st.python_analysis(python_repo(tmp_path))
    assert "get" in st.common_names(a)
    assert st._entries(a.callers(defn(a, "Store.get"))) == {"pkg/core.py:load", "pkg/core.py:run"}
    assert st._entries(a.callers(defn(a, "Cache.get"))) == {"pkg/other.py:warm"}


def test_an_unresolved_call_site_makes_the_key_untrusted(tmp_path):
    a = st.python_analysis(python_repo(tmp_path, '\n\ndef mystery(x):\n    return x.get("z")\n'))
    assert a.callers(defn(a, "Store.get")) is None


def test_python_questions(tmp_path):
    root = python_repo(tmp_path)
    a = st.python_analysis(root)
    q, key = st.callers_question(a, defn(a, "Store.get"), "python")
    assert q == "Which functions call `Store.get`, defined in `pkg/core.py`? List every one, including test functions."
    assert key == ("pkg/core.py:load", "pkg/core.py:run")
    q, key = st.two_hop_question(a, defn(a, "Store.get"), "python")
    assert "directly or through one intermediate function" in q
    assert key == ("pkg/core.py:load", "pkg/core.py:run", "pkg/other.py:main", "tests/test_core.py:test_run")
    module = next(m for m in a.modules if m.file == "pkg/core.py")
    assert module.importers == {"pkg/other.py": [1], "tests/test_core.py": [1]}
    q, key = st.importers_question(a, module, "python")
    assert q.startswith("Which files import the module `pkg.core`, or import names from it?")
    assert key == ("pkg/other.py", "tests/test_core.py")


def test_python_module_names():
    assert st.python_module("src/requests/utils.py") == "requests.utils"
    assert st.python_module("pkg/__init__.py") == "pkg"
    assert st.python_module("README.md") is None


MAIN_RS = """mod walk;
use crate::walk::Walker;

fn main() {
    let w = Walker::new();
    helper();
}

fn helper() {
    Walker::new();
}
"""

WALK_RS = """pub struct Walker;

impl Walker {
    pub fn new() -> Self {
        Walker
    }
}

#[cfg(windows)]
fn win() {
    Walker::new();
}
"""

NEW = "rust-analyzer cargo demo 0.1.0 walk/Walker#new()."
HELPER = "rust-analyzer cargo demo 0.1.0 helper()."
MODULE = "rust-analyzer cargo demo 0.1.0 walk/"


def occurrence(doc, text, line, word, symbol, nth=0, definition=False):
    """An occurrence of `word` (its nth appearance on 0-based `line` of `text`)."""
    cols = [i for i in range(len(text.split("\n")[line])) if text.split("\n")[line].startswith(word, i)]
    o = doc.occurrences.add()
    o.range.extend([line, cols[nth], cols[nth] + len(word)])
    o.symbol = symbol
    o.symbol_roles = scip_pb2.SymbolRole.Definition if definition else 0


def rust_repo(tmp_path, resolve_windows_call):
    root = repo(tmp_path, {"src/main.rs": MAIN_RS, "src/walk.rs": WALK_RS})
    idx = scip_pb2.Index()
    main = idx.documents.add()
    main.relative_path = "src/main.rs"
    occurrence(main, MAIN_RS, 0, "walk", MODULE, definition=True)
    occurrence(main, MAIN_RS, 1, "walk", MODULE)
    occurrence(main, MAIN_RS, 4, "new", NEW)
    occurrence(main, MAIN_RS, 5, "helper", HELPER)
    occurrence(main, MAIN_RS, 8, "helper", HELPER, definition=True)
    occurrence(main, MAIN_RS, 9, "new", NEW)
    walk = idx.documents.add()
    walk.relative_path = "src/walk.rs"
    occurrence(walk, WALK_RS, 3, "new", NEW, definition=True)
    if resolve_windows_call:  # rust-analyzer leaves code behind an inactive cfg unresolved
        occurrence(walk, WALK_RS, 10, "new", NEW)
    path = tmp_path / "index.scip"
    path.write_bytes(idx.SerializeToString())
    return root, path


def test_rust_callers_from_scip(tmp_path):
    root, index = rust_repo(tmp_path, resolve_windows_call=True)
    a = st.rust_analysis(root, index)
    new = defn(a, "Walker.new")
    assert st._entries(a.callers(new)) == {"src/main.rs:main", "src/main.rs:helper", "src/walk.rs:win"}
    q, _ = st.callers_question(a, new, "rust")
    assert q.startswith("Which functions call `Walker::new`, defined in `src/walk.rs`?")


def test_rust_call_site_without_an_occurrence_makes_the_key_untrusted(tmp_path):
    root, index = rust_repo(tmp_path, resolve_windows_call=False)
    a = st.rust_analysis(root, index)
    assert a.callers(defn(a, "Walker.new")) is None


def test_rust_module_importers(tmp_path):
    root, index = rust_repo(tmp_path, resolve_windows_call=True)
    a = st.rust_analysis(root, index)
    [module] = a.modules
    assert (module.file, module.grep, module.importers) == ("src/walk.rs", "walk", {"src/main.rs": [2]})
    assert st.importers_question(a, module, "rust") is None  # one importer is too few
````

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_structural.py -q`

Expected: a collection error, because `bench.eval.tasks.structural` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 4: Write the structural builder**

Create `bench/eval/tasks/structural.py`:

````python
"""Structural questions (callers, two-hop callers, importers) with keys from static analysis.

Python keys come from jedi, Rust keys from rust-analyzer's SCIP index. A key is kept only when it is complete:
every call site of the target's name must resolve, to the target or to something else. A site the analyzer can't
resolve (dynamic dispatch, code behind an inactive cfg) might be a caller the key would miss, so such a question is
dropped. Each key is also checked against `git grep -w`: every site in it must be one of grep's hits.
"""

from __future__ import annotations

import ast
import os
import random
import subprocess
import sysconfig
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, gold, prompts, setups, workspace
from ..suite import Task

CALLERS_SIZE = (2, 15)
TWO_HOP_SIZE = (3, 25)
IMPORTERS_SIZE = (2, 15)
FUNCTION_LIKE = ("function", "test-fn")


@dataclass(frozen=True)
class Def:
    path: str
    qualname: str  # Session.request; in Rust, Type.method
    line: int  # 1-based line of the name
    col: int  # 0-based column of the name: characters for Python, bytes for Rust
    test: bool = False  # defined in test code

    @property
    def name(self) -> str:
        return self.qualname.split(".")[-1]


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    col: int
    caller: str | None  # qualname of the outermost enclosing function; None at module level


@dataclass
class Module:
    file: str  # the file that defines it
    label: str  # how a question names it
    grep: str  # the word its imports contain
    importers: dict[str, list[int]]  # importing file -> lines of the import


@dataclass
class Analysis:
    root: Path
    defs: list[Def] = field(default_factory=list)
    calls: dict[str, list[Site]] = field(default_factory=lambda: defaultdict(list))  # by the called name
    modules: list[Module] = field(default_factory=list)
    resolve: Callable[[Site], set | None] = lambda site: None  # what a site refers to; None when unknown
    identity: Callable[[Def], object] = lambda d: None  # what `resolve` returns for a site that calls d

    def callers(self, d: Def) -> set[Site] | None:
        """The call sites of `d`; None when the set can't be trusted to be complete."""
        mine = self.identity(d)
        if mine is None:
            return None
        found = set()
        for site in self.calls.get(d.name, []):
            got = self.resolve(site)
            if not got:
                return None  # unresolved: it might call d
            if mine in got:
                if len(got) > 1 or site.caller is None:
                    return None  # ambiguous, or a call from module level, which no function answers
                found.add(site)
        return found

    def def_of(self, path: str, qualname: str) -> Def | None:
        return next((d for d in self.defs if d.path == path and d.qualname == qualname), None)


def grep_hits(root: Path, word: str) -> set[tuple[str, int]]:
    """(path, line) of every `git grep -w word` hit. -z separates the fields with NULs, since matched lines may
    hold colons, form feeds or other characters that would split them."""
    out = workspace.git("grep", "-n", "-z", "-w", "-I", "--no-color", "-e", word, cwd=root, check=False)
    hits = set()
    for record in out.split("\n"):
        fields = record.split("\0", 2)
        if len(fields) == 3 and fields[1].isdigit():
            hits.add((fields[0], int(fields[1])))
    return hits


def grep_confirms(root: Path, word: str, places: set[tuple[str, int]]) -> bool:
    return places <= grep_hits(root, word)


# ---- Python: ast finds definitions, call sites and imports; jedi says what each call site refers to ----


def python_module(path: str) -> str | None:
    """The dotted module a file defines: src/requests/utils.py -> requests.utils."""
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[0] in ("src", "lib") and len(parts) > 1:
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or None


def _imported(tree: ast.AST, rel: str) -> list[tuple[str, int]]:
    """(module, line) for every module an import statement in the file names, relative imports resolved:
    `from a.b import c` names a.b and a.b.c (c may be a submodule); `import a.b` names a.b."""
    own = python_module(rel) or ""
    package = own if rel.endswith("__init__.py") else own.rpartition(".")[0]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(al.name, al.lineno) for al in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - node.level + 1] if node.level > 1 else parts
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            out.append((base, node.lineno))
            out += [(f"{base}.{al.name}", al.lineno) for al in node.names if al.name != "*"]
    return out


def python_analysis(root: Path) -> Analysis:
    import jedi

    a = Analysis(root)
    sources: dict[str, str] = {}
    imports: dict[str, list[tuple[str, int]]] = {}
    for rel in [f for f in workspace.git("ls-files", "*.py", cwd=root).splitlines() if f]:
        src = (root / rel).read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        sources[rel] = src
        imports[rel] = _imported(tree, rel)
        lines = src.split("\n")
        units = gold.python_units(src)
        test = gold.is_test_path(rel, "python")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                top = gold.outermost(units, node.lineno)
                if top is None or top.head != node.lineno:
                    continue  # nested: only its parent calls it
                text = lines[node.lineno - 1]
                a.defs.append(Def(rel, top.qualname, node.lineno, text.index(node.name, text.index("def") + 3), test))
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    name, line, bcol = f.id, f.lineno, f.col_offset
                elif isinstance(f, ast.Attribute) and f.end_lineno is not None and f.end_col_offset is not None:
                    name, line, bcol = f.attr, f.end_lineno, f.end_col_offset - len(f.attr.encode())
                else:
                    continue
                col = len(lines[line - 1].encode()[:bcol].decode("utf-8", "replace"))
                caller = gold.outermost(units, line)
                a.calls[name].append(Site(rel, line, col, caller.qualname if caller else None))

    for rel in sources:
        module = python_module(rel)
        in_package = rel.endswith("__init__.py") or (root / rel).with_name("__init__.py").exists()
        if module and in_package and not gold.is_test_path(rel, "python"):
            found: dict[str, list[int]] = defaultdict(list)
            for other, names in imports.items():
                for name, line in names:
                    if name == module and other != rel:
                        found[other].append(line)
            a.modules.append(Module(rel, f"the module `{module}`", module.split(".")[-1], dict(found)))

    paths = sysconfig.get_paths()
    src_dir = root / "src"
    # the repo's own code and the standard library only: never an installed copy of the package
    sys_path = [str(src_dir if src_dir.is_dir() else root)] + sorted(
        {paths["stdlib"], paths["platstdlib"], os.path.join(paths["stdlib"], "lib-dynload")}
    )
    project = jedi.Project(str(root), sys_path=sys_path, smart_sys_path=False)
    scripts: dict[str, jedi.Script] = {}
    memo: dict[Site, set | None] = {}

    def resolve(site: Site) -> set | None:
        if site not in memo:
            if site.path not in scripts:
                scripts[site.path] = jedi.Script(sources[site.path], path=str(root / site.path), project=project)
            try:
                names = scripts[site.path].goto(site.line, site.col, follow_imports=True)
            except Exception:
                names = []
            got = set()
            for n in names:
                try:
                    where = str(Path(n.module_path).relative_to(root)) if n.module_path else "<builtin>"
                except ValueError:
                    where = "<external>"
                got.add((where, n.line))
            memo[site] = got or None
        return memo[site]

    a.resolve = resolve
    a.identity = lambda d: (d.path, d.line)
    return a


# ---- Rust: tree-sitter finds definitions, call sites and `use` declarations; SCIP says what each refers to ----


def scip_index(root: Path, repo: str, commit: str, cache: Path) -> Path:
    """rust-analyzer's SCIP index of a worktree, made once (about a minute for fd)."""
    out = cache / "scip" / f"{workspace.slug(repo)}@{commit[:12]}.scip"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        exe = workspace.rust_analyzer(cache)
        env = {k: os.environ[k] for k in ("HOME", "USER", "TMPDIR") if k in os.environ}
        env.update(setups.rust_env(cache, repo))
        tmp = out.with_suffix(".part")
        subprocess.run(
            [str(exe), "scip", str(root), "--output", str(tmp)], env=env, check=True, capture_output=True, timeout=3600
        )
        tmp.rename(out)
    return out


def _callee(node):
    """The identifier a call expression calls: f, x.f, T::f or f::<T>."""
    if node is None:
        return None
    if node.type == "identifier":
        return node
    if node.type == "field_expression":
        return node.child_by_field_name("field")
    if node.type == "scoped_identifier":
        return node.child_by_field_name("name")
    if node.type == "generic_function":
        return _callee(node.child_by_field_name("function"))
    return None


def _mod_file(path: str, name: str, root: Path) -> str | None:
    """The file `mod name;` in `path` loads: <dir>/name.rs or <dir>/name/mod.rs."""
    p = Path(path)
    base = p.parent if p.name in ("mod.rs", "lib.rs", "main.rs") else p.parent / p.stem
    for cand in (base / f"{name}.rs", base / name / "mod.rs"):
        if (root / cand).is_file():
            return str(cand)
    return None


def rust_analysis(root: Path, index: Path) -> Analysis:
    from .. import scip_pb2

    idx = scip_pb2.Index()
    idx.ParseFromString(index.read_bytes())
    at: dict[tuple[str, int, int], set[str]] = defaultdict(set)  # (path, line, column) -> symbols there
    where: dict[str, list[tuple[str, int]]] = defaultdict(list)  # symbol -> (path, line) of its occurrences
    for doc in idx.documents:
        for o in doc.occurrences:
            at[(doc.relative_path, o.range[0] + 1, o.range[1])].add(o.symbol)
            where[o.symbol].append((doc.relative_path, o.range[0] + 1))
    a = Analysis(root)
    uses: dict[str, list[tuple[int, int]]] = {}
    mods: list[tuple[str, str, str]] = []  # (symbol, module file, module name)
    for rel in [f for f in workspace.git("ls-files", "*.rs", cwd=root).splitlines() if f]:
        src = (root / rel).read_text(encoding="utf-8", errors="replace")
        data = src.encode()
        units = gold.rust_units(src)
        test_file = gold.is_test_path(rel, "rust")
        uses[rel] = []
        stack = [gold.rust_parser().parse(data).root_node]
        while stack:
            n = stack.pop()
            stack.extend(n.children)
            if n.type == "function_item":
                name = n.child_by_field_name("name")
                top = gold.outermost(units, name.start_point[0] + 1, FUNCTION_LIKE)
                if top is not None and top.head == n.start_point[0] + 1:
                    test = test_file or top.kind == "test-fn"
                    a.defs.append(Def(rel, top.qualname, name.start_point[0] + 1, name.start_point[1], test))
            elif n.type == "call_expression":
                ident = _callee(n.child_by_field_name("function"))
                if ident is not None:
                    line = ident.start_point[0] + 1
                    caller = gold.outermost(units, line, FUNCTION_LIKE)
                    called = data[ident.start_byte : ident.end_byte].decode()
                    a.calls[called].append(Site(rel, line, ident.start_point[1], caller.qualname if caller else None))
            elif n.type == "use_declaration":
                uses[rel].append((n.start_point[0] + 1, n.end_point[0] + 1))
            elif n.type == "mod_item" and n.child_by_field_name("body") is None:
                name = n.child_by_field_name("name")
                word = data[name.start_byte : name.end_byte].decode()
                symbols = at.get((rel, name.start_point[0] + 1, name.start_point[1]), set())
                target = _mod_file(rel, word, root)
                if len(symbols) == 1 and target:
                    mods.append((next(iter(symbols)), target, word))
    for symbol, file, word in mods:
        found: dict[str, list[int]] = defaultdict(list)
        for path, line in where.get(symbol, []):
            if path != file and any(s <= line <= e for s, e in uses.get(path, [])):
                found[path].append(line)
        a.modules.append(Module(file, f"the module defined in `{file}`", word, dict(found)))

    def identity(d: Def):
        symbols = at.get((d.path, d.line, d.col), set())
        return next(iter(symbols)) if len(symbols) == 1 else None

    a.resolve = lambda site: at.get((site.path, site.line, site.col)) or None
    a.identity = identity
    return a


# ---- questions ----


def _display(d: Def, lang: str) -> str:
    return d.qualname.replace(".", "::") if lang == "rust" else d.qualname


def _entries(sites: set[Site]) -> set[str]:
    return {f"{s.path}:{s.caller}" for s in sites}


def callers_question(a: Analysis, d: Def, lang: str) -> tuple[str, tuple[str, ...]] | None:
    sites = a.callers(d)
    if sites is None or not grep_confirms(a.root, d.name, {(s.path, s.line) for s in sites}):
        return None
    key = tuple(sorted(_entries(sites)))
    if not CALLERS_SIZE[0] <= len(key) <= CALLERS_SIZE[1]:
        return None
    return (
        f"Which functions call `{_display(d, lang)}`, defined in `{d.path}`? List every one, including test functions.",
        key,
    )


def two_hop_question(a: Analysis, d: Def, lang: str) -> tuple[str, tuple[str, ...]] | None:
    direct = a.callers(d)
    if direct is None or not grep_confirms(a.root, d.name, {(s.path, s.line) for s in direct}):
        return None
    key = _entries(direct)
    for s in direct:
        mid = a.def_of(s.path, s.caller)
        up = a.callers(mid) if mid else None
        if up is None or not grep_confirms(a.root, mid.name, {(u.path, u.line) for u in up}):
            return None
        key |= _entries(up)
    if not TWO_HOP_SIZE[0] <= len(key) <= TWO_HOP_SIZE[1] or len(key) == len(_entries(direct)):
        return None
    return (
        f"Which functions call `{_display(d, lang)}`, defined in `{d.path}`, either directly or through one "
        "intermediate function? List every one, including test functions.",
        tuple(sorted(key)),
    )


def importers_question(a: Analysis, m: Module, lang: str) -> tuple[str, tuple[str, ...]] | None:
    places = {(path, line) for path, lines in m.importers.items() for line in lines}
    if not IMPORTERS_SIZE[0] <= len(m.importers) <= IMPORTERS_SIZE[1] or not grep_confirms(a.root, m.grep, places):
        return None
    how = "or import names from it" if lang == "python" else "or import items from it, with a `use` declaration"
    return f"Which files import {m.label}, {how}? List every one, including test files.", tuple(sorted(m.importers))


def common_names(a: Analysis) -> set[str]:
    """Names defined more than once in the repo, whose callers grep over-reports."""
    count: dict[str, int] = defaultdict(int)
    for d in a.defs:
        count[d.name] += 1
    return {n for n, k in count.items() if k > 1}


def pick(a: Analysis, lang: str, make: Callable, common: int, unique: int, rng: random.Random) -> list[tuple]:
    """(def, question, key, is_common) for up to `common` common-name targets and `unique` others; a common one
    that can't be found is replaced by a unique one."""
    names = common_names(a)
    targets = sorted(
        (d for d in a.defs if not d.test and not d.name.startswith("__") and a.calls.get(d.name)),
        key=lambda d: (d.path, d.qualname, d.line),
    )
    rng.shuffle(targets)
    picked: list[tuple] = []
    for want_common, want in ((True, common), (False, None)):
        want = want if want is not None else unique + common - len(picked)
        for d in targets:
            if sum(1 for p in picked if p[3] == want_common) >= want:
                break
            if (d.name in names) == want_common and all(p[0] != d for p in picked):
                made = make(a, d, lang)
                if made:
                    picked.append((d, *made, want_common))
    return picked


def repo_tasks(pin: config.PinnedRepo, cache: Path, seed: int) -> list[Task]:
    root = workspace.worktree(pin.repo, pin.commit, "build", cache)
    if pin.lang == "python":
        import jedi

        a, tool = python_analysis(root), f"jedi {jedi.__version__}"
    else:
        a, tool = rust_analysis(root, scip_index(root, pin.repo, pin.commit, cache)), config.RUST_ANALYZER_VERSION
    source = f"{tool} on {pin.repo}@{pin.tag}"
    short = pin.repo.split("/")[1]
    rng = random.Random(f"{seed}:{pin.repo}")
    tasks = []

    def add(kind: str, label: str, question: str, key: tuple[str, ...], answer: str, stratum: str) -> None:
        tasks.append(
            Task(
                id=f"{short}-{kind}-{label}",
                kind="structural",
                lang=pin.lang,
                repo=pin.repo,
                commit=pin.commit,
                prompt=prompts.structural(question, answer, pin.lang),
                gold=key,
                source=source,
                answer=answer,
                stratum=stratum,
            )
        )

    for kind, make in (("callers", callers_question), ("two-hop", two_hop_question)):
        for d, question, key, is_common in pick(a, pin.lang, make, 1, 1, rng):
            add(kind, d.qualname, question, key, "functions", f"{kind}:{'common' if is_common else 'unique'}")
    modules = sorted(a.modules, key=lambda m: m.file)
    rng.shuffle(modules)
    for m in modules:
        made = importers_question(a, m, pin.lang)
        if made:
            add("importers", m.file.replace("/", "."), *made, "files", "importers")
            break
    return tasks


def build(seed: int = config.SEED, cache: Path | None = None) -> list[Task]:
    cache = cache or config.cache_dir()
    return [t for pin in config.STRUCTURAL_REPOS for t in repo_tasks(pin, cache, seed)]
````

- [ ] **Step 5: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_structural.py -q`

Expected: `7 passed`.

- [ ] **Step 6: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 7: Commit**

```bash
git add bench/eval/scip_pb2.py bench/eval/tasks/structural.py tests/test_eval_structural.py
git commit -F - <<'EOF'
feat: eval structural questions from jedi and rust-analyzer SCIP

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 10: The runner

**Files:**
- Create: `bench/eval/runner.py`
- Test: `tests/test_eval_runner.py`

**Interfaces:**
- Consumes: `stream.read`, `metrics`, `config_problems`, `infrastructure_error`, `turns_to_locate`; `score.parse_answer`, `score.score`; everything in `setups`; `workspace.worktree_path`, `restore`, `free_gb`; `suite.Task`; `config`.
- Produces, in `bench.eval.runner`:
  - `InfrastructureError`, and `Run(task, setup, rep)` with `.key`;
  - `schedule(tasks, setup_names, reps, seed) -> list[Run]`;
  - `finished(results_path) -> set`;
  - `probe(task, setup_name, cache, claude) -> list[str]`;
  - `execute(run, cache, out_dir, attempt, claude) -> dict`;
  - `Batch(runs, cache, out_dir, parallel, max_total_usd, execute_fn, claude, log)`, whose `.run()` returns why the batch stopped early, or None.
- `execute` returns one results line. It holds every `stream.metrics` key, plus:
  - `task`, `setup`, `rep`, `attempt`, `seed`, `kind`, `lang`, `repo`, `stratum`;
  - `config_ok`, `config_problems`, `killed`, `wall_s`;
  - `answer`, `score` (as `Score.as_dict()`), `turns_to_locate` and `worktree_changes`.

The tests drive `execute` with a fake `claude`: a script that replays a recorded stream, records the environment it got, and leaves a stray file for `restore` to remove.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_runner.py`:

````python
import gzip
import json
import os
import sys
import threading
import time

import pytest
from eval_helpers import origin

from bench.eval import config, runner, workspace
from bench.eval.suite import Task

RUNS = os.path.join(os.path.dirname(__file__), "eval_runs")


def task(i, commit="c" * 40):
    return Task(f"t{i}", "localization", "python", "o/r", commit, "p", ("src/a.py:needle_fn",), "s")


def test_schedule_is_complete_and_seeded():
    tasks = [task(1), task(2)]
    runs = runner.schedule(tasks, ["baseline", "duckgrep"], 2, seed=3)
    assert len(runs) == 8 and len({r.key for r in runs}) == 8
    assert [r.key for r in runs] == [r.key for r in runner.schedule(tasks, ["baseline", "duckgrep"], 2, seed=3)]
    assert [r.key for r in runs] != [r.key for r in runner.schedule(tasks, ["baseline", "duckgrep"], 2, seed=4)]


def fake_record(r, attempt, ok=True, cost=0.01):
    return {
        "task": r.task.id,
        "setup": r.setup,
        "rep": r.rep,
        "attempt": attempt,
        "config_ok": ok,
        "config_problems": [] if ok else ["tools differ"],
        "tool_calls": 1,
        "cost_usd": cost,
        "cli_cost_usd": cost,
        "score": {"success": True},
    }


def results(out):
    with open(out / "results.jsonl") as f:
        return [json.loads(line) for line in f]


def test_batch_records_every_run_once_and_resumes(tmp_path):
    runs = runner.schedule([task(1), task(2)], ["baseline"], 2, seed=1)
    batch = runner.Batch(
        runs, tmp_path, tmp_path / "out", parallel=2, execute_fn=lambda r, *a: fake_record(r, a[2]), log=lambda _: None
    )
    assert batch.run() is None
    assert sorted((x["task"], x["rep"]) for x in results(tmp_path / "out")) == [
        ("t1", 1),
        ("t1", 2),
        ("t2", 1),
        ("t2", 2),
    ]
    again = runner.Batch(
        runs, tmp_path, tmp_path / "out", execute_fn=lambda *a: pytest.fail("rerun"), log=lambda _: None
    )
    assert again.pending == [] and again.run() is None


def test_a_failed_configuration_check_is_retried_once(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    batch = runner.Batch(
        runs,
        tmp_path,
        tmp_path / "out",
        execute_fn=lambda r, c, o, attempt, cl: fake_record(r, attempt, ok=attempt == 2),
        log=lambda _: None,
    )
    batch.run()
    [rec] = results(tmp_path / "out")
    assert rec["attempt"] == 2 and rec["config_ok"]


def test_an_infrastructure_error_stops_the_batch_without_recording(tmp_path):
    runs = runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1)

    def execute(r, *a):
        if r.task.id == "t1":
            raise runner.InfrastructureError("authentication_failed")
        return fake_record(r, 1)

    stopped = runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None).run()
    assert "authentication_failed" in stopped
    assert (
        "t1" not in [x["task"] for x in results(tmp_path / "out")]
        if (tmp_path / "out/results.jsonl").exists()
        else True
    )


def test_the_spending_cap_stops_new_runs(tmp_path):
    runs = runner.schedule([task(1), task(2), task(3)], ["baseline"], 2, seed=1)
    stopped = runner.Batch(
        runs,
        tmp_path,
        tmp_path / "out",
        parallel=1,
        max_total_usd=0.025,
        execute_fn=lambda r, *a: fake_record(r, 1),
        log=lambda _: None,
    ).run()
    assert "cap" in stopped and len(results(tmp_path / "out")) == 3


def test_runs_sharing_a_worktree_never_overlap(tmp_path):
    runs = runner.schedule([task(1, "a" * 40), task(2, "b" * 40)], ["baseline"], 3, seed=1)
    active, lock, clashes = set(), threading.Lock(), []

    def execute(r, *a):
        wt = workspace.worktree_path(tmp_path, r.setup, r.task.repo, r.task.commit)
        with lock:
            clashes.append(wt in active)
            active.add(wt)
        time.sleep(0.05)
        with lock:
            active.discard(wt)
        return fake_record(r, 1)

    runner.Batch(runs, tmp_path, tmp_path / "out", parallel=3, execute_fn=execute, log=lambda _: None).run()
    assert len(clashes) == 6 and not any(clashes)


def fake_claude(tmp_path, stream_lines):
    """An executable standing in for `claude`: records its argv and environment, leaves a stray file in its
    working directory, and prints a recorded stream."""
    stream = tmp_path / "stream.jsonl"
    stream.write_text("\n".join(json.dumps(e) for e in stream_lines) + "\n")
    seen = tmp_path / "seen.json"
    exe = tmp_path / "claude"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv, 'env': dict(os.environ)}}, open({str(seen)!r}, 'w'))\n"
        "open('stray.txt', 'w').write('x')\n"
        f"sys.stdout.write(open({str(stream)!r}).read())\n"
    )
    exe.chmod(0o755)
    return str(exe), seen


def recorded_stream(answer):
    with open(os.path.join(RUNS, "plain.jsonl")) as f:
        events = [json.loads(line) for line in f]
    for e in events:
        if e.get("subtype") == "init":
            e["model"] = config.MODEL
        if e.get("type") == "result":
            e["result"] = answer
    return events


def test_execute_runs_scores_and_restores(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, {"src/a.py": "def needle_fn():\n    pass\n"})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    answer = 'Found it.\n\n```json\n{"locations": ["src/a.py:needle_fn"]}\n```'
    exe, seen = fake_claude(tmp_path, recorded_stream(answer))
    monkeypatch.setenv("GIT_DIR", "/should/not/leak")
    run = runner.Run(task(1, commit), "baseline", 1)
    rec = runner.execute(run, cache, tmp_path / "out", 1, exe)
    assert rec["config_ok"] and rec["score"]["success"] and rec["answer"] == ["src/a.py:needle_fn"]
    assert rec["turns_to_locate"] == 1 and rec["tool_calls"] == 2 and not rec["killed"]
    assert rec["worktree_changes"] == ["?? stray.txt"]
    wt = workspace.worktree_path(cache, "baseline", "o/r", commit)
    assert not (wt / "stray.txt").exists()
    raw = tmp_path / "out" / "t1" / "baseline-1.jsonl.gz"
    assert raw.exists() and not raw.with_suffix("").exists()
    with gzip.open(raw, "rt") as f:
        assert len(f.read().splitlines()) == len(recorded_stream(answer))
    got = json.loads(seen.read_text())
    added_by_python = {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}  # the fake is a Python script; macOS and PEP 538
    assert set(got["env"]) - added_by_python == {
        "HOME",
        "PATH",
        "USER",
        "TMPDIR",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
        "ENABLE_TOOL_SEARCH",
    }
    assert got["argv"][got["argv"].index("--model") + 1] == config.MODEL


def test_execute_turns_an_auth_failure_into_an_infrastructure_error(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": "x = 1\n"})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    with open(os.path.join(RUNS, "auth_failure.jsonl")) as f:
        exe, _ = fake_claude(tmp_path, [json.loads(line) for line in f])
    with pytest.raises(runner.InfrastructureError):
        runner.execute(runner.Run(task(1, commit), "baseline", 1), cache, tmp_path / "out", 1, exe)


def test_low_disk_stops_the_batch_before_the_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "free_gb", lambda path: 1.0)
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    stopped = runner.Batch(
        runs, tmp_path, tmp_path / "out", execute_fn=lambda *a: pytest.fail("ran"), log=lambda _: None
    ).run()
    assert "GB free" in stopped


def test_a_harness_fault_stops_the_batch(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)

    def broken(*a):
        raise RuntimeError("worktree is missing")

    stopped = runner.Batch(runs, tmp_path, tmp_path / "out", execute_fn=broken, log=lambda _: None).run()
    assert "worktree is missing" in stopped


def test_the_wall_clock_limit_kills_the_run_and_its_children(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, {"src/a.py": "x = 1\n"})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    pidfile = tmp_path / "child.pid"
    exe = tmp_path / "claude"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        'print(\'{"type": "system", "subtype": "init"}\', flush=True)\n'
        "time.sleep(60)\n"
    )
    exe.chmod(0o755)
    monkeypatch.setattr(config, "WALL_LIMIT_S", 1)
    rec = runner.execute(runner.Run(task(1, commit), "baseline", 1), cache, tmp_path / "out", 1, str(exe))
    assert rec["killed"] and rec["is_error"] and not rec["score"]["parsed"]
    child = int(pidfile.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
````

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_runner.py -q`

Expected: a collection error, because `bench.eval.runner` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 3: Write the runner**

Create `bench/eval/runner.py`:

````python
"""Run every (task, setup, repetition) of a suite: in a seeded random order, a few at a time, capped, resumable.

Results go to runs/<name>/results.jsonl, one line per finished run; a restart skips what is there. Each run's raw
stream is kept as runs/<name>/<task>/<setup>-<rep>.jsonl.gz.
"""

from __future__ import annotations

import gzip
import json
import os
import random
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import config, score, setups, stream, workspace
from .suite import Task


class InfrastructureError(RuntimeError):
    """The run failed outside the agent (login, rate limit, API outage). The batch stops; the run is redone later."""


@dataclass(frozen=True)
class Run:
    task: Task
    setup: str
    rep: int

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.task.id, self.setup, self.rep)


def schedule(tasks: list[Task], setup_names: list[str], reps: int, seed: int) -> list[Run]:
    """Every run in a seeded random order, so no setup systematically runs with a warmer prompt cache."""
    runs = [Run(t, s, r) for t in tasks for s in setup_names for r in range(1, reps + 1)]
    random.Random(seed).shuffle(runs)
    return runs


def finished(results: Path) -> set[tuple[str, str, int]]:
    if not results.exists():
        return set()
    with open(results) as f:
        return {(r["task"], r["setup"], r["rep"]) for r in map(json.loads, filter(str.strip, f))}


def _kill_group(proc: subprocess.Popen) -> None:
    """Stop the run's whole process group: claude, its MCP servers and their language servers."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.5)


def _mcp_file(setup: setups.Setup, task: Task, wt: Path, cache: Path, tmp: Path) -> Path | None:
    """Write the run's --mcp-config file (and its own SERENA_HOME) into `tmp`; None for the baseline."""
    home = setups.serena_home(tmp / "serena-home", cache) if setup.name == "serena" else None
    cfg = setups.mcp_config(setup, wt, task.repo, cache, home)
    if cfg is None:
        return None
    path = tmp / "mcp.json"
    path.write_text(json.dumps(cfg))
    return path


def probe(task: Task, setup_name: str, cache: Path, claude: str) -> list[str]:
    """Run a setup's exact command without USER. Login then fails before any model call, so the probe costs
    nothing, but the startup event still shows the tools, MCP servers, plugins and skills a run would get."""
    setup = setups.SETUPS[setup_name]
    wt = workspace.worktree_path(cache, setup_name, task.repo, task.commit)
    if not (wt / ".git").exists():
        return [f"{wt} is missing: run `prepare` first"]
    (cache / "tmp").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache / "tmp") as tmp:
        argv = setups.command(setup, task.prompt, _mcp_file(setup, task, wt, cache, Path(tmp)), claude=claude)
        out = subprocess.run(
            argv,
            cwd=wt,
            env=setups.environment(with_user=False),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
        )
    tr = stream.read(out.stdout.splitlines())
    problems = stream.config_problems(tr, setup.expected_tools, set(setup.servers))
    if tr.api_error != "authentication_failed":
        problems.append("the probe did not stop at login; check that it cost nothing")
    return problems


def execute(run: Run, cache: Path, out_dir: Path, attempt: int, claude: str) -> dict:
    """One run: start claude in the setup's worktree, capture the stream, score it, restore the worktree."""
    task, setup = run.task, setups.SETUPS[run.setup]
    wt = workspace.worktree_path(cache, run.setup, task.repo, task.commit)
    if not (wt / ".git").exists():
        raise RuntimeError(f"{wt} is missing: run `prepare` first")
    run_dir = out_dir / task.id
    run_dir.mkdir(parents=True, exist_ok=True)
    raw = run_dir / f"{run.setup}-{run.rep}.jsonl"
    (cache / "tmp").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache / "tmp") as tmp:
        argv = setups.command(setup, task.prompt, _mcp_file(setup, task, wt, cache, Path(tmp)), claude=claude)
        started = time.monotonic()
        killed = False
        with open(raw, "w") as out, open(run_dir / f"{run.setup}-{run.rep}.stderr", "w") as err:
            proc = subprocess.Popen(
                argv,
                cwd=wt,
                env=setups.environment(),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=config.WALL_LIMIT_S)
            except subprocess.TimeoutExpired:
                killed = True
            finally:
                _kill_group(proc)
        wall = time.monotonic() - started
    with open(raw) as f:
        tr = stream.read(f)
    with open(raw, "rb") as src, gzip.open(raw.with_name(raw.name + ".gz"), "wb") as dst:
        shutil.copyfileobj(src, dst)
    raw.unlink()
    if stream.infrastructure_error(tr) and not killed:
        raise InfrastructureError(f"{task.id}/{run.setup}-{run.rep}: {tr.api_error or tr.result.get('result')}")
    m = stream.metrics(tr)
    answer = score.parse_answer(m["final_text"])
    roots = (str(wt), os.path.realpath(wt))
    problems = stream.config_problems(tr, setup.expected_tools, set(setup.servers))
    return {
        "task": task.id,
        "setup": run.setup,
        "rep": run.rep,
        "attempt": attempt,
        "seed": config.SEED,
        "kind": task.kind,
        "lang": task.lang,
        "repo": task.repo,
        "stratum": task.stratum,
        "config_ok": not problems,
        "config_problems": problems,
        "killed": killed,
        "wall_s": round(wall, 1),
        **m,
        "answer": answer,
        "score": score.score(answer, task.gold, task.answer, roots).as_dict(),
        "turns_to_locate": stream.turns_to_locate(tr, task.gold),
        "worktree_changes": workspace.restore(wt),
    }


class Batch:
    """Runs a schedule with `parallel` workers. Runs that share a worktree never overlap."""

    def __init__(
        self,
        runs: list[Run],
        cache: Path,
        out_dir: Path,
        parallel: int = config.PARALLEL,
        max_total_usd: float = 200.0,
        execute_fn: Callable[..., dict] = execute,
        claude: str = "claude",
        log: Callable[[str], None] = print,
    ):
        self.results = out_dir / "results.jsonl"
        done = finished(self.results)
        self.pending = [r for r in runs if r.key not in done]
        self.cache, self.out_dir, self.parallel = cache, out_dir, parallel
        self.max_total_usd, self.execute, self.claude, self.log = max_total_usd, execute_fn, claude, log
        self.busy: set[Path] = set()
        self.cond = threading.Condition()
        self.spent = 0.0
        self.stopped: str | None = None
        self.completed = 0

    def _take(self) -> Run | None:
        with self.cond:
            while True:
                if not self.stopped and workspace.free_gb(self.cache) < config.MIN_FREE_GB:
                    self.stopped = f"less than {config.MIN_FREE_GB} GB free under {self.cache}"
                if self.stopped or not self.pending:
                    return None
                for i, r in enumerate(self.pending):
                    wt = workspace.worktree_path(self.cache, r.setup, r.task.repo, r.task.commit)
                    if wt not in self.busy:
                        self.busy.add(wt)
                        return self.pending.pop(i)
                self.cond.wait()

    def _release(self, r: Run) -> None:
        with self.cond:
            self.busy.discard(workspace.worktree_path(self.cache, r.setup, r.task.repo, r.task.commit))
            self.cond.notify_all()

    def _record(self, rec: dict) -> None:
        with self.cond:
            with open(self.results, "a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self.spent += rec.get("cli_cost_usd") or 0.0
            self.completed += 1
            if self.spent >= self.max_total_usd and not self.stopped:
                self.stopped = f"spent ${self.spent:.2f}, the batch cap is ${self.max_total_usd:.2f}"

    def _worker(self) -> None:
        while (r := self._take()) is not None:
            try:
                rec = self.execute(r, self.cache, self.out_dir, 1, self.claude)
                if not rec["config_ok"]:  # discard, and retry once
                    self.log(f"config check failed, retrying: {r.key} {rec['config_problems']}")
                    rec = self.execute(r, self.cache, self.out_dir, 2, self.claude)
                self._record(rec)
                self.log(
                    f"[{self.completed}] {r.task.id} {r.setup}-{r.rep}: {rec['tool_calls']} calls, "
                    f"${rec['cost_usd']:.3f}, success={rec['score']['success']}"
                )
            except InfrastructureError as e:
                with self.cond:
                    self.stopped = self.stopped or f"infrastructure error: {e}"
                    self.cond.notify_all()
            except Exception as e:  # a harness fault: stop rather than record a wrong result
                with self.cond:
                    self.stopped = self.stopped or f"{r.task.id} {r.setup}-{r.rep} failed: {e!r}"
                    self.cond.notify_all()
            finally:
                self._release(r)

    def run(self) -> str | None:
        """Run everything pending; return why the batch stopped early, or None if it finished."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        workers = [threading.Thread(target=self._worker, daemon=True) for _ in range(self.parallel)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        return self.stopped
````

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_runner.py -q`

Expected: `11 passed`.

- [ ] **Step 5: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 6: Commit**

```bash
git add bench/eval/runner.py tests/test_eval_runner.py
git commit -F - <<'EOF'
feat: eval runner: shuffled, capped, resumable runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 11: The report

**Files:**
- Create: `bench/eval/report.py`
- Test: `tests/test_eval_report.py`

**Interfaces:**
- Consumes: `config.SEED`, and the results-line keys listed in Task 10.
- Produces, in `bench.eval.report`:
  - `load(path) -> list[dict]`;
  - `Metric` and `METRICS`;
  - `task_means(records, metric)`;
  - `compare(table, setup, metric, pairs, seed) -> Comparison`;
  - `holm(ps)`;
  - `comparisons(records, seed)`;
  - `build(records, prepared, name, seed=SEED) -> str`;
  - `write_section(path, name, text)`.

The report covers the four tables of the spec, plus the post-cutoff Rust subset, adoption, repos, variance (run-to-run against task-to-task) and setup costs.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_report.py`:

````python
import pytest

from bench.eval import report

M = {m.name: m for m in report.METRICS}


def rec(
    task,
    setup,
    rep=1,
    calls=10,
    tokens=1000,
    cost=0.1,
    success=True,
    f1=1.0,
    located=2,
    ok=True,
    kind="localization",
    lang="python",
    stratum="lite",
    adopted=False,
    repo="o/r",
):
    return {
        "task": task,
        "setup": setup,
        "rep": rep,
        "tool_calls": calls,
        "rounds": calls + 1,
        "tokens_total": tokens,
        "cost_usd": cost,
        "score": {"success": success, "f1": f1},
        "turns_to_locate": located,
        "config_ok": ok,
        "kind": kind,
        "lang": lang,
        "stratum": stratum,
        "adopted": adopted,
        "mcp_share": 0.5 if adopted else 0.0,
        "repo": repo,
        "killed": False,
        "is_error": False,
        "worktree_changes": [],
        "cli_cost_usd": cost,
        "cli_version": "2.1.285",
        "model": "claude-sonnet-5-5",
    }


def test_holm():
    assert report.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert report.holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])


def test_task_means_average_repetitions_and_drop_incomplete_tasks():
    rows = [
        rec("a", "baseline", 1, calls=10),
        rec("a", "baseline", 2, calls=20),
        rec("b", "baseline", located=None),
        rec("c", "baseline", ok=False),
    ]
    assert report.task_means(rows, M["tool calls"]) == {("a", "baseline"): 15.0, ("b", "baseline"): 10.0}
    assert report.task_means(rows, M["turns to locate"]) == {("a", "baseline"): 2.0}


def test_compare_on_a_log_scale():
    c = report.compare("t", "duckgrep", M["tokens"], [(500.0, 1000.0), (50.0, 100.0), (5.0, 10.0)], seed=1)
    assert c.effect == pytest.approx(0.5) and c.low == pytest.approx(0.5) and c.high == pytest.approx(0.5)
    assert c.win == 1.0 and c.n == 3 and c.base == pytest.approx(100.0) and c.other == pytest.approx(50.0)


def test_compare_differences_and_ties():
    c = report.compare("t", "serena", M["success"], [(1.0, 0.0), (1.0, 1.0), (0.0, 0.0)], seed=1)
    assert c.effect == pytest.approx(1 / 3) and c.win == pytest.approx((1 + 0.5 + 0.5) / 3)
    same = report.compare("t", "serena", M["F1"], [(1.0, 1.0), (0.5, 0.5)], seed=1)
    assert same.p == 1.0 and same.effect == 0.0


def records():
    rows = []
    for i in range(6):
        for rep in (1, 2):
            rows.append(rec(f"t{i}", "baseline", rep, calls=12 + i, tokens=2000 + 100 * i, cost=0.2))
            rows.append(rec(f"t{i}", "duckgrep", rep, calls=4 + i, tokens=900 + 100 * i, cost=0.1, adopted=True))
            rows.append(rec(f"t{i}", "serena", rep, calls=15 + i, tokens=2600 + 100 * i, cost=0.3, adopted=rep == 1))
    return rows


def test_report_has_every_section():
    text = report.build(records(), [{"setup": "duckgrep", "index_seconds": 2.0, "index_mb": 5.0}], "pilot")
    assert text.startswith("## A/B evaluation: pilot")
    for heading in ("### Python localization", "### Adoption", "### By repo", "### Variance", "### Setup costs"):
        assert heading in text
    assert "| tool calls | 6 |" in text and "duckgrep" in text and "×" in text
    assert "### Rust localization" not in text  # no Rust runs


def test_comparisons_are_holm_corrected_together():
    found = report.comparisons(records())
    assert found and all(c.p_holm >= c.p for c in found)


def test_write_section_replaces_its_own_block(tmp_path):
    path = tmp_path / "RESULTS.md"
    path.write_text("# Benchmark results\n\nearlier text\n")
    report.write_section(path, "pilot", "first\n")
    report.write_section(path, "pilot", "second\n")
    text = path.read_text()
    assert text.count("<!-- eval:pilot -->") == 1 and "second" in text and "first" not in text
    assert text.startswith("# Benchmark results\n\nearlier text\n")
````

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_report.py -q`

Expected: a collection error, because `bench.eval.report` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 3: Write the report**

Create `bench/eval/report.py`:

````python
"""Paired comparisons of each setup against the baseline, task by task, as a markdown report.

The unit is the task: a setup's repetitions of a task are averaged first. Counts and token-like metrics are
compared on a log scale (the effect is a ratio of geometric means); success, F1 and turns-to-locate as
differences. Intervals are 95% bootstrap intervals over tasks; p-values are Wilcoxon signed-rank tests,
Holm-corrected across every comparison in the report.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats

from . import config

BOOTSTRAP = 10_000
CONTRASTS = ("duckgrep", "serena")
TABLES = (
    ("localization", "python", "Python localization"),
    ("localization", "rust", "Rust localization"),
    ("structural", "python", "Python structural questions"),
    ("structural", "rust", "Rust structural questions"),
)


@dataclass(frozen=True)
class Metric:
    name: str
    get: Callable[[dict], float | None]
    scale: str  # log, log1p (ratios of geometric means) or diff
    lower_is_better: bool


METRICS = (
    Metric("tool calls", lambda r: r["tool_calls"], "log1p", True),
    Metric("round trips", lambda r: r["rounds"], "log1p", True),
    Metric("tokens", lambda r: r["tokens_total"], "log", True),
    Metric("cost ($)", lambda r: r["cost_usd"], "log", True),
    Metric("turns to locate", lambda r: r["turns_to_locate"], "diff", True),
    Metric("success", lambda r: float(r["score"]["success"]), "diff", False),
    Metric("F1", lambda r: r["score"]["f1"], "diff", False),
)


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def task_means(records: list[dict], metric: Metric) -> dict[tuple[str, str], float]:
    """Mean over repetitions per (task, setup); a task drops out if any repetition has no value (e.g. an
    answer never located) or the value can't be logged."""
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for r in records:
        if r["config_ok"]:
            groups[(r["task"], r["setup"])].append(metric.get(r))
    out = {}
    for key, values in groups.items():
        if any(v is None for v in values):
            continue
        mean = float(np.mean(values))
        if metric.scale == "log" and mean <= 0:
            continue
        out[key] = mean
    return out


@dataclass
class Comparison:
    table: str
    setup: str
    metric: Metric
    n: int
    base: float  # the baseline's typical value (geometric mean on log scales)
    other: float
    effect: float  # ratio on log scales, difference otherwise
    low: float
    high: float
    p: float
    win: float  # share of tasks where the setup did better; ties count half
    p_holm: float = float("nan")


def _forward(scale: str, v: np.ndarray) -> np.ndarray:
    return np.log(v) if scale == "log" else np.log1p(v) if scale == "log1p" else v


def _typical(scale: str, v: np.ndarray) -> float:
    if scale == "log":
        return float(np.exp(np.log(v).mean()))
    if scale == "log1p":
        return float(np.expm1(np.log1p(v).mean()))
    return float(v.mean())


def compare(table: str, setup: str, metric: Metric, pairs: list[tuple[float, float]], seed: int) -> Comparison:
    """`pairs` are (setup, baseline) task means."""
    x = np.array([a for a, _ in pairs], dtype=float)
    y = np.array([b for _, b in pairs], dtype=float)
    d = _forward(metric.scale, x) - _forward(metric.scale, y)
    rng = np.random.default_rng(seed)
    boots = d[rng.integers(0, len(d), size=(BOOTSTRAP, len(d)))].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    mean = float(d.mean())
    if metric.scale == "diff":
        effect, low, high = mean, float(lo), float(hi)
    else:
        effect, low, high = math.exp(mean), math.exp(lo), math.exp(hi)
    p = 1.0 if not np.any(d != 0) else float(stats.wilcoxon(d).pvalue)
    better = (x < y) if metric.lower_is_better else (x > y)
    win = float((better.sum() + 0.5 * (x == y).sum()) / len(d))
    return Comparison(
        table, setup, metric, len(d), _typical(metric.scale, y), _typical(metric.scale, x), effect, low, high, p, win
    )


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    out = [1.0] * len(ps)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        out[i] = running
    return out


def comparisons(records: list[dict], seed: int = config.SEED) -> list[Comparison]:
    found = []
    for kind, lang, title in TABLES + (("localization", "rust-live", "Rust localization, post-cutoff issues"),):
        if lang == "rust-live":
            rows = [r for r in records if r["kind"] == kind and r["lang"] == "rust" and r["stratum"] == "live"]
        else:
            rows = [r for r in records if r["kind"] == kind and r["lang"] == lang]
        for metric in METRICS:
            means = task_means(rows, metric)
            for setup in CONTRASTS:
                pairs = [
                    (means[(t, setup)], means[(t, "baseline")])
                    for (t, s) in sorted(means)
                    if s == "baseline" and (t, setup) in means
                ]
                if len(pairs) >= 2:
                    found.append(compare(title, setup, metric, pairs, seed))
    for c, p in zip(found, holm([c.p for c in found]), strict=True):
        c.p_holm = p
    return found


def _num(v: float) -> str:
    return f"{v:,.4f}" if abs(v) < 1 else f"{v:,.1f}" if abs(v) < 100 else f"{v:,.0f}"


def _effect(c: Comparison) -> str:
    if c.metric.scale == "diff":
        return f"{c.effect:+.2f} [{c.low:+.2f}, {c.high:+.2f}]"
    return f"×{c.effect:.2f} [{c.low:.2f}, {c.high:.2f}]"


def metric_table(found: list[Comparison], title: str) -> list[str]:
    rows = [c for c in found if c.table == title]
    if not rows:
        return []
    out = [f"### {title}", ""]
    out.append("| metric | tasks | baseline | setup | vs baseline [95% CI] | p (Holm) | win rate |")
    out.append("|---|---:|---:|---|---|---:|---:|")
    for c in rows:
        out.append(
            f"| {c.metric.name} | {c.n} | {_num(c.base)} | {c.setup} {_num(c.other)} | {_effect(c)} | "
            f"{c.p_holm:.3f} | {c.win:.0%} |"
        )
    return out + [""]


def adoption_table(records: list[dict]) -> list[str]:
    out = [
        "### Adoption",
        "",
        "| setup | kind | runs | used its tool | its share of calls | calls (used / not) |",
        "|---|---|---:|---:|---:|---|",
    ]
    for setup in CONTRASTS:
        for kind in ("localization", "structural"):
            rows = [r for r in records if r["setup"] == setup and r["kind"] == kind and r["config_ok"]]
            if not rows:
                continue
            used = [r for r in rows if r["adopted"]]
            not_used = [r for r in rows if not r["adopted"]]

            def avg(rs):
                return f"{np.mean([r['tool_calls'] for r in rs]):.1f}" if rs else "–"

            out.append(
                f"| {setup} | {kind} | {len(rows)} | {len(used) / len(rows):.0%} | "
                f"{np.mean([r['mcp_share'] for r in rows]):.0%} | {avg(used)} / {avg(not_used)} |"
            )
    return out + [""]


def repo_table(records: list[dict]) -> list[str]:
    out = [
        "### By repo",
        "",
        "| repo | setup | runs | tool calls | cost ($) | success |",
        "|---|---|---:|---:|---:|---:|",
    ]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        if r["config_ok"]:
            groups[(r["repo"], r["setup"])].append(r)
    for (repo, setup), rs in sorted(groups.items()):
        out.append(
            f"| {repo} | {setup} | {len(rs)} | {np.mean([r['tool_calls'] for r in rs]):.1f} | "
            f"{np.mean([r['cost_usd'] for r in rs]):.3f} | {np.mean([r['score']['success'] for r in rs]):.0%} |"
        )
    return out + [""]


def variance_table(records: list[dict]) -> list[str]:
    """Run-to-run versus task-to-task spread, which sets the repetitions and task counts of the full run."""
    out = [
        "### Variance",
        "",
        "| setup | metric | within-task SD | between-task SD | within share of variance |",
        "|---|---|---:|---:|---:|",
    ]
    for setup in ("baseline",) + CONTRASTS:
        for label, get in (
            ("log tokens", lambda r: math.log(max(r["tokens_total"], 1))),
            ("log(1 + tool calls)", lambda r: math.log1p(r["tool_calls"])),
        ):
            by_task: dict[str, list[float]] = defaultdict(list)
            for r in records:
                if r["setup"] == setup and r["config_ok"]:
                    by_task[r["task"]].append(get(r))
            reps = [v for v in by_task.values() if len(v) > 1]
            if len(reps) < 2:
                continue
            within = float(np.mean([np.var(v, ddof=1) for v in reps]))
            between = float(np.var([np.mean(v) for v in reps], ddof=1))
            share = within / (within + between) if within + between else 0.0
            out.append(f"| {setup} | {label} | {math.sqrt(within):.2f} | {math.sqrt(between):.2f} | {share:.0%} |")
    return out + [""]


def setup_costs(prepared: list[dict]) -> list[str]:
    if not prepared:
        return []
    out = [
        "### Setup costs (not included above)",
        "",
        "| setup | worktrees | total seconds | total MB |",
        "|---|---:|---:|---:|",
    ]
    for setup in ("duckgrep", "serena"):
        rows = [p for p in prepared if p["setup"] == setup]
        if rows:
            secs = sum(p.get("index_seconds", 0) + p.get("serena_seconds", 0) for p in rows)
            mb = sum(p.get("index_mb", 0) for p in rows)
            out.append(f"| {setup} | {len(rows)} | {secs:,.0f} | {mb:,.0f} |")
    return out + [""]


def summary(records: list[dict]) -> list[str]:
    n = len(records)
    bad = sum(not r["config_ok"] for r in records)
    killed = sum(r["killed"] for r in records)
    errors = sum(r["is_error"] for r in records)
    dirty = sum(bool(r["worktree_changes"]) for r in records)
    cost = sum(r["cost_usd"] for r in records)
    cli = sum(r.get("cli_cost_usd") or 0 for r in records)
    versions = sorted({str(r["cli_version"]) for r in records})
    models = sorted({str(r["model"]) for r in records})
    return [
        f"{n} runs: {bad} failed the configuration check twice (excluded), {killed} hit the wall-clock limit, "
        f"{errors} ended in an error (turn or budget cap), {dirty} changed their worktree (restored after). "
        f"Cost ${cost:,.2f} at list rates (Claude Code billed ${cli:,.2f}). Claude Code {', '.join(versions)}; "
        f"model {', '.join(models)}.",
        "",
    ]


def build(records: list[dict], prepared: list[dict], name: str, seed: int = config.SEED) -> str:
    found = comparisons(records, seed)
    lines = [f"## A/B evaluation: {name}", ""] + summary(records)
    for title in [t for *_, t in TABLES] + ["Rust localization, post-cutoff issues"]:
        lines += metric_table(found, title)
    lines += adoption_table(records) + repo_table(records) + variance_table(records) + setup_costs(prepared)
    return "\n".join(lines).rstrip() + "\n"


def write_section(path: Path, name: str, text: str) -> None:
    """Put `text` between this report's markers in RESULTS.md, replacing an earlier version."""
    start, end = f"<!-- eval:{name} -->", f"<!-- /eval:{name} -->"
    block = f"{start}\n{text}{end}\n"
    current = path.read_text() if path.exists() else ""
    if start in current and end in current:
        before, rest = current.split(start, 1)
        after = rest.split(end, 1)[1].lstrip("\n")
        path.write_text(before + block + after)
    else:
        path.write_text(current.rstrip("\n") + "\n\n" + block if current else block)
````

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_report.py -q`

Expected: `7 passed`.

- [ ] **Step 5: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 6: Commit**

```bash
git add bench/eval/report.py tests/test_eval_report.py
git commit -F - <<'EOF'
feat: eval paired-statistics report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 12: Command line and docs

**Files:**
- Create: `bench/eval/__main__.py`
- Modify: `CLAUDE.md` (Commands; How it fits together; Gotchas)
- Test: `tests/test_eval_cli.py`

**Interfaces:**
- Consumes every module above.
- Produces `python -m bench.eval [--suite NAME] build|prepare|check|run|report`, plus `setup_list(value)`, `parser()`, `build(args)`, `check(tasks, setup_names, cache, claude) -> bool`, `claude_path()` and `main(argv=None) -> int`.

As the spec asks, `run` probes every setup before each batch, and stops at no cost if any probe finds a problem.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_cli.py`:

````python
import argparse

import pytest

from bench.eval import __main__ as cli
from bench.eval import config, suite
from bench.eval.suite import Task


def test_setup_list():
    assert cli.setup_list("baseline,serena") == ["baseline", "serena"]
    with pytest.raises(argparse.ArgumentTypeError):
        cli.setup_list("baseline,grep")


def test_report_without_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    assert cli.main(["--suite", "nothing", "report"]) == 1
    assert "no results" in capsys.readouterr().out


def test_run_rejects_unknown_task_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(cli, "claude_path", lambda: "claude")
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    assert cli.main(["run", "--tasks", "t1,nope"]) == 2


def test_run_stops_before_spending_when_a_setup_is_misconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(cli, "claude_path", lambda: "true")
    monkeypatch.setattr(
        cli.runner,
        "probe",
        lambda task, setup, cache, claude: ["MCP server serena is failed"] if setup == "serena" else [],
    )
    monkeypatch.setattr(cli.runner, "Batch", lambda *a, **k: pytest.fail("a batch started"))
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    assert cli.main(["run"]) == 1
````

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_cli.py -q`

Expected: a collection error, because `bench.eval.__main__` does not exist yet (a `ModuleNotFoundError` or an `ImportError` naming it).

- [ ] **Step 3: Write the command line**

Create `bench/eval/__main__.py`:

````python
"""The A/B evaluation of duckgrep against plain Claude Code and Serena.

    uv run python -m bench.eval [--suite pilot] build | prepare | check | run | report

`build` needs the network; `check` is free; `run` spends money (see --max-total-usd).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

from . import config, report, runner, suite, workspace
from .setups import SETUPS


def setup_list(value: str) -> list[str]:
    names = [s for s in value.split(",") if s]
    unknown = sorted(set(names) - set(SETUPS))
    if unknown or not names:
        raise argparse.ArgumentTypeError(f"setups must be among {sorted(SETUPS)}, got {value!r}")
    return names


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m bench.eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--suite", default="pilot", help="suite name: bench/eval/suites/<suite>-*.jsonl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="draw the suite's tasks and derive their answer keys (network)")
    b.add_argument("--kind", choices=["localization", "structural", "all"], default="all")
    b.add_argument("--seed", type=int, default=config.SEED)
    p = sub.add_parser("prepare", help="clone and check out every task's repo; build indexes and warm Serena up")
    p.add_argument("--setups", type=setup_list, default=list(SETUPS))
    c = sub.add_parser("check", help="verify each setup's configuration, free")
    c.add_argument("--setups", type=setup_list, default=list(SETUPS))
    r = sub.add_parser("run", help="run the suite; this costs money")
    r.add_argument("--setups", type=setup_list, default=list(SETUPS))
    r.add_argument("--reps", type=int, default=config.REPETITIONS)
    r.add_argument("--parallel", type=int, default=config.PARALLEL)
    r.add_argument("--tasks", help="comma-separated task ids (default: every task)")
    r.add_argument("--name", help="results directory under bench/eval/runs (default: the suite name)")
    r.add_argument("--max-total-usd", type=float, default=200.0, help="stop starting runs past this spend")
    rp = sub.add_parser("report", help="write the report")
    rp.add_argument("--name", help="results directory (default: the suite name)")
    rp.add_argument("--write", action="store_true", help="also put it into bench/RESULTS.md")
    return ap


def build(a) -> int:
    cache = config.cache_dir()
    if a.kind in ("localization", "all"):
        from .tasks import localization

        tasks = localization.build(seed=a.seed, cache=cache)
        suite.save(config.SUITES_DIR / f"{a.suite}-localization.jsonl", tasks)
        print(f"{len(tasks)} localization tasks")
    if a.kind in ("structural", "all"):
        from .tasks import structural

        tasks = structural.build(seed=a.seed, cache=cache)
        suite.save(config.SUITES_DIR / f"{a.suite}-structural.jsonl", tasks)
        print(f"{len(tasks)} structural tasks")
    return 0


def check(tasks: list, setup_names: list[str], cache, claude: str) -> bool:
    """Probe each setup on one task per language (free); print a line per probe; True if all are clean."""
    ok = True
    for lang in sorted({t.lang for t in tasks}):
        sample = next(t for t in tasks if t.lang == lang)
        for setup in setup_names:
            problems = runner.probe(sample, setup, cache, claude)
            ok = ok and not problems
            print(f"{setup:9} {lang:7} {'ok' if not problems else '; '.join(problems)}")
    return ok


def claude_path() -> str:
    found = shutil.which("claude")
    if not found:
        sys.exit("claude is not on PATH")
    return found


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    if a.cmd == "build":
        return build(a)
    cache = config.cache_dir()
    if a.cmd == "report":
        name = a.name or a.suite
        records = report.load(config.RUNS_DIR / name / "results.jsonl")
        if not records:
            print(f"no results in {config.RUNS_DIR / name}")
            return 1
        text = report.build(records, report.load(config.RUNS_DIR / a.suite / "prepare.jsonl"), name)
        (config.RUNS_DIR / name / "report.md").write_text(text)
        if a.write:
            report.write_section(config.RESULTS_MD, name, text)
        print(text)
        return 0
    tasks = suite.load_suite(a.suite, config.SUITES_DIR)
    if a.cmd == "prepare":
        built = workspace.prepare(tasks, a.setups, cache)
        out = config.RUNS_DIR / a.suite
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "prepare.jsonl", "a") as f:
            f.writelines(json.dumps(row) + "\n" for row in built)
        return 0
    wanted = a.tasks.split(",") if getattr(a, "tasks", None) else None
    chosen = [t for t in tasks if wanted is None or t.id in wanted]
    if wanted and len(chosen) != len(set(wanted)):
        print(f"unknown task ids: {sorted(set(wanted) - {t.id for t in chosen})}", file=sys.stderr)
        return 2
    claude = claude_path()
    version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.strip()
    print(f"claude {version}")
    if not check(chosen, a.setups, cache, claude):  # before every batch too: a misconfigured setup costs nothing yet
        return 1
    if a.cmd == "check":
        return 0
    workspace.require_space(cache)
    out = config.RUNS_DIR / (a.name or a.suite)
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "suite": a.suite,
        "tasks": len(chosen),
        "setups": a.setups,
        "reps": a.reps,
        "seed": config.SEED,
        "claude": version,
        "model": config.MODEL,
        "effort": config.EFFORT,
        "max_turns": config.MAX_TURNS,
        "max_budget_usd": config.MAX_BUDGET_USD,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    runs = runner.schedule(chosen, a.setups, a.reps, config.SEED)
    print(f"{len(runs)} runs, results in {out}")
    stopped = runner.Batch(runs, cache, out, a.parallel, a.max_total_usd, claude=claude).run()
    if stopped:
        print(f"stopped early: {stopped}; rerun the same command to resume")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
````

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_eval_cli.py -q`

Expected: `4 passed`.

- [ ] **Step 5: Run the whole suite and the linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

Expected: every test passes (the existing duckgrep tests too), and ruff reports nothing.

- [ ] **Step 6: Document the harness in CLAUDE.md**

Add to the Commands block:

```bash
export DUCKGREP_EVAL_CACHE=/Volumes/research/code-tasks   # clones and worktrees; never inside ~/git
uv run python -m bench.eval build        # draw the pilot suites (network): bench/eval/suites/pilot-*.jsonl
uv run python -m bench.eval prepare      # clones, one worktree per setup, duckgrep index, Serena warm-up
uv run python -m bench.eval check        # free: each setup's tools and MCP servers, login refused
uv run python -m bench.eval run --tasks A,B --reps 1 --name smoke   # costs money; rerun to resume
uv run python -m bench.eval report --write                          # paired statistics into bench/RESULTS.md
```

Add to "How it fits together":

- `bench/eval/` is the A/B harness (spec in `docs/specs/2026-09-29-ab-eval-harness-design.md`):
  - It runs the real `claude` CLI in a scrubbed environment under three setups, which differ only in one MCP server.
  - It parses stream-json and scores against answer keys, which come from fix patches or from jedi and rust-analyzer SCIP.
  - Suites are committed JSONL files; raw runs go under the gitignored `bench/eval/runs/`.

Add to "Gotchas":

- **The agent sees its working directory.** So worktree and cache paths never name a setup or duckgrep.
- **`check` runs without `USER`.** Login then fails before any model call, which makes it free.
- **Tests never call a model.** A fake `claude` script stands in for the CLI.

- [ ] **Step 7: Commit**

```bash
git add bench/eval/__main__.py tests/test_eval_cli.py CLAUDE.md
git commit -F - <<'EOF'
feat: eval command line; docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 13: Build and inspect the pilot suites

**Files:**
- Create: `bench/eval/suites/pilot-localization.jsonl`, `bench/eval/suites/pilot-structural.jsonl`

This step needs the network. It downloads about 60 MB of datasets and 14 MB of rust-analyzer, and full-clones requests, pytest, ripgrep and fd; the localization repos are cloned later, by `prepare`.

- [ ] **Step 1: Build**

```bash
export DUCKGREP_EVAL_CACHE=/Volumes/research/code-tasks
uv run python -m bench.eval build
```

Expected: `20 localization tasks` and `20 structural tasks`.

- [ ] **Step 2: Check the draw against the prototype's**

The build is deterministic, so the tasks must be exactly these, in this order:

| task | language | stratum | key entries |
|---|---|---|---:|
| `sympy__sympy-12236` | python | lite | 1 |
| `astropy__astropy-14365` | python | lite | 2 |
| `psf__requests-2674` | python | lite | 1 |
| `sphinx-doc__sphinx-8282` | python | lite | 3 |
| `scikit-learn__scikit-learn-14092` | python | lite | 1 |
| `django__django-13925` | python | lite | 1 |
| `mwaskom__seaborn-2848` | python | lite | 1 |
| `matplotlib__matplotlib-23913` | python | lite | 1 |
| `pylint-dev__pylint-5859` | python | lite | 1 |
| `pallets__flask-5063` | python | lite | 1 |
| `sharkdp__bat-562` | rust | multilingual | 1 |
| `tokio-rs__tokio-6603` | rust | multilingual | 1 |
| `burntsushi__ripgrep-2576` | rust | multilingual | 1 |
| `nushell__nushell-13605` | rust | multilingual | 2 |
| `uutils__coreutils-6731` | rust | multilingual | 1 |
| `prefix-dev__pixi-6335` | rust | live | 5 |
| `lakehq__sail-2292` | rust | live | 1 |
| `gleam-lang__gleam-5971` | rust | live | 1 |
| `rust-lang__rust-analyzer-22751` | rust | live | 2 |
| `web-infra-dev__rspack-14803` | rust | live | 2 |
| `requests-callers-info` | python | callers:common | 4 |
| `requests-callers-RequestsCookieJar.get_policy` | python | callers:unique | 2 |
| `requests-two-hop-PreparedRequest.prepare_url` | python | two-hop:common | 7 |
| `requests-two-hop-PreparedRequest.prepare_body` | python | two-hop:unique | 7 |
| `requests-importers-src.requests.utils.py` | python | importers | 6 |
| `pytest-callers-Node.repr_failure` | python | callers:common | 3 |
| `pytest-callers-_recursive_sequence_map` | python | callers:unique | 11 |
| `pytest-two-hop-TerminalReporter.rewrite` | python | two-hop:common | 4 |
| `pytest-two-hop-FormattedExcinfo._getindent` | python | two-hop:unique | 5 |
| `pytest-importers-src._pytest._io.saferepr.py` | python | importers | 8 |
| `ripgrep-callers-StandardBuilder.max_columns_preview` | rust | callers:common | 11 |
| `ripgrep-callers-Match.is_ignore` | rust | callers:unique | 3 |
| `ripgrep-two-hop-WalkBuilder.add_custom_ignore_filename` | rust | two-hop:common | 7 |
| `ripgrep-two-hop-Core.find_by_line_fast` | rust | two-hop:unique | 3 |
| `ripgrep-importers-crates.core.flags.doc.mod.rs` | rust | importers | 3 |
| `fd-callers-DirEntry.file_type` | rust | callers:common | 4 |
| `fd-callers-dirname` | rust | callers:unique | 2 |
| `fd-two-hop-DirEntry.file_type` | rust | two-hop:common | 8 |
| `fd-two-hop-replace_path_separator` | rust | two-hop:unique | 4 |
| `fd-importers-src.filter.mod.rs` | rust | importers | 3 |

A difference means an input moved (a dataset revision, a tool version or a pinned commit) and must be explained before continuing.

- [ ] **Step 3: Spot-check the keys**

For three localization tasks, one per source, read the fix patch next to the key. For example: `psf__requests-2674`, `tokio-rs__tokio-6603` and `gleam-lang__gleam-5971`.

For three structural questions, including one common-name question, confirm the key with `git grep -n -w <name>` in the build worktree under `$DUCKGREP_EVAL_CACHE/wt/build/`.

Record anything surprising in the PR description.

- [ ] **Step 4: Commit**

```bash
git add bench/eval/suites
git commit -F - <<'EOF'
feat: eval pilot suites (40 tasks)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
```

### Task 14: Whole-branch review and PR 1

- [ ] **Step 1: Independent review**

Launch one fresh `builder` agent (Opus 5.5, high effort) to review, not to edit. Give it the spec, this plan and `git diff main...eval/ab-harness`, and ask for:
- correctness bugs;
- places where a run could be scored wrongly, or could leak one setup's state into another;
- places where the harness could spend money its guards don't bound.

Check each finding against the code. Fix the valid ones on the branch, with a test each, and commit.

- [ ] **Step 2: Open the PR**

```bash
git push -u origin eval/ab-harness
gh pr create --title "A/B evaluation harness and pilot suites" --body-file <(cat <<'EOF'
The harness from docs/specs/2026-09-29-ab-eval-harness-design.md, built to docs/plans/2026-09-29-ab-eval-harness.md, and the 40-task pilot suites.

- `python -m bench.eval build|prepare|check|run|report`
- Tests never call a model; `check` is free; `run` is capped per run and per batch.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01JJHvD1qjJbo75WHfE9rrki
EOF
)
```

- [ ] **Step 3: The review loop**

Follow the repo's Workflow section:
1. Wait for Bugbot and Greptile, and act on comments about code, bugs and design.
2. Once the PR is clean, re-check for new comments.
3. Merge with `gh pr merge --merge --delete-branch`, then update the local `main`.

## After PR 1: the pilot, then PR 2

These are operational steps, not code. They follow the spec's order.

1. **Prepare.**
   - Run `uv run python -m bench.eval prepare`.
   - It full-clones the 20 localization repos and the 4 structural ones, makes three worktrees per task commit, builds the duckgrep indexes and warms Serena up.
   - Expect roughly an hour, and several GB under the cache: clones, worktrees, indexes, and rust-analyzer's build output for Serena.
2. **Check (free).** Run `uv run python -m bench.eval check`. Every row must say `ok`.
3. **Smoke run (about $1).**
   - Run `uv run python -m bench.eval run --tasks psf__requests-2674,fd-callers-dirname --reps 1 --name smoke`.
   - Then read `bench/eval/runs/smoke/results.jsonl` and one raw stream per setup.
   - Confirm that `pgrep -fl rust-analyzer` shows nothing left running, and that the worktrees are clean.
4. **The pilot (estimated $50–150).**
   - Run `uv run python -m bench.eval run`: 240 runs, 3 at a time, resumable, stopping at $200.
   - It needs the account's overage until 2026-10-02.
5. **The full suites.** `localization.build` takes its sizes (`n_python`, `n_multilingual`, `n_live`, `per_repo_*`). `structural.repo_tasks` asks each repo for a fixed mix: two callers questions, two two-hop questions and one importers question. When PR 2 fixes the full run's sizes from the pilot's variance, give `build` flags for them and draw `--suite full`.
6. **Report.**
   - Run `uv run python -m bench.eval report --write`.
   - On a new branch, commit `bench/RESULTS.md`, with the parameters for the full run: repetitions and task counts, chosen from the variance table.
   - Open PR 2.
