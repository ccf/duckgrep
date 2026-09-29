# Wrong-answer fixes before the A/B eval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the verified wrong answers and failure modes that would skew an A/B evaluation of duckgrep on Python and Rust repos.

**Architecture:** Four groups of tasks with disjoint file ownership. Each group is built test-first in its own worktree, and the coordinator merges them:
- **A:** crate-aware Rust module resolution, plus keeping the module context fresh.
- **B:** resolution precision in `schema.EDGES_COMPUTE`, with a new `builtin_names.py`.
- **C:** freshness robustness in `index.py`.
- **D:** the agent-facing surface: `query.py`, `mcp_server.py`, `cli.py` and the table macros.

Every change keeps one invariant: an incrementally maintained index must equal a fresh one.

**Tech Stack:** Python ≥3.10, DuckDB 1.5, tree-sitter 0.26 (grammar packages ≥0.23), mcp 1.x (FastMCP), pytest, uv, ruff, pre-commit.

**Spec:** the verified audit of 2026-09-29: 46 findings, of which 44 were confirmed and 2 partially confirmed; none were refuted. Each task's **Why** gives the findings it fixes, their evidence and the required behaviour. A summary is in `~/.claude/projects/-Users-ccf-git-duckgrep/memory/duckgrep-audit-2026-09-29.md`.

## Global Constraints

- Python 3.10 compatible (`requires-python = ">=3.10"`): no `tomllib`, no 3.11+ syntax.
- `uv run pre-commit run --all-files` passes before every commit (ruff check --fix, ruff format at line length 120, pytest).
- Tests build their repos under `tmp_path` with `helpers.make_repo`; never index a directory outside `tmp_path`.
- Don't edit `tests/helpers.py`, `tests/conftest.py` or `tests/fixture/`; put extra helpers in your own test file.
- After edits, tests assert `snapshot(root, ALL) == fresh_snapshot(root, scratch, ALL)` whenever a change touches extraction, resolution or dirty-marking.
- `SCHEMA_DOC` and `QUERY_DOC` ship in every agent session's tool list; keep them accurate and compact.
- Stay inside your group's files (table below). Commit messages use a `fix:`/`feat:`/`test:` prefix and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

| Group | Owns | Test files |
|---|---|---|
| A | `extract.py`. In `index.py`: new `_rust_crates` and `_module_context`, and the context/candidate code of `freshen` before `BEGIN` | `tests/test_rust.py`, `tests/test_module_context.py` |
| B | New `builtin_names.py`. In `schema.py`: `EDGES_COMPUTE` and the `edges(...)` block of `SCHEMA_DOC`. In `index.py`: only the `CREATE OR REPLACE TEMP TABLE _r` statement in `sync_edges` | `tests/test_resolution.py` |
| C | `index.py`: `extractor_version`, the new `edges_version`, `_compute_edges`, `_pool` and `_grammar_versions`, `_rebuild_edges`, and the transaction/chunk code of `freshen` from `BEGIN` to `return` | `tests/test_freshness.py` |
| D | `query.py`, `mcp_server.py`, `cli.py`, `bench/mcp_smoke.py`, `README.md`. In `schema.py`: the `VIEWS` table macros and the `TABLE MACROS`/`EXAMPLES` part of `SCHEMA_DOC`. In `index.py`: `find_root` and `list_files` | `tests/test_surface.py` |

## Review Focus

1. **A Cargo workspace where two crates both define `auth::Signer`.** `use crate::auth::Signer` must stay inside the file's own crate, and `Signer::new()` must reach only that crate's impl. (Tasks A1, B3; tests `test_crate_path_stays_in_own_crate`, `test_rust_type_calls_prefer_the_type_in_scope`.)
2. **Python that calls `dict.get`, `list.append` or `logger.info` in a repo that defines one method with the same name.** This must never produce a `name` edge, so `callers('Cache.get')` lists only real callers. (Task B1; test `test_builtin_method_names_do_not_match_by_name`.)
3. **An agent query with no `LIMIT` over millions of rows.** The first `max_rows` come back fast and memory stays flat. (Task D1; test `test_oversized_results_stop_at_max_rows`.)
4. **A server killed mid-rebuild after a branch switch.** The next query must rebuild the call graph, not report itself fresh. (Task C1; tests `test_interrupted_edge_rebuild_is_redone`, `test_interrupted_parse_on_rebuild_path_is_redone`.)
5. **Code with backslashes and tab indentation (regexes, Go).** `source()` and `grep()` must return the file's text byte for byte, so an agent can paste it into an edit. (Task D2; test `test_cells_keep_code_text_verbatim`.)

## Deferred (not in this plan)

- **Further freshness gaps:** re-export staleness beyond what B3's bound-receiver marking covers (FRESH-3/4/7), waiting on the lock or answering stale data while another process writes (FRESH-9/F6), time-boxing refresh (F9), and `isError` on MCP errors (F11).
- **JS/TS and Python extraction gaps:** EXT-4..8, 12 and 13, plus tsconfig paths.
- **Resolution gaps:** stdlib-named modules matched by path suffix (CG-9, which needs `__init__.py`-aware roots), inheritance-aware self calls (CG-3, type-inference phase) and multi-hop re-exports (CG-4).
- **The mcp 2 migration.**

---

## Task 0 (coordinator, before the groups start): shared test helpers

**Files:**
- Create: `tests/helpers.py`, `tests/conftest.py`
- Modify: `tests/test_duckgrep.py` (drop its local `FIXTURE`, `repo`, `rows`, `_edges_snapshot`, `_rebuild_snapshot`, `w`)

**Interfaces — Produces:**
- `rows(root, sql) -> list[tuple]`
- `write(root, rel, text)`
- `make_repo(path, files: dict[str, str]) -> str`
- `snapshot(root, tables=("edges",)) -> dict`
- `fresh_snapshot(root, scratch, tables=("edges",)) -> dict`
- `FIXTURE`
- The `repo` fixture

- [ ] **Step 1: Write `tests/helpers.py`**

```python
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
```

- [ ] **Step 2: Write `tests/conftest.py`**

```python
import shutil

import pytest
from helpers import FIXTURE


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns(".duckgrep"))
    return str(root)
```

- [ ] **Step 3: Point `tests/test_duckgrep.py` at the helpers**

Remove `FIXTURE`, the `repo` fixture, `rows`, `_edges_snapshot`, `_rebuild_snapshot` and both nested `w` helpers. Import `from helpers import fresh_snapshot, rows, snapshot, write`. Then replace:
- `_edges_snapshot(repo) == _rebuild_snapshot(repo, tmp_path / "a")` with `snapshot(repo) == fresh_snapshot(repo, tmp_path / "a")`, likewise for `b`, `c` and `x`;
- `w("rel", text)` with `write(repo, "rel", text)`.

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: 20 passed, the same tests as before.

- [ ] **Step 5: Commit** `test: shared helpers (make_repo, snapshot, fresh_snapshot) for parallel test files`

---

## Group A — Rust module resolution and module context

### Task A1: Crate-aware module keys and use paths (EXT-1, EXT-3)

**Why:** `_rs_modpath` keeps only the path after the last `src/` and prefixes it with `crate`, so every crate in a workspace shares keys (`crate::auth`), and `use crate::auth::Signer` in crate b resolves to crate a. That edge gets the confident `import` label. On kmm, 177 of 514 resolved `crate::` imports (34%) point into another crate. Cross-crate `use pmm_core::x` never resolves at all: 1,223 of 2,009 in-repo-looking Rust imports have no target.

**Required:**
- Keys are `<crate>::<module path>`.
- `crate::` maps to the file's own crate; `<other_crate>::` maps to that crate.
- The crate import name is `[lib] name` when set, otherwise `[package] name` with `-` replaced by `_`.
- Files outside any Cargo package keep the old `crate::...` keys.

**Files:**
- Modify: `src/duckgrep/extract.py`: `_rs_modpath`, `_rs_abs`, `_imports_rust`, `module_keys`, and the `mods = ...` line in `extract`.
- Modify: `src/duckgrep/index.py`: add `_rust_crates`; the `ctx = ...` line in `freshen`.
- Create: `tests/test_rust.py`.

**Interfaces — Produces:**
- `ctx["rscrates"]: list[tuple[str, str]]`: `(package dir, crate import name)`, deepest directory first.
- `index._rust_crates(root, paths) -> list[tuple[str, str]]`.
- `extract.module_keys(path, lang, ctx=None)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_rust.py`

