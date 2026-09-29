# duckgrep

A DuckDB index of your codebase for coding agents. It holds symbols, references, imports, a call graph, full text and git history, all queryable with SQL.

Agents locate code with chains of `grep` → read file → `grep` again. That works for "where is this string" but is slow and token-hungry for structural questions: *who calls this, what does it call, what breaks if I change it, what's dead*. duckgrep parses the repo with tree-sitter into a single DuckDB file and exposes it as **one read-only `query(sql)` MCP tool** whose description carries the whole schema. The agent can write a correct query on its first call.

```sql
SELECT * FROM callers('Session.request') WHERE resolution <> 'name';

-- everything that reaches a function within 3 hops
WITH RECURSIVE up(p, q, d) AS (
  SELECT path, qualname, 0 FROM symbols WHERE qualname = 'SQLCompiler.execute_sql'
  UNION SELECT e.src_path, e.src_scope, d + 1 FROM edges e JOIN up ON e.dst_path = up.p AND e.dst_qualname = up.q
  WHERE d < 3 AND e.resolution NOT IN ('name', 'ambiguous', 'unresolved') AND e.src_scope <> '')
SELECT p, q, min(d) FROM up GROUP BY p, q;

-- hottest files by churn that define classes
SELECT * FROM file_churn WHERE path IN (SELECT path FROM symbols WHERE kind = 'class')
ORDER BY n_commits DESC LIMIT 10;
```

Languages: Python, TypeScript/TSX, JavaScript, Go, Rust. Every other text file is still indexed for `grep()`.

## Install

```bash
uv tool install git+ssh://git@github.com/ccf/duckgrep
```

### Claude Code

```bash
claude mcp add duckgrep -- duckgrep mcp
```

The server's root is `-C DIR`, else `$DUCKGREP_ROOT`, else the nearest directory above its working directory with `.git` or `.duckgrep`. Outside a repository it refuses to start. The first call builds the index. After that, every call runs an incremental refresh, so results reflect the agent's latest edits with no hooks.

## CLI

`-C DIR` sets the repo root, used as given.

```bash
duckgrep index                      # build / update (.duckgrep/index.duckdb, self-gitignored)
duckgrep def Session                # where defined
duckgrep callers Session.request    # who references it, with the calling function
duckgrep callees Session.request    # what it references
duckgrep outline sessions.py        # symbols in a file
duckgrep grep '(?i)retry'           # regex + enclosing symbol
duckgrep source Session.request     # the code
duckgrep q "SELECT ..."             # anything
duckgrep schema                     # what the agent sees
```

## Schema

| table | what |
|---|---|
| `files` | path, lang, size, sha, n_lines, skipped, parse_errors |
| `symbols` | name, qualname (`Class.method`, `GoType.Method`), kind, parent, lines, signature, first doc line, exported |
| `refs` | every identifier use: kind (`call`/`attr`/`write`/`type`/`inherit`/`decorator`/`import`/...), receiver text, enclosing scope |
| `imports` / `imports_resolved` | as written, plus the repo file each resolves to |
| `edges` | reference → definition (the call graph), with a `resolution` tier |
| `lines` | full text, for `grep()` |
| `commits`, `file_changes`, `file_churn` | git history |

Table macros: `defs`, `callers`, `callees`, `outline`, `grep`, `source`.

### Resolution tiers

Tree-sitter gives syntax, not types, so every edge says how it was resolved:

- **Confident:** `self`, `local`, `package` (Go), `import`, `module`, `qualified`. These come from scope and import analysis, including aliases, relative imports, `go.mod` prefixes, and one hop of re-exports (`__init__.py`, `export … from`).
- **`name`:** name match only. Kept as one row per candidate when there are ≤10 candidates.
- **`ambiguous`:** >10 same-named definitions (`obj.get()`, `obj.save()`); the target is left NULL rather than guessed.
- **`unresolved`:** calls to things not defined in the repo (stdlib, third-party).

## How it stays fresh

`freshen()` runs before every query: one `git ls-files`, a stat per file, then a content hash only for files whose size or mtime changed. Changed files have their rows swapped in one transaction. Edges that depend on other files (same name elsewhere, imports that move when a file appears or disappears) are marked dirty. They are recomputed lazily, and only when a query touches `edges`/`callers`/`callees`. A test checks that the incremental edges equal a full rebuild after renames, shadowing modules and deletions. Any change to the extractor code triggers a full re-parse.

## Results so far

Full numbers are in [bench/RESULTS.md](bench/RESULTS.md). Headlines:

- **Latency (django, 7k files):** a no-op refresh takes ~140 ms, one edited file ~330 ms (+~0.8 s call-graph sync when the query needs it), and typical queries 30–90 ms. On vscode (19.5k files, 6.5M refs) typical queries take 80–370 ms. The full initial index of vscode takes ~2.5 min on 2 cores.
- **vs ripgrep on agent-style questions (django):** "Who calls `get_or_create`, from which function" took 22 rg calls and 58 KB for grep vs 1 call and 9 KB for duckgrep. "Everything within 3 hops of `execute_sql`" took 110 calls and 104 KB vs 1 call and 113 bytes.
- **Call-graph precision vs jedi (requests):** confident tiers covered 85.5% of in-repo calls with 100% precision. This is a small sample (55 calls); django is still to run.

## Known gaps / next

1. **Calls on local variables** (`compiler.execute_sql()`) fall to `name`/`ambiguous`. On django, a 3-hop caller walk finds 4 functions via confident edges and 2,639 if name-only edges are followed. Light local type inference (assignment from a constructor or annotated param, then attribute calls) is the next big win.
2. Run `bench/accuracy.py` on django at scale; add a TS equivalent (tsserver as reference).
3. Optional SCIP ingestion where an indexer exists, for exact edges.
4. Task-level eval: agent success, turns and tokens on real tasks with vs without duckgrep (vs plain grep and Serena).
5. Refresh on edit touches every ref sharing a name with the edited file's definitions (~60k refs for django `query.py`). Scope the dirty set tighter.

## Dev

```bash
uv sync                          # .venv with the dev tools: pytest, ruff, pre-commit
uv run pre-commit install        # ruff check --fix, ruff format and pytest on every commit
uv run pytest
uv run python bench/latency.py <repo> <file-to-edit> <symbol> <qualname>
uv run python bench/vs_grep.py <django-checkout>
uv run --group bench python bench/accuracy.py <python-repo> [n]   # jedi as the reference
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
