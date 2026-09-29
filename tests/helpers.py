"""Shared test helpers: build small repos, query them, and compare against a fresh index."""

import os
import shutil

from duckgrep import query as q

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture")

# every table freshen maintains, with the columns a fresh build must reproduce (not mtimes)
COLUMNS = {
    "files": "path, lang, family, size, sha, n_lines, skipped, parse_errors",
    "symbols": "*",
    "refs": "*",
    "imports": "*",
    "modules": "*",
    "lines": "*",
    "edges": "*",
}


def rows(root, sql):
    return q.run(root, sql, max_rows=10_000).rows


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def make_repo(path, files):
    """Create a repo at `path` from {relative path: text}; returns the root."""
    os.makedirs(path, exist_ok=True)
    for rel, text in files.items():
        write(str(path), rel, text)
    return str(path)


def snapshot(root, tables=("edges",)):
    return {t: rows(root, f"SELECT {COLUMNS[t]} FROM {t} ORDER BY ALL") for t in tables}


def fresh_snapshot(root, scratch, tables=("edges",)):
    """Snapshot of a from-scratch index of a copy of `root`: what an incremental index must equal."""
    clone = os.path.join(str(scratch), "fresh")
    shutil.rmtree(clone, ignore_errors=True)
    shutil.copytree(root, clone, ignore=shutil.ignore_patterns(".duckgrep"))
    return snapshot(clone, tables)