```python
"""Rust module resolution across a Cargo workspace."""

from helpers import make_repo, rows

WORKSPACE = {
    "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
    "crates/a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n',
    "crates/a/src/lib.rs": "pub mod auth;\n",
    "crates/a/src/auth.rs": "pub struct Signer;\n\nimpl Signer {\n    pub fn new() -> Self {\n        Signer\n    }\n}\n",
    "crates/b/Cargo.toml": '[package]\nname = "b"\nversion = "0.1.0"\n',
    "crates/b/src/lib.rs": "pub mod auth;\npub mod rest;\n",
    "crates/b/src/auth.rs": "pub struct Signer;\n\nimpl Signer {\n    pub fn new() -> Self {\n        Signer\n    }\n}\n",
    "crates/b/src/rest.rs": (
        "use crate::auth::Signer;\n"  # 1
        "\n"
        "pub fn sign() -> Signer {\n"  # 3
        "    Signer::new()\n"
        "}\n"
        "\n"
        "pub fn helper() {}\n"  # 7
        "\n"
        "mod tests {\n"
        "    use super::helper;\n"  # 10
        "\n"
        "    fn t() {\n"
        "        helper();\n"
        "    }\n"
        "}\n"
    ),
    "crates/c-cli/Cargo.toml": '[package]\nname = "c-cli"\nversion = "0.1.0"\n',
    "crates/c-cli/src/main.rs": "use a::auth::Signer;\n\nfn main() {\n    let _s = Signer::new();\n}\n",
}


def workspace(tmp_path, **extra):
    return make_repo(tmp_path / "ws", {**WORKSPACE, **extra})


def target(root, path, line):
    return rows(root, f"SELECT target_path FROM imports_resolved WHERE path = '{path}' AND line = {line}")


def test_module_keys_are_qualified_by_crate(tmp_path):
    root = workspace(tmp_path)
    got = dict(rows(root, "SELECT path, key FROM modules WHERE family = 'rs'"))
    assert got["crates/a/src/auth.rs"] == "a::auth"
    assert got["crates/b/src/auth.rs"] == "b::auth"
    assert got["crates/b/src/lib.rs"] == "b"
    assert got["crates/c-cli/src/main.rs"] == "c_cli"


def test_crate_path_stays_in_own_crate(tmp_path):
    root = workspace(tmp_path)
    assert target(root, "crates/b/src/rest.rs", 1) == [("crates/b/src/auth.rs",)]
    got = rows(
        root,
        "SELECT dst_path, resolution FROM edges WHERE src_path = 'crates/b/src/rest.rs' AND line = 3 AND name = 'Signer'",
    )
    assert got == [("crates/b/src/auth.rs", "import")]


def test_other_crate_path_resolves(tmp_path):
    root = workspace(tmp_path)
    assert target(root, "crates/c-cli/src/main.rs", 1) == [("crates/a/src/auth.rs",)]
    got = rows(
        root,
        "SELECT dst_path, resolution FROM edges WHERE src_path = 'crates/c-cli/src/main.rs' AND line = 4 "
        "AND name = 'Signer'",
    )
    assert got == [("crates/a/src/auth.rs", "import")]


def test_lib_name_override(tmp_path):
    root = workspace(
        tmp_path,
        **{
            "crates/a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n\n[lib]\nname = "a_core"\n',
            "crates/c-cli/src/main.rs": "use a_core::auth::Signer;\n\nfn main() {\n    let _s = Signer::new();\n}\n",
        },
    )
    assert target(root, "crates/c-cli/src/main.rs", 1) == [("crates/a/src/auth.rs",)]


def test_uniform_path_use_of_child_module(tmp_path):
    root = workspace(tmp_path, **{"crates/b/src/lib.rs": "pub mod auth;\npub mod rest;\nuse auth::Signer;\n"})
    assert target(root, "crates/b/src/lib.rs", 3) == [("crates/b/src/auth.rs",)]


def test_external_crates_stay_unresolved(tmp_path):
    root = workspace(tmp_path, **{"crates/b/src/io.rs": "use std::collections::HashMap;\nuse serde::Serialize;\n"})
    got = rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'crates/b/src/io.rs' ORDER BY line")
    assert got == [(None,), (None,)]


def test_loose_rust_without_cargo_keeps_crate_keys(repo):
    # tests/fixture/rs has no Cargo.toml
    assert ("rs/src/shapes.rs", "crate::shapes") in rows(repo, "SELECT path, key FROM modules WHERE family = 'rs'")
```

- [ ] **Step 2: Run** `uv run pytest tests/test_rust.py -v`. Expected failures: `test_module_keys_are_qualified_by_crate` (keys are `crate::auth`), `test_crate_path_stays_in_own_crate` (resolves to `crates/a/src/auth.rs`), `test_other_crate_path_resolves` (`None`), `test_lib_name_override`, and `test_uniform_path_use_of_child_module`. The last two tests pass: they pin existing behaviour.

- [ ] **Step 3: Implement.** In `extract.py`, replace `_rs_modpath` and `_rs_abs`:

```python
RS_EXTERNAL = frozenset({"std", "core", "alloc", "proc_macro", "test"})


def _rs_crate(path: str, ctx) -> tuple[str, str] | None:
    """(package dir, crate import name) of the Cargo package that owns `path`; None outside any package."""
    for d, name in (ctx or {}).get("rscrates", ()):
        if not d or path.startswith(d + "/"):
            return d, name
    return None


def _rs_roots(path: str, ctx) -> tuple[list[str], list[str]]:
    """(crate root segments, module segments) of a Rust file.

    Files under <package>/src belong to crate <name>. tests/, examples/ and benches/ files are their own
    crate roots, keyed <name>::tests and so on. Other files outside src/ (build.rs) get a key no import can
    name. Without a Cargo.toml the old scheme applies: 'crate' + the path after the last src/.
    """
    crate = _rs_crate(path, ctx)
    if crate is None:
        rel = path.split("/")
        if "src" in rel:
            rel = rel[len(rel) - rel[::-1].index("src") :]
        root = ["crate"]
    else:
        d, name = crate
        rel = (path[len(d) + 1 :] if d else path).split("/")
        if rel[0] == "src":
            root, rel = [name], rel[1:]
        elif rel[0] in ("tests", "examples", "benches") and len(rel) > 1:
            root, rel = [name, rel[0]], rel[1:]
        else:
            return [name, "!" + "/".join(rel)], []
    stem = rel[-1].rsplit(".", 1)[0]
    return root, rel[:-1] + ([] if stem in ("lib", "main", "mod") else [stem])


def _rs_modpath(path: str, ctx=None) -> list[str]:
    root, mods = _rs_roots(path, ctx)
    return root + mods


def _rs_abs(mod: str, path: str, ctx=None, inline=()) -> str:
    """Module key of a `use` path written in `path`, inside the inline `mod` blocks `inline`."""
    segs = mod.split("::") if mod else []
    if segs and segs[0] == "":  # ::name
        segs = segs[1:]
    root, mods = _rs_roots(path, ctx)
    here = root + mods + list(inline)
    crates = {name for _, name in (ctx or {}).get("rscrates", ())}
    if not segs:
        return "::".join(here)
    head = segs[0]
    if head == "crate":
        segs = root + segs[1:]
    elif head == "self":
        segs = here + segs[1:]
    elif head == "super":
        n = 0
        while n < len(segs) and segs[n] == "super":
            n += 1
        segs = here[: max(len(root), len(here) - n)] + segs[n:]
    elif head in crates or head in RS_EXTERNAL:
        pass
    else:  # 2018 uniform paths: an item of the current module (`mod auth; use auth::Signer;`)
        segs = here + segs
    return "::".join(segs)
```

In `_imports_rust`, find the enclosing inline mods once. Then pass `ctx` and `inline` to both `_rs_abs` calls:

```python
    inline = []
    p = node.parent
    while p is not None:
        if p.type == "mod_item" and p.child_by_field_name("body") is not None:
            nm = p.child_by_field_name("name")
            if nm is not None:
                inline.append(_text(src, nm))
        p = p.parent
    inline.reverse()
    # ... then:  key = _rs_abs(mod, path, ctx, inline)   (both places)
```

Change `module_keys(path: str, lang: str, ctx: dict | None = None)`; its Rust branch becomes `return [("::".join(_rs_modpath(path, ctx)), 0)]`. In `extract` use `module_keys(path, lang, ctx)`.

In `index.py`, add `import re` and:

```python
_TOML_SECTION = re.compile(r"^\s*\[\s*([A-Za-z0-9_.\-]+)\s*\]")
_TOML_NAME = re.compile(r"""^\s*name\s*=\s*["']([^"']+)["']""")


def _rust_crates(root: str, paths: list[str]) -> list[tuple[str, str]]:
    """(package dir, crate import name) for every Cargo.toml with a [package]; deepest dir first."""
    crates = []
    for p in paths:
        if posixpath.basename(p) != "Cargo.toml":
            continue
        section, names = None, {}
        try:
            with open(os.path.join(root, p), encoding="utf-8", errors="replace") as f:
                for ln in f:
                    m = _TOML_SECTION.match(ln)
                    if m:
                        section = m.group(1)
                        continue
                    m = _TOML_NAME.match(ln)
                    if m and section in ("package", "lib"):
                        names.setdefault(section, m.group(1))
        except OSError:
            continue
        if "package" in names:
            crates.append((posixpath.dirname(p), names.get("lib", names["package"]).replace("-", "_")))
    return sorted(crates, key=lambda c: -len(c[0]))
```

In `freshen`, replace the `ctx = ...` line (Task A3 replaces it again):

```python
    parsed = {lang for _, lang, parse in jobs if parse}
    ctx = {}
    if "go" in parsed:
        ctx["gomods"] = _go_modules(root, paths)
    if "rust" in parsed:
        ctx["rscrates"] = _rust_crates(root, paths)
```

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass, including the fixture's Rust cases in `test_edges`.
- [ ] **Step 5: Commit** `fix: crate-aware Rust module keys; crate:: and other_crate:: resolve within the workspace`

### Task A2: `self`/`super` inside inline `mod` blocks (EXT-2)

**Why:** `_rs_abs` derives `super` from the file path only. In `mod tests { use super::*; }` it therefore points one level too high: at the crate root, or at another crate's `lib.rs`. In kmm, 181 of these imports land on `crates/pmm-md/src/lib.rs`. **Required:** inside `mod x { ... }`, `self` is `<file module>::x`, and `super` is the file module.

**Files:** Test: `tests/test_rust.py`. The implementation shipped with A1's `inline` handling. This task pins it and fixes anything the tests expose.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_rust.py`)

```python
def test_super_in_inline_mod_is_the_enclosing_file(tmp_path):
    root = workspace(tmp_path)
    assert target(root, "crates/b/src/rest.rs", 10) == [("crates/b/src/rest.rs",)]


def test_nested_inline_mods(tmp_path):
    deep = "pub fn top() {}\n\nmod x {\n    mod y {\n        use super::super::top;\n    }\n}\n"
    root = workspace(tmp_path, **{"crates/b/src/deep.rs": deep})
    assert target(root, "crates/b/src/deep.rs", 5) == [("crates/b/src/deep.rs",)]
```

- [ ] **Step 2: Verify RED.** Run `git stash`, `uv run pytest tests/test_rust.py -k inline -v`, then `git stash pop`. The tests should fail on the pre-A1 code. If A1 is already committed, check out `HEAD~1 -- src/duckgrep/extract.py` temporarily instead.
- [ ] **Step 3:** If they pass on A1's code, no implementation change is needed.
- [ ] **Step 4: Run** `uv run pytest -q`.
- [ ] **Step 5: Commit** `test: use super/self inside inline Rust mods resolves to the enclosing file`

### Task A3: Re-parse Go and Rust files when go.mod or Cargo.toml renames a module (EXT-11, FRESH-5)

