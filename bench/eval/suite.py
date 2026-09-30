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
