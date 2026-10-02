# duckgrep

**Grep finds strings. duckgrep answers questions.**

Give your coding agent a live, queryable model of your codebase (definitions, callers, imports, history), so structural questions take one query instead of a search loop.

## Why it matters

Coding agents find their way around a codebase with chains of `grep` → read file → `grep` again. That works for "where is this string." But the questions that matter when changing code are structural: *who calls this, what does it call, what breaks if I change it, what's dead*. Each one turns into a loop of searches, file reads and guesses, paid for in turns and tokens.

- **It answers structural questions, not text matches.** "Who calls this, and from which function?" is one query, not a search loop. On django, finding every call to `get_or_create` and the function it sits in took 22 grep calls and 58 KB of output, most of it docs, comments and whole files. duckgrep answered it in one query and 9 KB.
- **It answers questions no tool author anticipated.** Code-navigation tools give agents a fixed menu: find definition, find references. duckgrep gives them a query language. "Public functions in `pricing/` with no callers outside their module" isn't a feature anyone built; the agent just writes the query.
- **It joins code with its history.** The call graph, git churn and authorship sit in the same place, so an agent can ask "what's risky to change here," which text search can't answer.
- **It's trustworthy mid-edit.** The index refreshes before every query, so it reflects the agent's latest edits. Every call-graph edge says how it was resolved, so the agent knows when to trust the graph and when to go read the code.

## How it works

duckgrep parses the repo with tree-sitter into a single DuckDB file and exposes it as **one read-only `query(sql)` MCP tool**. The tool's description carries the whole schema, so the agent can write a correct query on its first call.

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

The server's root is `-C DIR`, else `$DUCKGREP_ROOT`, else the nearest directory above its working directory with `.git` or `.duckgrep`. Outside a repository it refuses to start. It answers at once and builds the index in the background; the first call waits for it. After that, every call runs an incremental refresh, so results reflect the agent's latest edits with no hooks.

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

- **Confident:** `self`, `local`, `package` (Go), `import`, `module` and `qualified`. These come from scope and import analysis:
  - aliases, relative imports, and star imports (Python and Rust);
  - `go.mod` prefixes and Cargo crate names;
  - one hop of re-exports (`__init__.py`, `export … from`, `pub use`).

  `qualified` (`Class.method`, `Type::method`) requires the class to be defined or imported in the calling file.
- **`name`:** `obj.method()` on a receiver of unknown type, matched by method name only. Kept as one row per candidate when there are ≤10 candidates.
- **`ambiguous`:** more than 10 candidates, or a method that builtin types also have (`get`, `append`, `push`, `clone`…). The target is left NULL rather than guessed. `callers('Class.method')` ends with a row counting these calls.
- **`unresolved`:** no in-repo target found. That covers stdlib, builtins, third-party code, calls on a receiver bound to an external import (`json.dumps`), and imports duckgrep can't follow.

A bare name (`helper()`) resolves only through its file's scope and imports. It never matches another file's definition by name alone; Rust macros and `.d.ts` declarations are the exceptions.

## How it stays fresh

`freshen()` runs before every query: one `git ls-files`, a stat per file, then a content hash only for files whose size or mtime changed. Changed files have their rows swapped in one transaction. Edges that depend on other files (same name elsewhere, imports that move when a file appears or disappears) are marked dirty. They are recomputed lazily, and only when a query touches `edges`/`callers`/`callees`. Tests check that the incremental index equals a full rebuild after renames, shadowing modules, deletions, re-export and star-import changes, and `go.mod`/`Cargo.toml` renames. A rebuild interrupted midway is finished by the next query. An upgrade that changes the extractor, the grammars or the table layout triggers a full re-parse. One that changes only the resolution SQL rebuilds the call graph.

## Results so far

Full numbers are in [bench/RESULTS.md](bench/RESULTS.md). Headlines:

- **Agents on real tasks (Claude Code with Sonnet 5.5; 3,620 runs on 724 tasks from 25 open-source repos):**
  - **Structural questions** (494 questions on callers, two-hop callers and importers, in Python and Rust): with duckgrep, agents used 12–19% fewer tool calls, 8–19% fewer round trips and 14–25% less cost than with Claude Code's own tools alone. Accuracy was the same, 93–97%.
  - **Finding where to fix an issue** (230 SWE-bench tasks): it saved nothing, since the spot is usually one grep away, and on Python, carrying the tool added 11–13% tokens. Those are cached, so cost was flat.
  - **Serena, measured alongside:** it added 47–58% tokens on structural questions.
- **Latency (django, 7k files):** a no-op refresh takes ~140 ms, one edited file ~330 ms (+~0.8 s call-graph sync when the query needs it), and typical queries 30–90 ms. On vscode (19.5k files, 6.5M refs) typical queries take 80–370 ms. The full initial index of vscode takes ~2.5 min on 2 cores.
- **vs ripgrep on agent-style questions (django; simulated generously for grep, one exhaustive answer per question):** "Who calls `get_or_create`, from which function" took 22 rg calls and 58 KB for grep vs 1 call and 9 KB for duckgrep. "Everything within 3 hops of `execute_sql`" took 110 calls and 104 KB vs 1 call and 113 bytes.
- **Call-graph precision vs jedi:**
  - requests: confident tiers covered 85.5% of 55 in-repo calls at 100% precision.
  - freqtrade (1,118 in-repo calls): 73% at 100% precision.
  - django (474): 72–76% at 99.4–100%. The misses reached the right definition plus a same-named nested class.

## Known gaps / next

1. **Calls on local variables** (`compiler.execute_sql()`) fall to `name`/`ambiguous`, and so do methods named like builtins (`cache.get()`) and methods of external objects (`con.execute()` on a duckdb connection). On django, a 3-hop caller walk finds 4 functions via confident edges and 2,639 if name-only edges are followed. Light local type inference would turn 20–65% of these receiver calls into correct confident edges (measured against jedi on five Python repos): inherited `self`/`super()` methods, `x = Foo()`, annotated parameters, `self.attr`, imported module-level instances, and return annotations. It is the next big win.
2. Add a TS equivalent of `bench/accuracy.py`, with tsserver as the reference.
3. Optional SCIP ingestion where an indexer exists, for exact edges.
4. Finding where to fix an issue: the task-level eval measured no saving there, as an issue usually names something one grep finds. A second repetition of the eval would settle a possible small gain on Rust (cost ×0.89, not yet significant).
5. Refresh on edit touches every ref sharing a name with the edited file's definitions (~60k refs for django `query.py`). Scope the dirty set tighter.
6. **Aliased re-export chains** (`from pkg import X as Y` where `pkg/__init__.py` re-exports `X`, and TS barrels) can keep a stale edge after an edit to the underlying module. The differential fuzzer still finds this on a re-export-heavy synthetic repo; real repos rarely hit it (6 of 32,754 import edges in a large TS repo).

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