**Why:** import keys are fixed at extraction time from `go.mod` module paths and, after A1, from Cargo crate names. Editing only `go.mod` or `Cargo.toml` re-parses nothing, so keys stay stale indefinitely. The reindex reports `+0 ~1 -0` and the import stays unresolved. **Required:**
- When the parsed context (the go.mod `(dir, module)` pairs, or the Cargo `(dir, crate name)` pairs) changes, every file of that language is re-parsed.
- Edits that leave it unchanged, such as a new dependency line, re-parse nothing.
- The stored context is written in the same transaction as the rows.

**Files:**
- Modify: `src/duckgrep/index.py`: new `_module_context`; the pre-`BEGIN` part of `freshen`.
- Create: `tests/test_module_context.py`.

**Interfaces — Produces:** `meta('module_ctx')` holds the JSON context.

- [ ] **Step 1: Write the failing tests**

```python
"""go.mod and Cargo.toml changes re-key the imports of the files that depend on them."""

from duckgrep.index import connect, freshen
from helpers import fresh_snapshot, make_repo, rows, snapshot, write

ALL = ("files", "symbols", "refs", "imports", "modules", "lines", "edges")

GO = {
    "go.mod": "module example.com/old\n\ngo 1.22\n",
    "store/store.go": "package store\n\nfunc Open() {}\n",
    "app/app.go": 'package app\n\nimport "example.com/new/store"\n\nfunc Run() { store.Open() }\n',
}

RS = {
    "Cargo.toml": '[workspace]\nmembers = ["a", "c"]\n',
    "a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n',
    "a/src/lib.rs": "pub fn go() {}\n",
    "c/Cargo.toml": '[package]\nname = "c"\nversion = "0.1.0"\n',
    "c/src/main.rs": "use core_a::go;\n\nfn main() {\n    go();\n}\n",
}


def test_go_module_rename_rekeys_imports(tmp_path):
    root = make_repo(tmp_path / "go", GO)
    assert rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'app/app.go'") == [(None,)]
    write(root, "go.mod", "module example.com/new\n\ngo 1.22\n")
    assert rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'app/app.go'") == [("store/store.go",)]
    assert rows(root, "SELECT resolution FROM edges WHERE src_path = 'app/app.go' AND name = 'Open'") == [("module",)]
    assert snapshot(root, ALL) == fresh_snapshot(root, tmp_path, ALL)


def test_crate_rename_rekeys_imports(tmp_path):
    root = make_repo(tmp_path / "rs", RS)
    assert rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'c/src/main.rs'") == [(None,)]
    write(root, "a/Cargo.toml", '[package]\nname = "core-a"\nversion = "0.1.0"\n')
    assert rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'c/src/main.rs'") == [("a/src/lib.rs",)]
    assert snapshot(root, ALL) == fresh_snapshot(root, tmp_path, ALL)


def test_dependency_edit_does_not_reparse(tmp_path):
    root = make_repo(tmp_path / "rs", RS)
    rows(root, "SELECT 1")
    write(root, "a/Cargo.toml", '[package]\nname = "a"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1"\n')
    con = connect(root)
    try:
        st = freshen(con, root)
    finally:
        con.close()
    assert (st.changed, st.parsed) == (1, 0)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_module_context.py -v`. The two rename tests should fail: the import stays `None` after the change. The dependency test passes: it pins the rule that a dependency edit doesn't re-parse.

- [ ] **Step 3: Implement** in `index.py`:

```python
def _is_ctx_file(p: str) -> bool:
    return posixpath.basename(p) in ("go.mod", "Cargo.toml")


def _module_context(con, root, paths, candidates, deleted, full) -> tuple[dict, set[str], str | None]:
    """Extraction context (go.mod module paths, Cargo crate names), the languages to re-parse because it
    changed, and the JSON to store (None if unchanged). Re-read only when a go.mod or Cargo.toml changed."""
    row = con.execute("SELECT value FROM meta WHERE key = 'module_ctx'").fetchone()
    old = {k: [tuple(x) for x in v] for k, v in json.loads(row[0]).items()} if row else None
    if old is not None and not full and not any(_is_ctx_file(p) for p, _, _ in candidates) \
            and not any(_is_ctx_file(p) for p in deleted):
        return old, set(), None
    new = {"gomods": _go_modules(root, paths), "rscrates": _rust_crates(root, paths)}
    if full or old is None:
        reparse = set() if full else {"go", "rust"}
    else:
        reparse = {lang for lang, k in (("go", "gomods"), ("rust", "rscrates")) if old.get(k) != new[k]}
    return new, reparse, json.dumps(new)
```

In `freshen`:
- Keep a `stat` dict in the listing loop: `stat[p] = (s.st_size, s.st_mtime_ns)`.
- Right after `deleted = ...`, add:

```python
    ctx, reparse, ctx_json = _module_context(con, root, paths, candidates, deleted, full)
    if reparse:  # the context changed: files of that language parse differently though their bytes didn't
        have = {p for p, _, _ in candidates}
        candidates += [(p, *stat[p]) for p in listed if p not in have and lang_of(p) in reparse]
```

- In pass 1, skip a file as merely touched only when `lang not in reparse`:
  `if k is not None and k[2] == sha and not full and lang not in reparse:`.
- Delete the A1 `ctx = ...` block.
- Inside the transaction, before the final `COMMIT`: `if ctx_json is not None: con.execute("INSERT OR REPLACE INTO meta VALUES ('module_ctx', ?)", [ctx_json])`.
- Add `import json`.

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `fix: re-parse Go/Rust files when go.mod or Cargo.toml renames a module`

---

## Group B — resolution precision

### Task B1: Builtin and external names never match by name; star imports resolve (CG-1)

**Why:** for receiver calls, the name tier counts only in-repo members with that name. It never considers that the receiver may be a builtin or an external object. In agentcairn, `callers('JudgedCache.get')` returns 387 rows: 279 are `dict.get` and similar calls, and 14 are real. In claude-code, 688 of the 787 `callers('createClock.now')` rows are `Date.now()`. Across 300 sampled name-tier sites, jedi puts the target outside the repo in 68% of agentcairn's and 23% of open-webui's. Bare names are worse still: a bare call can only reach another file through an import, so name-matching it across files is always a guess.

**Required, for references no confident tier resolved:**
1. **Receiver bound to an import that doesn't resolve in the repo** (`json.dumps`, `HashMap::new`): calls are `unresolved`; other kinds get no edge.
2. **Builtin or global receiver the file doesn't rebind** (`Date`, `Math`, `str`, `Vec`): the same.
3. **Method name that builtin types also have** (`get`, `append`, `push`, `clone`): `ambiguous`, with `n_candidates` = the in-repo count.
4. **Bare names:** never matched by name, except Rust macros and TypeScript `.d.ts` ambient declarations. A new star-import tier resolves `from m import *` and `use m::*`, directly or through one re-export.

**Files:** Create `src/duckgrep/builtin_names.py`; modify `src/duckgrep/schema.py` (`EDGES_COMPUTE`, `SCHEMA_DOC` edges block); create `tests/test_resolution.py`.

**Interfaces — Produces:**
- `builtin_names.METHODS` and `builtin_names.GLOBALS`: `frozenset[tuple[str, str]]` of `(family, name)`.
- `EDGES_COMPUTE` keeps its `{source}`, `{where}` and `{cap}` placeholders.

- [ ] **Step 1: Write the failing tests** in `tests/test_resolution.py`

```python
"""Resolution precision: builtin and external names, star imports, deterministic labels, bound classes."""

from duckgrep import schema
from duckgrep.index import connect
from helpers import fresh_snapshot, make_repo, rows, snapshot, write

ALL = ("files", "symbols", "refs", "imports", "modules", "lines", "edges")
CARGO = '[package]\nname = "k"\nversion = "0.1.0"\n'


def edges_at(root, path, name):
    return rows(
        root,
        f"SELECT dst_path, dst_qualname, resolution FROM edges WHERE src_path = '{path}' AND name = '{name}' "
        "ORDER BY ALL",
    )


def test_builtin_method_names_do_not_match_by_name(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "cache.py": "class Cache:\n    def get(self, key):\n        return key\n",
            "use.py": "def lookup(d):\n    return d.get('x')\n",
        },
    )
    assert edges_at(root, "use.py", "get") == [(None, None, "ambiguous")]
    assert rows(root, "SELECT * FROM callers('Cache.get')") == []


def test_calls_on_external_modules_are_unresolved(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "store.py": "class Store:\n    def dumps(self):\n        return ''\n",
            "ext.py": "import json\n\n\ndef save(x):\n    return json.dumps(x)\n",
        },
    )
    assert edges_at(root, "ext.py", "dumps") == [(None, None, "unresolved")]


def test_calls_on_js_globals_are_unresolved(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "clock.ts": "export class Clock {\n  now() { return 1; }\n}\n",
            "use.ts": "export function stamp() { return Date.now(); }\n",
        },
    )
    assert edges_at(root, "use.ts", "now") == [(None, None, "unresolved")]


def test_rust_prelude_and_std_receivers_are_unresolved(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "Cargo.toml": CARGO,
            "src/lib.rs": "pub mod stack;\npub mod use_it;\n",
            "src/stack.rs": "pub struct Stack;\n\nimpl Stack {\n    pub fn new() -> Self {\n        Stack\n    }\n}\n",
            "src/use_it.rs": (
                "use std::collections::HashMap;\n\npub fn f() {\n"
                "    let _v: Vec<u8> = Vec::new();\n    let _m: HashMap<u8, u8> = HashMap::new();\n}\n"
            ),
        },
    )
    assert edges_at(root, "src/use_it.rs", "new") == [(None, None, "unresolved"), (None, None, "unresolved")]


def test_bare_names_do_not_match_other_files_by_name(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "numberformat.py": "def format(number):\n    return str(number)\n",
            "use.py": "def show(x):\n    return format(x, '.2f')\n",
        },
    )
    assert edges_at(root, "use.py", "format") == [(None, None, "unresolved")]


def test_star_imports_resolve_bare_names(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "pkg/__init__.py": "",
            "pkg/consts.py": "def helper():\n    return 1\n",
            "pkg/api.py": "from .consts import helper\n",
            "pkg/use.py": "from .consts import *\n\n\ndef f():\n    return helper()\n",
            "pkg/use2.py": "from .api import *\n\n\ndef g():\n    return helper()\n",
        },
    )
    assert edges_at(root, "pkg/use.py", "helper") == [("pkg/consts.py", "helper", "import")]
    assert edges_at(root, "pkg/use2.py", "helper") == [("pkg/consts.py", "helper", "import")]


def test_rust_macros_still_match_by_name(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "Cargo.toml": CARGO,
            "src/lib.rs": "#[macro_use]\nmod macros;\nmod user;\n",
            "src/macros.rs": "macro_rules! my_mac {\n    () => {};\n}\n",
            "src/user.rs": "pub fn f() {\n    my_mac!();\n}\n",
        },
    )
    assert edges_at(root, "src/user.rs", "my_mac") == [("src/macros.rs", "my_mac", "name")]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_resolution.py -v`. Expected failures:
  - The builtin, external, JS-global, Rust-prelude and bare-name tests fail: each gets a `name` edge to the in-repo namesake.
  - The star-import test fails with `name` instead of `import`.
  - The macro test passes; it pins existing behaviour.

