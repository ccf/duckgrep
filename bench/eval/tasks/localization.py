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
