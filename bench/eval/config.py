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
API_ERROR_STREAK = 10  # recorded runs in a row that ended on an API error stop the batch: a systematic failure
PARALLEL = 3
PREPARE_PARALLEL = 6  # repo/commit pairs `prepare` builds at once
RUST_LSP_LIMIT = 3  # rust-analyzers at once, in runs and in prepare's warm-ups: each can take several GB
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

STRUCTURAL_REPOS_FULL = STRUCTURAL_REPOS + (
    PinnedRepo("pallets/flask", "python", "3.1.0", "ab8149664182b662453a563161aa89013c806dc9"),
    PinnedRepo("pallets/click", "python", "8.1.8", "934813e4d421071a1b3db3973c02fe2721359a6e"),
    PinnedRepo("pallets/jinja", "python", "3.1.5", "877f6e51be8e1765b06d911cfaa9033775f051d1"),
    PinnedRepo("pallets/werkzeug", "python", "3.1.3", "6389612fd1ee1bd93579eed5026e8fd471d04abd"),
    PinnedRepo("Textualize/rich", "python", "v13.9.4", "43d3b04725ab9731727fb1126e35980c62f32377"),
    PinnedRepo("encode/httpx", "python", "0.28.1", "26d48e0634e6ee9cdc0533996db289ce4b430177"),
    PinnedRepo("python-attrs/attrs", "python", "24.3.0", "598494a618410490cfbe0c896b7a544f6d23e0d9"),
    PinnedRepo("encode/starlette", "python", "0.45.3", "4d72fd87ea1c358691a19468168f7217d8ca6a87"),
    PinnedRepo("marshmallow-code/marshmallow", "python", "3.23.2", "90931f0bb3ffcecf90860751dc5f55a5c538d711"),
    PinnedRepo("tqdm/tqdm", "python", "v4.67.1", "0ed5d7f18fa3153834cbac0aa57e8092b217cc16"),
    PinnedRepo("sharkdp/bat", "rust", "v0.25.0", "25f4f96ea3afb6fe44552f3b38ed8b1540ffa1b3"),
    PinnedRepo("sharkdp/hyperfine", "rust", "v1.19.0", "12fec42098642a19855ead34c8cb1e0be28c8ead"),
    PinnedRepo("casey/just", "rust", "1.39.0", "9ec7b60b55cba4d9c095d3b8f119637980286165"),
    PinnedRepo("ajeetdsouza/zoxide", "rust", "v0.9.7", "d74bce3b7418ed965f5056297db8bb081a29121c"),
    PinnedRepo("dandavison/delta", "rust", "0.18.2", "a589ff9debaefdd3992384434120f5a03a103481"),
    PinnedRepo("XAMPPRocky/tokei", "rust", "v13.0.0-alpha.8", "edbd5d5cbb0b7ea0081df0265a8ae6e7742a5051"),
    PinnedRepo("alacritty/alacritty", "rust", "v0.15.0", "53395536aa4ebebcbc0431e7336c2a6857efcff5"),
    PinnedRepo("starship/starship", "rust", "v1.22.1", "d60519607cdd67b81a84a37471c27abb0fa948a8"),
    PinnedRepo("eza-community/eza", "rust", "v0.20.19", "f526208cfe9a80349b5d0cb23cc32a7c78e921e5"),
    PinnedRepo("clap-rs/clap", "rust", "v4.5.27", "eadcc8f66c128272ea309fed3d53d45b9c700b6f"),
    PinnedRepo("tokio-rs/tokio", "rust", "tokio-1.43.0", "5f3296df77ad594779d1fe1a1583078ca9832daf"),
)


@dataclass(frozen=True)
class Profile:
    """How `build` draws a suite. The pilot's reproduces how it was drawn."""

    python: int  # localization tasks at most, and at most `python_per_repo` of them from one repo
    python_per_repo: int
    multilingual: int
    live: int
    rust_per_repo: int
    live_every_date: bool  # the pilot took only Live issues from after the model's training data
    rust_functions: int  # the most functions a Rust key may name
    repos: tuple[PinnedRepo, ...]  # the structural questions' repos
    questions: tuple[int, int, int]  # callers, two-hop and importers questions per repo


EVERY = 10**6  # no limit
PROFILES = {
    "pilot": Profile(10, 3, 5, 5, 2, False, 10, STRUCTURAL_REPOS, (2, 2, 1)),
    # every eligible task; Rust keys of 1-3 functions like Python's, since bigger fixes bundle more
    "full": Profile(EVERY, EVERY, EVERY, EVERY, EVERY, True, 3, STRUCTURAL_REPOS_FULL, (8, 8, 4)),
}

EVAL_DIR = Path(__file__).resolve().parent
SUITES_DIR = EVAL_DIR / "suites"
RUNS_DIR = EVAL_DIR / "runs"
RESULTS_MD = EVAL_DIR.parent / "RESULTS.md"
MIN_FREE_GB = 10.0  # Serena's rust-analyzer builds grow the cache during a batch


def cache_dir() -> Path:
    """Clones, worktrees, indexes and tool caches; DUCKGREP_EVAL_CACHE moves them to a bigger disk. The agent sees
    its worktree's path, so the default name mentions neither duckgrep nor evaluation."""
    return Path(os.environ.get("DUCKGREP_EVAL_CACHE") or Path.home() / ".cache" / "code-tasks").expanduser()