- [ ] **Step 3: Create `src/duckgrep/builtin_names.py`**

```python
"""Names that belong to a language or its standard library, not to the repo.

Without types, `d.get()` would link to the repo's only `get` method and `Date.now()` to some class's
`now`. Calls on these names resolve through scope and imports, or not at all.
"""

# methods of builtin types (a receiver of unknown type calling one of these is probably a builtin)
_PY_METHODS = """
add append as_integer_ratio bit_count bit_length capitalize casefold center clear conjugate copy count critical
debug decode difference difference_update discard encode endswith error exception expandtabs extend find format
format_map from_bytes fromhex fromkeys get hex index info insert intersection intersection_update is_integer
isalnum isalpha isascii isdecimal isdigit isdisjoint isidentifier islower isnumeric isprintable isspace issubset
issuperset istitle isupper items join keys ljust lower lstrip maketrans partition pop popitem remove removeprefix
removesuffix replace reverse rfind rindex rjust rpartition rsplit rstrip setdefault sort split splitlines
startswith strip swapcase symmetric_difference symmetric_difference_update title to_bytes translate union
update upper values warn warning zfill
"""
_JS_METHODS = """
add apply at bind call catch charAt charCodeAt clear codePointAt concat debug delete endsWith entries error every
exec fill filter finally find findIndex findLast findLastIndex flat flatMap forEach get getTime has
hasOwnProperty includes indexOf info join json keys lastIndexOf localeCompare log map match matchAll normalize
padEnd padStart pop push reduce reduceRight repeat replace replaceAll reverse search set shift slice some sort
splice split startsWith substring test text then toFixed toISOString toLowerCase toString toUpperCase trim
trimEnd trimStart unshift valueOf values warn
"""
_RS_METHODS = """
all and_then any as_bytes as_deref as_mut as_ptr as_ref as_slice as_str binary_search borrow borrow_mut bytes
capacity chain chars chunks clone cloned cmp collect contains contains_key copied count dedup drain ends_with
entry enumerate eq expect extend extend_from_slice filter filter_map find first flat_map flatten fmt fold
for_each from get get_mut get_or_insert_with hash insert into into_iter is_empty is_err is_none is_ok is_some
iter iter_mut join keys last len lines lock map map_err max min next ok ok_or ok_or_else or_default or_insert
or_insert_with parse partial_cmp pop position push push_str read recv remove replace retain rev send skip sort
sort_by sort_by_key sort_unstable split split_off starts_with step_by sum take to_lowercase to_owned to_string
to_uppercase to_vec trim truncate try_from try_into unwrap unwrap_or unwrap_or_default unwrap_or_else values
windows with_capacity write zip
"""

# receivers that are builtins or globals when the file doesn't define or import the name itself
_PY_GLOBALS = "bool bytearray bytes complex dict float frozenset int list object set str tuple type"
_JS_GLOBALS = """
Array ArrayBuffer Atomics BigInt Boolean Buffer DataView Date Error Float32Array Float64Array Int16Array
Int32Array Int8Array Intl JSON Map Math Number Object Promise Proxy RangeError Reflect RegExp Set String Symbol
TypeError URL URLSearchParams Uint16Array Uint32Array Uint8Array WeakMap WeakRef WeakSet console crypto document
globalThis localStorage location navigator performance process sessionStorage window
"""
_RS_GLOBALS = """
Arc BTreeMap BTreeSet BinaryHeap Box Cell Cow Duration Err HashMap HashSet Instant Mutex None Ok Option Path
PathBuf PhantomData Rc RefCell Result RwLock Some String SystemTime Vec VecDeque alloc bool char core f32 f64
i128 i16 i32 i64 i8 isize std str u128 u16 u32 u64 u8 usize
"""


def _pairs(family: str, words: str) -> frozenset[tuple[str, str]]:
    return frozenset((family, w) for w in words.split())


METHODS = _pairs("py", _PY_METHODS) | _pairs("js", _JS_METHODS) | _pairs("rs", _RS_METHODS)
GLOBALS = _pairs("py", _PY_GLOBALS) | _pairs("js", _JS_GLOBALS) | _pairs("rs", _RS_GLOBALS)
```

- [ ] **Step 4: Change `EDGES_COMPUTE`** in `schema.py`. The builtin tables are inlined as `VALUES` when the module loads; `{source}`/`{where}`/`{cap}` stay for `.format()`.

  (a) Add `from . import builtin_names` at the top of `schema.py`. Rename the string to `_EDGES_TEMPLATE` and, after it, add:

```python
def _values(pairs) -> str:
    return ", ".join(f"('{f}', '{n}')" for f, n in sorted(pairs))


EDGES_COMPUTE = _EDGES_TEMPLATE.replace("@BUILTIN_METHODS@", _values(builtin_names.METHODS)).replace(
    "@BUILTIN_GLOBALS@", _values(builtin_names.GLOBALS)
)
```

  (b) In the `r` CTE, add the receiver's first and last identifiers:

```sql
    SELECT r.*, f.family, regexp_replace(r.path, '/[^/]*$', '') AS dir,
           regexp_extract(r.receiver, '^[A-Za-z_$][A-Za-z0-9_$]*') AS recv_root,
           regexp_extract(r.receiver, '[A-Za-z_$][A-Za-z0-9_$]*$') AS recv_last
```

  (c) After the `rx` CTE, add:

```sql
ext AS (  -- local names bound by imports that don't resolve inside the repo (stdlib, third party)
    SELECT DISTINCT i.path, i."local" FROM imps i
    WHERE i."local" IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM imp WHERE imp.path = i.path AND imp."local" = i."local")
),
builtin_methods(family, name) AS (VALUES @BUILTIN_METHODS@),
builtin_globals(family, name) AS (VALUES @BUILTIN_GLOBALS@),
glob AS (  -- builtin receivers (Date, Math, str, Vec ...) the file doesn't rebind
    SELECT DISTINCT r.path, r.recv_root AS name FROM r
    JOIN builtin_globals g ON g.family = r.family AND g.name = r.recv_root
    WHERE NOT EXISTS (SELECT 1 FROM imps i WHERE i.path = r.path AND i."local" = r.recv_root)
      AND NOT EXISTS (SELECT 1 FROM symbols s WHERE s.path = r.path AND s.name = r.recv_root AND s.parent IS NULL)
),
```

  (d) At the end of `t1`, before the `qualified` branch, add the star-import tier:

```sql
  UNION ALL
    -- `from m import *` / `use m::*`: a bare name defined at the top of the star-imported module
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'import'
    FROM r
    JOIN imp i ON i.path = r.path AND i.name = '*' AND i."local" IS NULL
    JOIN sym s ON s.path = i.target_path AND s.name = r.name AND s.parent IS NULL
    WHERE r.receiver IS NULL
  UNION ALL
    -- ... or re-exported by it
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'import'
    FROM r
    JOIN imp i ON i.path = r.path AND i.name = '*' AND i."local" IS NULL
    JOIN rx ON rx.mod_path = i.target_path AND (rx."local" = r.name OR (rx.name = '*' AND rx."local" IS NULL))
    JOIN sym s ON s.path = rx.target_path AND s.parent IS NULL
           AND s.name = CASE WHEN rx.name = '*' THEN r.name ELSE coalesce(nullif(rx.name, 'default'), rx."local") END
    WHERE r.receiver IS NULL
```

  (e) Replace the `nc`, `rest` and `out` CTEs with:

```sql
nc AS (
    SELECT family, name,
           count(*) FILTER (WHERE kind = 'macro' OR ends_with(path, '.d.ts')) AS n_bare,
           count(*) FILTER (WHERE parent IS NOT NULL OR family IN ('go', 'rs')) AS n_member
    FROM sym GROUP BY ALL
),
rest AS (
    SELECT r.*, CASE WHEN r.receiver IS NULL THEN nc.n_bare ELSE nc.n_member END AS n,
           (x.path IS NOT NULL OR g.path IS NOT NULL) AS external,
           bm.name IS NOT NULL AS builtin_method
    FROM r
    LEFT JOIN nc ON nc.family = r.family AND nc.name = r.name
    LEFT JOIN ext x ON r.receiver IS NOT NULL AND x.path = r.path AND x."local" = r.recv_root
    LEFT JOIN glob g ON r.receiver IS NOT NULL AND g.path = r.path AND g.name = r.recv_root
    LEFT JOIN builtin_methods bm ON r.receiver IS NOT NULL AND bm.family = r.family AND bm.name = r.name
    ANTI JOIN t1refs t ON t.path = r.path AND t.line = r.line AND t.col = r.col
),
out AS (
    SELECT path, scope, line, col, kind, name, receiver, dst_path, dst_qualname, dst_kind, dst_line, resolution,
           count(*) OVER (PARTITION BY path, line, col) AS n
    FROM t1d
  UNION ALL
    -- by name alone: receiver calls of unknown type. A bare name needs an import to reach another file,
    -- except Rust macros and TypeScript ambient (.d.ts) declarations.
    SELECT r.path, r.scope, r.line, r.col, r.kind, r.name, r.receiver,
           s.path, s.qualname, s.kind, s.start_line, 'name', r.n
    FROM rest r JOIN sym s ON s.name = r.name AND s.family = r.family
    WHERE r.kind <> 'name' AND r.n BETWEEN 1 AND {cap} AND NOT r.external AND NOT r.builtin_method
      AND CASE WHEN r.receiver IS NULL THEN s.kind = 'macro' OR ends_with(s.path, '.d.ts')
               ELSE s.parent IS NOT NULL OR s.family IN ('go', 'rs') END
  UNION ALL
    SELECT path, scope, line, col, kind, name, receiver, NULL, NULL, NULL, NULL, 'ambiguous', n
    FROM rest WHERE kind <> 'name' AND NOT external AND (n > {cap} OR (builtin_method AND n > 0))
  UNION ALL
    SELECT path, scope, line, col, kind, name, receiver, NULL, NULL, NULL, NULL, 'unresolved', 0
    FROM rest WHERE kind = 'call' AND (external OR coalesce(n, 0) = 0)
)
```

  (f) Update the resolution block of `SCHEMA_DOC`:

