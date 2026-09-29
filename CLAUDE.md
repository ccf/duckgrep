# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

duckgrep parses a repo with tree-sitter (Python, TS/TSX/JS, Go, Rust) into one DuckDB file, `<root>/.duckgrep/index.duckdb`, and gives coding agents one read-only `query(sql)` MCP tool whose description carries the schema (`SCHEMA_DOC`). The bet: structural questions (who calls this, what does it call, what breaks if I change it) take one SQL call instead of grep → read → grep chains. It has to beat plain grep on tool calls, tokens and turns-to-locate on real tasks, measured, or the structure isn't earning its keep. It is Python on purpose: the goal is proving that, and the heavy lifting is already native (tree-sitter, DuckDB).

README "Known gaps / next" is the roadmap; `bench/RESULTS.md` holds the measured numbers, with the machine, versions and commits behind them.

## Commands

```bash
uv sync                                    # .venv with dev tools (pytest, ruff, pre-commit)
uv run pre-commit install                  # once per clone: ruff check --fix, ruff format, pytest on commit
uv run pytest                              # whole suite, a few seconds
uv run pytest tests/test_duckgrep.py::test_reexports   # one test
uv run pytest -k test_edges                # the parametrized resolution cases
uv run pre-commit run --all-files

uv run duckgrep -C <repo> index [--full]   # build or update <repo>/.duckgrep/index.duckdb
uv run duckgrep -C <repo> q "SELECT ..."   # shortcuts: def, callers, callees, outline, grep, source <arg>
uv run duckgrep schema                     # the schema text the agent sees

uv run python bench/latency.py <repo> <file-to-edit> <symbol> <qualname>
uv run python bench/vs_grep.py <django-checkout>
uv run --group bench python bench/accuracy.py <python-repo> [n]   # jedi as the reference
uv run python bench/mcp_smoke.py [repo]    # drives the MCP server over stdio
```

## Workflow

Every change lands through a pull request:
1. Branch from an up-to-date `main`.
2. Commit through the hook.
3. Push the branch and open a PR with `gh pr create`.

Bugbot and Greptile review every PR. Check each comment against the code before acting: fix the valid ones on the branch, and reply with evidence to the rest. Never commit on, merge into or push `main`; a pre-commit hook refuses commits on it. The user merges PRs.

## How it fits together

- `extract.py` is per file and pure: (path, bytes, small repo context) → symbols, refs, imports, module keys. `index.py` decides which files to re-extract and swaps their rows. `schema.py` holds the SQL: tables, `EDGES_COMPUTE` (reference resolution), views, table macros and `SCHEMA_DOC`. `builtin_names.py` lists the builtin method and global names that `EDGES_COMPUTE` inlines. `query.py` refreshes, then runs the agent's SQL. `mcp_server.py` and `cli.py` are thin wrappers over it.
- Freshness is per query, not hook-based. Every `query.run()` first calls `freshen()`: one `git ls-files`, a stat per file, and a blake2b hash only when size or mtime changed. A changed file's rows in `files`, `symbols`, `refs`, `imports`, `modules` and `lines` are deleted and re-inserted in one transaction. The MCP server builds the index on a background thread at startup, and the first query waits for it.
- `edges` (reference → definition, the call graph) is the only table that depends on other files, and it is maintained lazily. `freshen()` records in `edges_dirty` which refs a change can affect:
  - `path`: every ref in a changed file, in a file importing a module key that appeared or disappeared, or in a star-importer of a changed module;
  - `name`: refs named like anything the changed files define or import, before and after;
  - `bound`: refs whose name or receiver is bound by an import of a changed file's module keys.

  `sync_edges()` recomputes just those, and only when the SQL mentions `edges`, `callers` or `callees` (`query.needs_edges`). When more than 30% of files change, edges are rebuilt from scratch in batches. `meta('edges_rebuild_pending')` makes the next freshen finish a rebuild that was interrupted.
