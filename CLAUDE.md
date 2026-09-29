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

## How it fits together

- `extract.py` is per file and pure: (path, bytes, small repo context) → symbols, refs, imports, module keys. `index.py` decides which files to re-extract and swaps their rows. `schema.py` holds the SQL: tables, `EDGES_COMPUTE` (reference resolution), views, table macros and `SCHEMA_DOC`. `query.py` refreshes, then runs the agent's SQL; `mcp_server.py` and `cli.py` are thin wrappers over it.
- Freshness is per query, not hook-based. Every `query.run()` first calls `freshen()`: one `git ls-files`, a stat per file, and a blake2b hash only when size or mtime changed. A changed file's rows in `files`, `symbols`, `refs`, `imports`, `modules` and `lines` are deleted and re-inserted in one transaction.
- `edges` (reference → definition, the call graph) is the only table that depends on other files, and it is maintained lazily. `freshen()` records in `edges_dirty` which refs a change can affect: `path` (every ref in a changed file, or in a file importing a module key that appeared or disappeared), `name` (refs named like anything the changed files define or import, before and after), `bound` (refs bound by imports of a changed file's module keys). `sync_edges()` recomputes just those, and only when the SQL mentions `edges`, `callers` or `callees` (`query.needs_edges`). When more than 30% of files change, edges are rebuilt from scratch in batches.
- Invariant: incremental edges must equal a full rebuild. `test_incremental_edges_match_full_rebuild` and `test_reexports` compare against a fresh index of a copy of the tree; a change to resolution or dirty-marking needs a case there.
- Resolution tiers in `EDGES_COMPUTE`: confident = `self`, `local`, `package` (Go), `import`, `module`, `qualified`, plus one hop of re-exports; otherwise `name` (≤ `NAME_CAP` = 10 same-named candidates, one row each), `ambiguous` (more, target NULL) or `unresolved` (calls with no in-repo candidate). `EDGES_COMPUTE` must stay consistent with the `imports_resolved` view.
- Imports join through module keys. `module_keys()` lists every key a file can be imported under (Python dotted suffixes, JS path without extension plus the directory for `index.*`, Go directory, Rust `crate::` path); `_imports_<lang>()` normalises each import into the same key space (relative imports, the `go.mod` module prefix, Rust `self`/`super`).
- The extractor is table-driven: each language is a `Spec` of node types (definitions, calls, member access) and of which parent/field puts an identifier into a context such as `write`, `type` or `import`. Language quirks live in `_def_targets`, `_imports_<lang>` and `module_keys`, not in the walker.

## Gotchas

- Indexing writes `.duckgrep/` into the root it indexes (self-gitignored). To try duckgrep on another repo, clone that repo into a scratch directory first.
- Only a change to `extract.py` forces a re-parse (`extractor_version()` hashes that file). After changing `EDGES_COMPUTE` or other SQL in `schema.py`, existing indexes keep stale edges: run `duckgrep index --full`, or bump `SCHEMA_VERSION` when the tables change (a mismatch deletes and rebuilds the db).
- `SCHEMA_DOC` ships in the MCP tool description, so an agent pays for every token of it in every session. Keep it accurate and compact when tables or macros change.
- `tests/fixture/` is test data: tests assert exact line numbers in it, so ruff and the whitespace hooks skip it. Tests copy it into `tmp_path` before indexing.
- Agent SQL runs on a read-only connection with `enable_external_access = false` and a 30 s timeout. The MCP tool returns errors as text rather than raising.