```
    self | local | package | import | module | qualified   confident (scope, import and star-import analysis)
    name        obj.method() matched by method name only, <= 10 candidates (one row each; n_candidates = how many)
    ambiguous   > 10 candidates, or a method builtin types also have (get, append, push ...); dst_* NULL
    unresolved  call to something not defined in the repo (stdlib, builtins, third party); dst_* NULL
  A bare name never matches by name: it resolves through its file's scope and imports, or not at all.
```

- [ ] **Step 5: Run** `uv run pytest -q`. Expected: all pass, including every `test_edges` case and `test_macros`.
- [ ] **Step 6: Commit** `fix: builtin, external and bare names no longer match by name; star imports resolve`

### Task B2: Deterministic choice between confident tiers (FRESH-8)

**Why:** `SELECT DISTINCT ON (path, line, col, dst_path, dst_qualname) * FROM t1` has no `ORDER BY`. When two tiers reach the same target, the label is arbitrary: 35 of 40 runs gave `import` and 5 gave `local`. **Required:** the highest tier wins (self > local > package > import > module > qualified), then the lowest `dst_line`.

**Files:** `schema.py` (`t1d`), `tests/test_resolution.py`.

- [ ] **Step 1: Write the failing test**

```python
def test_confident_label_is_deterministic(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "lib/__init__.py": "from .core import helper\n",
            "lib/core.py": "from lib import helper\n\n\ndef helper():\n    return 1\n\n\ndef use():\n    return helper()\n",
        },
    )
    rows(root, "SELECT 1")
    con = connect(root)
    try:
        labels = set()
        for _ in range(40):
            con.execute("DELETE FROM edges")
            con.execute(schema.EDGES_COMPUTE.format(source="refs", where="TRUE", cap=schema.NAME_CAP))
            q = "SELECT resolution FROM edges WHERE src_path = 'lib/core.py' AND name = 'helper' AND line = 9"
            labels |= set(con.execute(q).fetchall())
    finally:
        con.close()
    assert labels == {("local",)}
```

- [ ] **Step 2: Run** it. It is expected to fail with `{('import',), ('local',)}` or `{('import',)}`. If DuckDB happens to pick `local` every time here, note that in the commit and keep the test as a pin.
- [ ] **Step 3: Replace `t1d`**

```sql
t1d AS (  -- one row per (ref, target): the strongest tier, then the first definition
    SELECT DISTINCT ON (path, line, col, dst_path, dst_qualname) * FROM t1
    ORDER BY path, line, col, dst_path, dst_qualname,
             list_position(['self', 'local', 'package', 'import', 'module', 'qualified'], resolution), dst_line
),
```

- [ ] **Step 4: Run** `uv run pytest -q`.
- [ ] **Step 5: Commit** `fix: deterministic resolution label when two confident tiers reach one target`

### Task B3: `qualified` only for a class bound in the file (CG-5), and marking that follows receivers

**Why:** the `qualified` branch matches `s.parent = r.receiver OR ends_with(s.parent, '.' || r.receiver)` with no language, import or path filter. As a result:
- In django, `DatabaseClient.settings_to_cmd_args_env` gets 4 confident edges (mysql, sqlite3, oracle, postgresql), even though the file imports the postgresql client.
- In kmm, `Backoff::new` reaches 4 crates although `Backoff` is defined in the same file.
- A lowercase `client.fetch()` matches the closure `Service.client.fetch`.

**Required:**
- `qualified` needs the same language and a type-shaped receiver (last segment capitalised).
- The class must be bound in the file: defined there, imported by name (directly or through one re-export), or reached through an imported module (`mod.Class.m()`).
- Unbound matches fall to the name tier.
- Because edges now depend on the receiver's binding, refs whose receiver root or last segment is a bound import local get recomputed when that import's target changes.

**Files:**
- `schema.py`: the `qualified` branch.
- `index.py`: only the `CREATE OR REPLACE TEMP TABLE _r` statement in `sync_edges`.
- `tests/test_resolution.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_qualified_calls_reach_only_the_bound_class(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "pkg/__init__.py": "",
            "pkg/mysql/__init__.py": "",
            "pkg/mysql/client.py": "class Client:\n    def settings(self):\n        return 1\n",
            "pkg/pg/__init__.py": "",
            "pkg/pg/client.py": "class Client:\n    def settings(self):\n        return 2\n",
            "web/client.ts": "export class Client {\n  static settings() { return 3; }\n}\n",
            "use.py": "from pkg.pg.client import Client\n\n\ndef f():\n    return Client.settings()\n",
        },
    )
    assert edges_at(root, "use.py", "settings") == [("pkg/pg/client.py", "Client.settings", "qualified")]


def test_unbound_class_calls_fall_back_to_name(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "class Client:\n    def settings(self):\n        return 1\n",
            "b.py": "class Client:\n    def settings(self):\n        return 2\n",
            "use.py": "def f(Client):\n    return Client.settings()\n",
        },
    )
    assert edges_at(root, "use.py", "settings") == [
        ("a.py", "Client.settings", "name"),
        ("b.py", "Client.settings", "name"),
    ]


def test_lowercase_receivers_are_not_classes(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "svc.py": "class Service:\n    def client(self):\n        def fetch():\n            return 1\n        return fetch\n",
            "use.py": "def f(client):\n    return client.fetch()\n",
        },
    )
    assert "qualified" not in {r[2] for r in edges_at(root, "use.py", "fetch")}


def test_rust_type_calls_prefer_the_type_in_scope(tmp_path):
    impl = "pub struct Backoff;\n\nimpl Backoff {\n    pub fn new() -> Self {\n        Backoff\n    }\n}\n"
    root = make_repo(
        tmp_path / "r",
        {
            "Cargo.toml": CARGO,
            "src/lib.rs": "pub mod a;\npub mod b;\n",
            "src/a.rs": impl,
            "src/b.rs": impl + "\npub fn go() {\n    let _b = Backoff::new();\n}\n",
        },
    )
    assert edges_at(root, "src/b.rs", "new") == [("src/b.rs", "Backoff.new", "qualified")]


def test_reexport_change_updates_class_calls(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "pkg/__init__.py": "from .models import Client\n",
            "pkg/models.py": "class Client:\n    def settings(self):\n        return 1\n",
            "pkg/other.py": "class Client:\n    def settings(self):\n        return 2\n",
            "use.py": "from pkg import Client\n\n\ndef f():\n    return Client.settings()\n",
        },
    )
    assert edges_at(root, "use.py", "settings") == [("pkg/models.py", "Client.settings", "qualified")]
    write(root, "pkg/__init__.py", "from .other import Client\n")
    assert edges_at(root, "use.py", "settings") == [("pkg/other.py", "Client.settings", "qualified")]
    assert snapshot(root, ALL) == fresh_snapshot(root, tmp_path, ALL)
```

- [ ] **Step 2: Run** them. Expected failures:
  - `test_qualified_calls_reach_only_the_bound_class`: 3 edges, one of them into the `.ts` file.
  - `test_unbound_class_calls_fall_back_to_name`: labelled `qualified`.
  - `test_lowercase_receivers_are_not_classes`: a `qualified` closure edge.
  - `test_rust_type_calls_prefer_the_type_in_scope`: 2 edges.
  - `test_reexport_change_updates_class_calls`: 2 edges before the change.

- [ ] **Step 3: Replace the `qualified` branch of `t1`**

```sql
  UNION ALL
    -- Class.method / Type::method, with the class bound in this file: defined here, imported by name
    -- (directly or through one re-export), or reached through an imported module (mod.Class.method)
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'qualified'
    FROM r JOIN sym s ON s.name = r.name AND s.family = r.family AND s.parent IS NOT NULL
         AND (s.parent = r.recv_last OR ends_with(s.parent, '.' || r.recv_last))
    WHERE r.receiver IS NOT NULL AND r.receiver NOT IN ('self', 'cls', 'this', 'Self')
      AND regexp_matches(r.recv_last, '^[A-Z]')
      AND (s.path = r.path
           OR EXISTS (SELECT 1 FROM imp i WHERE i.path = r.path AND i.target_path = s.path
                        AND i."local" IN (r.recv_last, r.recv_root))
           OR EXISTS (SELECT 1 FROM imp i JOIN rx ON rx.mod_path = i.target_path
                      WHERE i.path = r.path AND i."local" = r.recv_last
                        AND rx."local" = coalesce(nullif(i.name, 'default'), r.recv_last)
                        AND rx.target_path = s.path))
```

  In `index.sync_edges`, change the `bound` clause of the `_r` selection so it also takes refs whose receiver the binding names:

```sql
                -- refs bound through an import: by name (f()) or through their receiver (m.f(), Class.m())
                UNION SELECT r.rowid FROM refs r
                      SEMI JOIN (SELECT path, name FROM edges_dirty WHERE kind = 'bound') b
                      ON b.path = r.path
                         AND b.name IN (r.name, regexp_extract(r.receiver, '^[A-Za-z_$][A-Za-z0-9_$]*'),
                                        regexp_extract(r.receiver, '[A-Za-z_$][A-Za-z0-9_$]*$')))
```

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass, including `test_edges` (`make_engine → Engine.build` is same-file; `total → Circle.new` is imported).
- [ ] **Step 5: Commit** `fix: qualified edges only for a class bound in the file; marking follows bound receivers`

---

## Group C — freshness robustness

### Task C1: An interrupted rebuild is redone (FRESH-1, FRESH-2)

