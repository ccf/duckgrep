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
RETRY_WAITS_S = (60, 300, 900)  # a transient API error is retried after each wait in turn, then stops the batch
PARALLEL = 3
PREPARE_PARALLEL = 6  # repo/commit pairs `prepare` builds at once
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