- Invariant: incremental edges must equal a full rebuild. Tests assert `snapshot(root) == fresh_snapshot(root, tmp_path)` (from `tests/helpers.py`) after edits. A change to resolution or dirty-marking needs such a case. The known remaining gap is aliased re-export chains (`from pkg import X as Y` through a re-exporting `__init__`, TS barrels).
- Resolution tiers in `EDGES_COMPUTE`:
  - **Confident:** `self`, `local`, `package` (Go), `import` (including `from m import *` / `use m::*`, Python and Rust only), `module` and `qualified`, plus one hop of re-exports. `qualified` (`Class.m`, `Type::m`) needs the same language, a capitalised receiver, and a class defined or imported in the calling file.
  - **`name`:** receiver calls of unknown type, at most `NAME_CAP` = 10 candidates, one row each.
  - **`ambiguous`:** more candidates, or a method name builtin types also have; the target is NULL.
  - **`unresolved`:** no in-repo target, including calls on receivers bound to external imports or builtin globals.

  A bare name never matches another file by name alone; Rust macros and `.d.ts` declarations are the exceptions. When two confident tiers reach one target, the order above decides the label. `EDGES_COMPUTE` must stay consistent with the `imports_resolved` view.
- Imports join through module keys. `module_keys()` lists every key a file can be imported under:
  - Python: dotted suffixes.
  - JS: the path without extension, plus the directory for `index.*`.
  - Go: the directory.
  - Rust: `<crate>::<module path>`, where a crate is a Cargo package's import name. Files under `tests/`, `examples/`, `benches/` and `src/bin/` belong to their own crates.

  `_imports_<lang>()` normalises each import into the same key space: relative imports, the `go.mod` module prefix, Rust `crate`/`self`/`super`, other crates' names, uniform paths and inline `mod` blocks. That context (go.mod module paths, Cargo crate names) is stored in `meta('module_ctx')`; when it changes, every file of that language is re-parsed.
- The extractor is table-driven: each language is a `Spec` of node types (definitions, calls, member access) and of which parent/field puts an identifier into a context such as `write`, `type` or `import`. Language quirks live in `_def_targets`, `_imports_<lang>` and `module_keys`, not in the walker.

## Gotchas

- Indexing writes `.duckgrep/` into the root it indexes (self-gitignored). To try duckgrep on another repo, clone that repo into a scratch directory first.
- The root is `-C DIR` as given, else `$DUCKGREP_ROOT` (for `duckgrep mcp` only), else the nearest `.git`/`.duckgrep` above the cwd. With no root the CLI and the server exit with an error; they never index the cwd by guess.
- Stored data is versioned, so upgrades need no manual step:
  - `extractor_version()` (extract.py, the grammar versions, `TABLES`) forces a full re-parse when it changes.
  - `edges_version()` (`EDGES_COMPUTE`, `VIEWS`, `NAME_CAP`) forces an edge rebuild.
  - Bump `SCHEMA_VERSION` only for incompatible table changes; a mismatch deletes and rebuilds the db.
- `SCHEMA_DOC` ships in the MCP tool description, so an agent pays for every token of it in every session. Keep it accurate and compact when tables or macros change.
- Git hooks export `GIT_DIR`/`GIT_INDEX_FILE`, and they must never leak:
  - A conftest autouse fixture strips `GIT_*` for every test.
  - duckgrep's own git calls use `_git_env()`.

  Without these, a test's `git init` inside a worktree's commit hook re-initialised the shared repo as bare. Commit through the hook; never `--no-verify`.
- `tests/fixture/` is test data: tests assert exact line numbers in it, so ruff and the whitespace hooks skip it. Tests copy it into `tmp_path` before indexing. New tests build their repos with `helpers.make_repo`.
- Agent SQL runs on a read-only connection with `enable_external_access = false` and a 30 s timeout. Results stop at `max_rows` ("there are more"), and cell text is verbatim apart from newlines (⏎) and cells over 300 characters (…). The MCP tool returns errors as text rather than raising.