**Why:** `_rebuild_edges` deletes all edges and `edges_dirty`, then commits per batch; the >30% parse path commits per chunk. An interruption between commits leaves the call graph partial, and nothing records it. After an interrupted batch 2, claude-code kept 90,115 of 177,063 edges, and the next freshen reported `+0 ~0 -0`. An interrupted parse loses the deleted files and their old names, so edges from deleted files survive. **Required:**
- Before the first intermediate commit, write `meta('edges_rebuild_pending')`.
- Clear it in the final transaction.
- When it is present at the start of a freshen, force a full edge rebuild.

**Files:** `index.py` (new `_compute_edges`; `_rebuild_edges`, `sync_edges` call site, `freshen` transaction); create `tests/test_freshness.py`.

**Interfaces — Produces:** `index._compute_edges(con, source: str, where: str = "TRUE") -> None`.

- [ ] **Step 1: Write the failing tests**

```python
"""Freshness under interruption, logic changes and killed parents."""

import os
import subprocess
import sys
import time

import pytest
from duckgrep import index, schema
from helpers import fresh_snapshot, make_repo, rows, snapshot, write


def lib_repo(tmp_path, n=40):
    files = {"lib/__init__.py": "", "lib/core.py": "def helper():\n    return 1\n"}
    for i in range(n):
        files[f"m/f{i}.py"] = f"from lib.core import helper\n\n\ndef fn{i}():\n    return helper()\n"
    return make_repo(tmp_path / "repo", files)


def rename_helper(root, n_callers):
    write(root, "lib/core.py", "def helper2():\n    return 1\n")
    for i in range(n_callers):
        write(root, f"m/f{i}.py", f"from lib.core import helper2\n\n\ndef fn{i}():\n    return helper2()\n")


def test_interrupted_edge_rebuild_is_redone(tmp_path, monkeypatch):
    root = lib_repo(tmp_path)
    rows(root, "SELECT 1")
    rename_helper(root, 16)  # 17 of 42 files changed: the >30% path, which rebuilds every edge
    monkeypatch.setattr(index, "EDGE_BATCH_REFS", 20)  # several batches, each committed
    real, calls = index._compute_edges, []

    def fail_second_batch(con, source, where="TRUE"):
        calls.append(where)
        if len(calls) == 2:
            raise RuntimeError("killed")
        real(con, source, where)

    monkeypatch.setattr(index, "_compute_edges", fail_second_batch)
    with pytest.raises(RuntimeError, match="killed"):
        rows(root, "SELECT 1")
    monkeypatch.undo()
    assert snapshot(root) == fresh_snapshot(root, tmp_path)


def test_interrupted_parse_on_rebuild_path_is_redone(tmp_path, monkeypatch):
    root = lib_repo(tmp_path)
    rows(root, "SELECT 1")
    rename_helper(root, 10)
    for i in range(30, 34):  # the deletions push the change over 30%
        os.remove(os.path.join(root, f"m/f{i}.py"))
    monkeypatch.setattr(index, "CHUNK", 4)
    real, calls = index._work, []

    def fail_in_second_chunk(args):
        calls.append(args[1])
        if len(calls) == 6:
            raise RuntimeError("killed")
        return real(args)

    monkeypatch.setattr(index, "_work", fail_in_second_chunk)
    with pytest.raises(RuntimeError, match="killed"):
        rows(root, "SELECT 1")
    monkeypatch.undo()
    assert snapshot(root) == fresh_snapshot(root, tmp_path)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_freshness.py -v`. The first test is expected to fail with `AttributeError: _compute_edges` until Step 3. The second fails on the snapshot: edges from `m/f30..33.py` survive.
- [ ] **Step 3: Implement.** Add:

```python
def _compute_edges(con, source: str, where: str = "TRUE") -> None:
    con.execute(schema.EDGES_COMPUTE.format(source=source, where=where, cap=schema.NAME_CAP))
```

Use it in `_rebuild_edges` (`_compute_edges(con, "refs", where)`) and in `sync_edges` (`_compute_edges(con, "_r")`). In `freshen`:
- Read `meta` once: `meta = dict(con.execute("SELECT key, value FROM meta").fetchall())`, and take `extractor_version` from it.
- Set `rebuild_edges = full or "edges_rebuild_pending" in meta or len(replaced) > 0.3 * max(1, len(listed))`.
- After `BEGIN`: `if rebuild_edges: con.execute("INSERT OR REPLACE INTO meta VALUES ('edges_rebuild_pending', '1')")`.
- After `_rebuild_edges(con)`: `con.execute("DELETE FROM meta WHERE key = 'edges_rebuild_pending'")`.
- Replace the comment above the per-chunk `COMMIT`: `# Big rebuild: commit per chunk to bound memory. The pending marker makes the next freshen finish it.`

- [ ] **Step 4: Run** `uv run pytest -q`.
- [ ] **Step 5: Commit** `fix: an interrupted edge rebuild or rebuild-path parse is finished by the next freshen`

### Task C2: Upgrades that change stored rows or edges recompute them (FRESH-6)

**Why:** `extractor_version()` hashes only `extract.py`. So neither a change to `EDGES_COMPUTE` or `NAME_CAP` (exactly what Group B and the planned type inference do) nor a grammar upgrade recomputes an existing index, and the graph silently mixes two resolvers. **Required:**
- `extractor_version` covers `extract.py`, the tree-sitter runtime and grammar versions, and `schema.TABLES`; a change forces a full re-parse.
- A new `edges_version` covers `EDGES_COMPUTE` and `NAME_CAP`; a change forces an edge rebuild only.

**Files:** `index.py`, `tests/test_freshness.py`.

**Interfaces — Produces:** `index._grammar_versions() -> str`, `index.edges_version() -> str`, `meta('edges_version')`.

- [ ] **Step 1: Write the failing tests**

```python
def test_edge_logic_change_rebuilds_edges(tmp_path, monkeypatch):
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "class A:\n    def run(self):\n        pass\n",
            "b.py": "class B:\n    def run(self):\n        pass\n",
            "c.py": "def go(x):\n    x.run()\n",
        },
    )
    q = "SELECT resolution FROM edges WHERE src_path = 'c.py' AND name = 'run'"
    assert rows(root, q) == [("name",), ("name",)]
    monkeypatch.setattr(schema, "NAME_CAP", 1)  # 2 candidates is now over the cap
    assert rows(root, q) == [("ambiguous",)]


def test_grammar_upgrade_reparses(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "r", {"a.py": "def f():\n    pass\n"})
    rows(root, "SELECT 1")
    monkeypatch.setattr(index, "_grammar_versions", lambda: "tree-sitter-python==99")
    con = index.connect(root)
    try:
        st = index.freshen(con, root)
    finally:
        con.close()
    assert st.parsed == 1
```

- [ ] **Step 2: Run.** Expected failures: the first test gets `[('name',), ('name',)]` again; the second fails with `AttributeError: _grammar_versions`.
- [ ] **Step 3: Implement**

```python
GRAMMARS = ("tree-sitter", "tree-sitter-python", "tree-sitter-javascript", "tree-sitter-typescript",
            "tree-sitter-go", "tree-sitter-rust")


def _grammar_versions() -> str:
    """Installed tree-sitter runtime and grammar versions: an upgrade can change every parse."""
    from importlib import metadata

    out = []
    for name in GRAMMARS:
        try:
            out.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            out.append(f"{name}==?")
    return ";".join(out)


def extractor_version() -> str:
    """What stored rows depend on: extractor code, grammars and table layout. A change forces a full re-parse."""
    from . import extract as _e

    h = hashlib.blake2b(digest_size=8)
    with open(_e.__file__, "rb") as f:
        h.update(f.read())
    h.update(_grammar_versions().encode())
    h.update(schema.TABLES.encode())
    return h.hexdigest()


def edges_version() -> str:
    """What the call graph depends on beyond the rows: the resolution SQL and its cap. A change rebuilds edges."""
    return hashlib.blake2b(f"{schema.EDGES_COMPUTE}\0{schema.NAME_CAP}".encode(), digest_size=8).hexdigest()
```

In `freshen`, compute `ever = edges_version()`. Add `or meta.get("edges_version") != ever` to `rebuild_edges`, and record `('edges_version', ever)` next to the pending-marker delete.

- [ ] **Step 4: Run** `uv run pytest -q`.
- [ ] **Step 5: Commit** `fix: grammar, table and resolution-logic changes recompute an existing index`

### Task C3: Pool workers exit when their parent dies (F4, orphaned workers)

**Why:** a killed indexer, such as an MCP server whose client gave up during the initial build, left all 17 `ProcessPoolExecutor` workers running 60 s later. The same happened after SIGTERM. **Required:** workers poll their parent and exit within about 2 s of its death.

**Files:** `index.py` (`_pool`, `_exit_with_parent`; `freshen` uses `_pool`), `tests/test_freshness.py`.

**Interfaces — Produces:** `index._pool(workers: int | None) -> ProcessPoolExecutor`.

- [ ] **Step 1: Write the failing test**

```python
def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_pool_workers_exit_when_parent_dies(tmp_path):
    script = tmp_path / "parent.py"
    script.write_text(
        "import os, time\n"
        "from duckgrep import index\n"
        "ex = index._pool(2)\n"
        "futures = [ex.submit(time.sleep, 60) for _ in range(2)]\n"
        "time.sleep(1.5)\n"
        "print(' '.join(str(p) for p in ex._processes), flush=True)\n"
        "os._exit(0)\n"
    )
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60).stdout
    pids = [int(p) for p in out.split()]
    assert pids
    deadline = time.time() + 10
    while time.time() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.2)
    assert not any(_alive(p) for p in pids)
```

- [ ] **Step 2: Run.** Expected failure: `AttributeError: _pool` (and, with a plain pool, the workers stay alive).
- [ ] **Step 3: Implement**

```python
def _exit_with_parent(parent: int) -> None:
    """Pool worker initializer: exit when the indexing process dies, so a killed server leaves no workers."""

    def watch():
        while os.getppid() == parent:
            time.sleep(1)
        os._exit(1)

    threading.Thread(target=watch, daemon=True).start()


def _pool(workers: int | None) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(max_workers=workers, initializer=_exit_with_parent, initargs=(os.getpid(),))
```

Add `import threading`. In `freshen`, replace `ProcessPoolExecutor(max_workers=workers)` with `_pool(workers)`.

- [ ] **Step 4: Run** `uv run pytest -q`.
- [ ] **Step 5: Commit** `fix: indexing pool workers exit when their parent process dies`

---

## Group D — the agent-facing surface

### Task D1: Oversized results stop at `max_rows` (F3)

**Why:** `run()` counts the overflow with `len(cur.fetchall())`, which loads every remaining row as Python tuples, outside DuckDB's memory limit. One agent query without `LIMIT` hit 14 GB RSS and a timeout. The same query returns its first 101 rows in 0.01 s. **Required:**
- Fetch `max_rows + 1` rows and report "there are more".
- `Result.more: bool` replaces the overflow count; `total` is the number of rows returned.
- `max_rows < 1` is treated as 1.

**Files:** `query.py`, `tests/test_surface.py`.

- [ ] **Step 1: Write the failing test**

```python
"""The agent-facing surface: result limits, verbatim text, macro disambiguation, MCP startup, roots."""

import os
import subprocess
import time
import tracemalloc

import anyio
from duckgrep import cli, mcp_server
from duckgrep import query as q
from helpers import make_repo, rows
from mcp.shared.memory import create_connected_server_and_client_session


def test_oversized_results_stop_at_max_rows(repo):
    tracemalloc.start()
    t = time.perf_counter()
    res = q.run(repo, "SELECT * FROM range(5000000)", max_rows=10)
    elapsed = time.perf_counter() - t
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(res.rows) == 10 and res.more
    assert "there are more" in q.format_tsv(res)
    assert peak < 50_000_000 and elapsed < 5
```

- [ ] **Step 2: Run** `uv run pytest tests/test_surface.py -v`. Expected failure: `AttributeError: 'Result' object has no attribute 'more'`, or peak memory far above 50 MB.
- [ ] **Step 3: Implement.** Add a `more: bool = False` field to `Result`. In `run()`:

```python
        max_rows = max(1, max_rows)
        rows = cur.fetchmany(max_rows + 1)
        more = len(rows) > max_rows
        rows = rows[:max_rows]
        return Result(cols, rows, len(rows), note, more)
```

In `format_tsv`, replace the `if res.total > len(res.rows):` branch with:

```python
    if res.more:
        out.append(f"# showing the first {len(res.rows)} rows; there are more. Add LIMIT/WHERE or raise max_rows")
```

In `format_table`, replace its `if res.total > len(res.rows):` branch with `if res.more: lines.append(f"# showing the first {len(res.rows)} rows; there are more")`. In `format_json`, add `"more": res.more` to the dict.

- [ ] **Step 4: Run** `uv run pytest -q`. The bench scripts use `res.total`, which is still the number of rows returned.
- [ ] **Step 5: Commit** `fix: stop at max_rows instead of loading the rest of a result to count it`

### Task D2: Cell text is verbatim (F8)

**Why:** `_cell` doubles backslashes and turns tabs into `\t`. So `source()` and `grep()` text differs from the file: `requests/utils.py:495` comes back with 8 backslashes instead of 4, and every Go line shows a literal `\t`. An agent pasting that into an edit fails to match. **Required:**
- Cells are verbatim, except that newlines are shown as `⏎` and cells over 300 characters are cut with `…`.
- `QUERY_DOC` says so.
- The CLI's table view expands tabs for alignment.

**Files:** `query.py`, `mcp_server.py` (`QUERY_DOC`), `tests/test_surface.py`.

- [ ] **Step 1: Write the failing test**

```python
def test_cells_keep_code_text_verbatim(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a.go": "package a\n\nfunc F() {\n\tif x := 1; x > 0 {\n\t\treturn\n\t}\n}\n",
            "b.py": 'PAT = r"\\d+\\\\"\n',
        },
    )
    out = q.format_tsv(q.run(root, "SELECT text FROM lines WHERE path IN ('a.go', 'b.py') ORDER BY path, line"))
    assert "\tif x := 1; x > 0 {" in out
    assert 'PAT = r"\\d+\\\\"' in out
```

- [ ] **Step 2: Run.** Expected failure: the output holds `\\tif` and doubled backslashes.
- [ ] **Step 3: Implement**

```python
def _cell(v) -> str:
    """The value as stored, so code can be pasted into an edit; only newlines (⏎) and long cells (…) change."""
    if v is None:
        return ""
    s = v if isinstance(v, str) else str(v)
    s = s.replace("\r\n", "⏎").replace("\n", "⏎").replace("\r", "⏎")
    return s if len(s) <= MAX_CELL else s[: MAX_CELL - 1] + "…"
```

In `format_table`, use `_cell(v).expandtabs(4)`. In `mcp_server.QUERY_DOC`, replace `Results are tab-separated.` with: `Results are tab-separated; cell text is verbatim (tabs and backslashes included) except newlines, shown as ⏎, and cells over 300 characters, cut with ….`

- [ ] **Step 4: Run** `uv run pytest -q`.
- [ ] **Step 5: Commit** `fix: return code text verbatim; document the only two cell changes`

### Task D3: Macros say which file or symbol a row comes from (F2)

**Why:** `outline`, `callees` and `source` merge or pick among same-named files and symbols without saying so:
- `outline('index.ts')` in claude-code interleaves 485 rows from 74 files.
- `callees('checkPathConstraints')` mixes the Bash and PowerShell copies.
- `source('__init__')` shows 1 of 118 definitions without a path.
- The transitive-callers recipe joins on the qualname only.

**Required:**
- `outline(p)`: an exact path wins; a leading `file` column is filled only when several files match.
- `callees(q)`: an exact qualname wins; leading `file` and `caller` columns are filled only when several functions match.
- `source(q)`: a leading `file` column that, when several definitions match, holds `path (1 of N matches)` on the first line only.
- The recipes in `SCHEMA_DOC` and the README key on `(path, qualname)`.

**Files:** `schema.py` (`VIEWS` macros; `SCHEMA_DOC` macros and examples), `README.md`, `tests/test_surface.py`.

- [ ] **Step 1: Write the failing tests**

```python
TWIN = {
    "a/index.ts": "export function one() { return two(); }\nexport function two() { return 1; }\n",
    "b/index.ts": "export function three() { return 3; }\n",
    "a/check.py": "def check():\n    return helper_a()\n\n\ndef helper_a():\n    return 1\n",
    "b/check.py": "def check():\n    return helper_b()\n\n\ndef helper_b():\n    return 2\n",
}


def test_outline_names_the_file_when_ambiguous(tmp_path):
    root = make_repo(tmp_path / "r", TWIN)
    got = rows(root, "SELECT file, qualname FROM outline('index.ts')")
    assert got == [("a/index.ts", "one"), ("a/index.ts", "two"), ("b/index.ts", "three")]
    assert rows(root, "SELECT file, qualname FROM outline('a/index.ts')") == [(None, "one"), (None, "two")]


def test_callees_name_file_and_caller_when_ambiguous(tmp_path):
    root = make_repo(tmp_path / "r", TWIN)
    got = rows(root, "SELECT file, caller, name FROM callees('check')")
    assert got == [("a/check.py", "check", "helper_a"), ("b/check.py", "check", "helper_b")]


def test_source_says_which_match_it_shows(tmp_path):
    root = make_repo(tmp_path / "r", TWIN)
    got = rows(root, "SELECT file, line, text FROM source('check')")
    assert got[0] == ("a/check.py (1 of 2 matches)", 1, "def check():")
    assert all(r[0] is None for r in got[1:])
```

- [ ] **Step 2: Run.** Expected failure: `BinderException ... "file" not found` for all three.
- [ ] **Step 3: Replace the three macros in `VIEWS`**

```sql
-- symbols in a file; `file` is filled only when the argument matches more than one file
CREATE OR REPLACE MACRO outline(p) AS TABLE
    WITH m AS (
        SELECT * FROM symbols WHERE path = p
        UNION ALL
        SELECT * FROM symbols WHERE ends_with(path, '/' || p) AND NOT EXISTS (SELECT 1 FROM files WHERE path = p)
    ), k AS (SELECT count(DISTINCT path) AS n FROM m)
    SELECT CASE WHEN k.n > 1 THEN m.path END AS file, start_line, end_line, kind, qualname, signature
    FROM m, k
    ORDER BY m.path, start_line;

-- what a function references; `file` and `caller` are filled only when the name matches more than one function
CREATE OR REPLACE MACRO callees(q) AS TABLE
    WITH m AS (
        SELECT * FROM edges WHERE src_scope = q
        UNION ALL
        SELECT * FROM edges WHERE ends_with(src_scope, '.' || q) AND NOT EXISTS (SELECT 1 FROM edges WHERE src_scope = q)
    ), k AS (SELECT count(DISTINCT (src_path, src_scope)) AS n FROM m)
    SELECT CASE WHEN k.n > 1 THEN m.src_path END AS file, CASE WHEN k.n > 1 THEN m.src_scope END AS caller,
           line, ref_kind, name, receiver, dst_path, dst_qualname, resolution, n_candidates
    FROM m, k
    ORDER BY m.src_path, m.src_scope, line, col;

-- the code of a symbol (qualname or name); with several matches, the first row's `file` names the one shown
CREATE OR REPLACE MACRO source(q) AS TABLE
    WITH c AS (SELECT * FROM symbols WHERE qualname = q OR name = q),
         s AS (SELECT * FROM c ORDER BY (qualname = q) DESC, path, start_line LIMIT 1),
         k AS (SELECT count(*) AS n FROM c)
    SELECT CASE WHEN k.n > 1 AND l.line = s.start_line THEN s.path || ' (1 of ' || k.n || ' matches)' END AS file,
           l.line, l.text
    FROM s JOIN lines l ON l.path = s.path AND l.line BETWEEN s.start_line AND s.end_line, k
    ORDER BY l.line;
```

  In `SCHEMA_DOC`, under `TABLE MACROS`, add the line:
  `  outline, callees and source fill their first column(s) only when the argument matches several files or symbols`.
  Replace the transitive-callers example with:

```
  -- transitive callers (2 hops), keyed on (path, qualname) so same-named functions elsewhere don't join
  WITH RECURSIVE up(p, q, d) AS (SELECT path, qualname, 0 FROM symbols WHERE qualname = 'Session.send' UNION
    SELECT e.src_path, e.src_scope, d+1 FROM edges e JOIN up ON e.dst_path = up.p AND e.dst_qualname = up.q
    WHERE d < 2 AND e.resolution <> 'name' AND e.src_scope <> '')
  SELECT * FROM up;
```

  Make the same change to the 3-hop example at the top of `README.md`.

- [ ] **Step 4: Run** `uv run pytest -q`. `test_macros` still passes: `outline('helpers.py')` is unambiguous, and `source('Engine.step')` is exact. Also run every `SCHEMA_DOC` example against the fixture with `uv run duckgrep -C tests/fixture q "<example>"` and confirm none errors.
- [ ] **Step 5: Commit** `fix: outline, callees and source say which file or symbol they show; recipes key on path`

### Task D4: The MCP server answers before the index is built (F4)

**Why:** `serve()` refreshes the index before it answers `initialize`. On vscode the handshake took 42–52 s, and a client that gives up restarts the build from scratch on every launch. **Required:**
- `build()` starts the initial refresh on a background thread.
- The query tool is async and waits for that refresh in a worker thread, so the event loop stays free.
- `serve()` calls `run()` immediately.

**Files:** `mcp_server.py`, `tests/test_surface.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_serve_starts_before_the_index_is_built(repo, monkeypatch):
    from mcp.server.fastmcp import FastMCP

    started = []
    monkeypatch.setattr(q, "refresh", lambda root, **kw: time.sleep(2) or "")
    monkeypatch.setattr(FastMCP, "run", lambda self, *a, **k: started.append(time.perf_counter()))
    t = time.perf_counter()
    mcp_server.serve(repo)
    assert started and started[0] - t < 1.0


def test_first_query_waits_for_the_initial_index(repo):
    async def main():
        server = mcp_server.build(repo)
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            res = await client.call_tool("query", {"sql": "SELECT count(*) AS n FROM files"})
        return res.content[0].text

    assert int(anyio.run(main).splitlines()[-1]) > 0
```

- [ ] **Step 2: Run.** The first test is expected to fail, starting after about 2 s. The second passes and pins that a query answers correctly after waiting.
- [ ] **Step 3: Implement**

```python
def build(root: str) -> FastMCP:
    """The server. The index is built on a background thread while the handshake is answered; the first
    query waits for it."""
    mcp = FastMCP("duckgrep")
    ready = threading.Event()

    def warm():
        try:
            q.refresh(root)
        finally:
            ready.set()

    threading.Thread(target=warm, name="duckgrep-index", daemon=True).start()

    def answer(sql: str, max_rows: int) -> str:
        ready.wait()
        try:
            return q.format_tsv(q.run(root, sql, max_rows=max_rows))
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"

    @mcp.tool(description=QUERY_DOC)
    async def query(sql: str, max_rows: int = 100) -> str:
        return await anyio.to_thread.run_sync(answer, sql, max_rows)

    return mcp


def serve(root: str) -> None:
    build(root).run()
```

Add `import threading` and `import anyio`. The `main()` changes in Task D5.

- [ ] **Step 4: Run** `uv run pytest -q`.
- [ ] **Step 5: Commit** `fix: answer the MCP handshake at once and build the index in the background`

### Task D5: Roots are explicit or found, never guessed (F5, F7, F12)

**Why:**
- `duckgrep mcp`, the README's registration, computes the root from the cwd, so `DUCKGREP_ROOT` is ignored and the agent silently queries another repo.
- With no `.git` or `.duckgrep` above the cwd, the server indexes whatever directory it starts in, dotfiles like `.env.local` included.
- A mistyped `-C` creates an empty index that answers every query with 0 rows.
- `-C` is documented as "repo root" but is only a starting point for an upward search.

**Required:**
- `-C DIR` is the root, used as given, and must exist.
- `duckgrep mcp` takes `-C`, then `$DUCKGREP_ROOT`, then the nearest `.git`/`.duckgrep` above the cwd.
- When no root is found, exit 2 with a message.
- The walk fallback skips dotfiles.
- `duckgrep-mcp` runs the same code as `duckgrep mcp`.

**Files:** `cli.py`, `mcp_server.py` (`main`), `index.py` (`find_root`, `list_files`), `bench/mcp_smoke.py`, `README.md`, `tests/test_surface.py`.

**Interfaces — Produces:**
- `index.find_root(start=None) -> str | None`.
- `cli.resolve_root(explicit, env=None) -> str`, which raises `cli.RootError`.

- [ ] **Step 1: Write the failing tests**

```python
def git_repo(path, files):
    root = make_repo(path, files)
    subprocess.run(["git", "init", "-q", root], check=True)
    return root


def test_explicit_root_is_used_as_given(tmp_path, capsys):
    outer = git_repo(tmp_path / "outer", {"sub/a.py": "def f():\n    pass\n", "b.py": "x = 1\n"})
    code = cli.main(["-C", os.path.join(outer, "sub"), "q", "SELECT count(*) AS n FROM files", "-f", "tsv"])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "1"


def test_missing_root_is_an_error(tmp_path, capsys):
    assert cli.main(["-C", str(tmp_path / "typo"), "q", "SELECT 1"]) == 2
    assert "no such directory" in capsys.readouterr().err
    assert not (tmp_path / "typo").exists()


def test_no_repository_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["q", "SELECT 1"]) == 2
    assert "not inside a repository" in capsys.readouterr().err


def test_mcp_root_comes_from_env(tmp_path, monkeypatch):
    target = git_repo(tmp_path / "target", {"a.py": "x = 1\n"})
    elsewhere = git_repo(tmp_path / "elsewhere", {"b.py": "y = 2\n"})
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("DUCKGREP_ROOT", target)
    seen = []
    monkeypatch.setattr(mcp_server, "serve", seen.append)
    assert cli.main(["mcp"]) == 0
    assert seen == [target]


def test_walk_fallback_skips_dotfiles(tmp_path):
    root = make_repo(tmp_path / "plain", {"a.py": "x = 1\n", ".env": "API_KEY=secret\n"})
    assert cli.main(["-C", root, "index"]) == 0
    assert rows(root, "SELECT path FROM files") == [("a.py",)]
```

- [ ] **Step 2: Run.** Expected failures:
  - explicit root: the count is `2` because the outer repo was indexed;
  - missing root: exit code `0`;
  - no repository: exit code `0`;
  - env: the elsewhere repo was indexed;
  - dotfiles: `.env` is listed.

- [ ] **Step 3: Implement.** In `index.py`:

```python
def find_root(start: str | None = None) -> str | None:
    """Nearest directory at or above `start` (default: the cwd) holding .duckgrep or .git; None if there is none."""
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(cur, DB_DIR)) or os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
```

  In the `os.walk` fallback of `list_files`, skip dotfiles: `if fn.startswith("."): continue`.

  In `cli.py`:

```python
class RootError(Exception):
    pass


def resolve_root(explicit: str | None, env: str | None = None) -> str:
    """-C DIR as given, else $DUCKGREP_ROOT (MCP server only), else the nearest .git/.duckgrep above the cwd."""
    for given in (explicit, env):
        if given:
            path = os.path.abspath(given)
            if not os.path.isdir(path):
                raise RootError(f"no such directory: {given}")
            return path
    found = find_root()
    if found is None:
        raise RootError(f"not inside a repository (no .git or .duckgrep above {os.getcwd()}); use -C DIR")
    return found
```

  In `main`:
  - Change the `-C` help to `"repo root, used as given (default: nearest .git / .duckgrep above the cwd)"`.
  - Handle `schema` before resolving the root.
  - Then:

```python
    try:
        root = resolve_root(a.root, os.environ.get("DUCKGREP_ROOT") if a.cmd == "mcp" else None)
    except RootError as e:
        print(f"duckgrep: {e}", file=sys.stderr)
        return 2
```

  Add `import os`. In `mcp_server.py`, replace `main()` with `sys.exit(cli.main(["mcp"]))`, importing `cli` inside the function to avoid a cycle. `serve(root)` now takes a required root.

  In `README.md`, under Claude Code, write: "The server's root is `-C DIR`, else `$DUCKGREP_ROOT`, else the nearest directory above its working directory with `.git` or `.duckgrep`. Outside a repository it refuses to start." In the CLI section, write: "`-C DIR` sets the repo root, used as given."

- [ ] **Step 4: Run** `uv run pytest -q`, then `uv run python bench/mcp_smoke.py`. It now indexes `tests/fixture` alone, so the paths print as `pkg/core.py`.
- [ ] **Step 5: Commit** `fix: -C and DUCKGREP_ROOT are the root as given; no repository means an error, not $HOME`

---

## Integration (coordinator)

1. Merge the group branches in the order B, D, A, C: `git merge --no-ff <branch>`, then `uv run pytest -q` after each merge. Expected conflicts:
   - **`index.py` `freshen` (A3 against C1/C2):** keep A's context and candidate code before `BEGIN`, and C's `meta` read, version checks, pending marker, `_compute_edges` and `_pool`. Both read `meta`: compute `ctx` from A's `_module_context`, and `rebuild_edges` from C's rule.
   - **`schema.py` `SCHEMA_DOC`:** B's `edges(...)` block against D's `TABLE MACROS`/`EXAMPLES` parts. Keep both.
   - **`index.py` `sync_edges`:** B's `_r` selection against C's `_compute_edges(con, "_r")`. Keep both.
2. Run `uv run pre-commit run --all-files`. It must be green.
3. Update `CLAUDE.md`:
   - The resolution bullet: star-import tier; bare names never match by name; builtin names are ambiguous; qualified needs a bound class.
   - The gotchas: resolution changes now rebuild automatically through `edges_version`.
   - The roots rule.

   Update the README's resolution section to match.
4. Verification workflow: one agent per area, high effort.
   - Re-run each targeted finding's audit repro on fresh corpus clones (kmm, agentcairn, claude-code, django, requests, gin). Each must now show the required behaviour.
   - Run `bench/accuracy.py` on freqtrade and django with n = 1000. Confident precision must stay at 100%; report the false name edges on jedi-external calls before and after.
   - Run `bench/latency.py` on django. Queries and edge sync must not regress more than 20%.
   - Run the audit's freshness fuzzer (seeds 200–299, without go.mod edits). There must be no new divergence classes.
   - One independent reviewer checks the integrated diff.
