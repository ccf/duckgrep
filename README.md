# duckgrep

**Grep finds strings. duckgrep answers questions.**

Give your coding agent a live, queryable model of your codebase (definitions, callers, imports, history), so structural questions take one query instead of a search loop.

## What it saves

We measured duckgrep with Claude Code (Sonnet 5.5) on 494 structural questions from 25 open-source Python and Rust repos: who calls this, what reaches it through one more caller, what imports this module. In every setup the model and tools were the same; the only difference was the MCP server. Each agent also had the same one-line instruction to use its tool.

| Per question | Claude Code alone | + Serena | **+ duckgrep** |
|---|---:|---:|---:|
| ***Python** (237 questions)* | | | |
| Tool calls | 3.0 | 2.8 (−5%)† | **2.3 (−18%)** |
| Round trips to the model | 3.6 | 2.9 (−16%) | **2.8 (−19%)** |
| Tokens | 27.4k | 33.6k (+23%) | **22.5k (−18%)** |
| Cost | $0.0267 | $0.0255 (−4%)† | **$0.0203 (−24%)** |
| Correct answers | 94% | 95% | **96%** |
| ***Rust** (257 questions)* | | | |
| Tool calls | 3.3 | 3.9 (+14%) | **2.6 (−16%)** |
| Round trips to the model | 3.7 | 3.8 (+2%)† | **3.1 (−12%)** |
| Tokens | 29.4k | 45.9k (+56%) | **26.0k (−12%)** |
| Cost | $0.0299 | $0.0336 (+12%) | **$0.0226 (−25%)** |
| Correct answers | 96% | 96% | **96%** |

**12–19% less work and a quarter less cost, with answers just as accurate.**
- **Why:** duckgrep answers "who calls this, and from which function" in one call. A grep hit gives a line, and the agent then has to read around it to find the function.
- **Without the instruction,** agents still chose duckgrep for 75% of these questions. They cut tool calls by 12–19% and cost by 14–18%.

**Where it doesn't help:** finding where to fix a bug from an issue report (230 SWE-bench tasks). The issue usually names something one grep finds, so there's nothing to save. Carrying the tool's description added 11–13% tokens on Python; they're cached, so cost stayed flat.

<sub>3,620 runs in all, made on 2026-10-02 with Claude Code 2.1.287. Changes compare each setup with Claude Code alone, task by task, with tool calls and round trips compared as 1 + n. † marks a difference that doesn't hold after correcting for all 160 comparisons. Every duckgrep change in calls, round trips, tokens and cost holds; the correct-answer rates differ by no more than chance in any setup. Full tables, the method and the harness are in [bench/RESULTS.md](https://github.com/ccf/duckgrep/blob/main/bench/RESULTS.md) and [bench/eval](https://github.com/ccf/duckgrep/tree/main/bench/eval).</sub>

## Why it matters

Coding agents find their way around a codebase with chains of `grep` → read file → `grep` again. That works for "where is this string." But the questions that matter when changing code are structural: *who calls this, what does it call, what breaks if I change it, what's dead*. Each one turns into a loop of searches, file reads and guesses, paid for in turns and tokens.

- **It answers structural questions, not text matches.** "Who calls this, and from which function?" is one query, not a search loop. On django, finding every call to `get_or_create` and the function it sits in took 22 calls (one grep, then 21 file reads to find the enclosing functions) and 58 KB of output. duckgrep answered it in one query and 9 KB.
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

Languages: Python, TypeScript/TSX, JavaScript, Go, Rust. Every other text file is still indexed for `grep()`. Built on DuckDB and tree-sitter.

## Install

```bash
uv tool install duckgrep
```

### Claude Code

```bash
claude mcp add duckgrep -- duckgrep mcp
```

The server answers at once and builds the index in the background; the first call waits for it. After that, every call runs an incremental refresh, so results reflect the agent's latest edits with no hooks.

### Tell your agent to use it

Agents reach for grep out of habit. Add this to your `CLAUDE.md` (or your agent's instructions). It's the line the numbers above were measured with:

> The repository in the current directory is indexed by duckgrep. To find code, call its query tool before Grep, Glob or Read: SELECT * FROM defs('name') finds where a symbol is defined, callers('name') who calls it, and grep('regex') searches the text and names each match's enclosing function. Read a file once you know where to look.

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

## FAQ

<details>
<summary><b>What can the agent query?</b></summary>

| table | what |
|---|---|
| `files` | path, lang, size, sha, n_lines, skipped, parse_errors |
| `symbols` | name, qualname (`Class.method`, `GoType.Method`), kind, parent, lines, signature, first doc line, exported |
| `refs` | every identifier use: kind (`call`/`attr`/`write`/`type`/`inherit`/`decorator`/`import`/...), receiver text, enclosing scope |
| `imports` / `imports_resolved` | as written, plus the repo file each resolves to |
| `edges` | reference → definition (the call graph), with a `resolution` tier |
| `lines` | full text, for `grep()` |
| `commits`, `file_changes`, `file_churn` | git history |

Table macros: `defs`, `callers`, `callees`, `outline`, `grep`, `source`. `duckgrep schema` prints exactly what the agent sees.
</details>

<details>
<summary><b>How does it resolve calls without a type checker?</b></summary>

Tree-sitter gives syntax, not types, so every edge says how it was resolved:

- **Confident:** `self`, `local`, `package` (Go), `import`, `module` and `qualified`. These come from scope and import analysis:
  - aliases, relative imports, and star imports (Python and Rust);
  - `go.mod` prefixes and Cargo crate names;
  - one hop of re-exports (`__init__.py`, `export … from`, `pub use`).

  `qualified` (`Class.method`, `Type::method`) requires the class to be defined or imported in the calling file.
- **`typed`** (confident, Python for now): `obj.method()` where the receiver's class can be read from syntax: `x = Foo()`, an annotation (`p: Foo`, or `-> Foo` one call away), `self.attr` set or annotated in the class, an imported module-level instance, or `self`/`super()` through base classes in other files. A name bound more than once, or to something untypable, gets no inference. The edge goes to the method on that class or its nearest in-repo base; a subclass that overrides it isn't followed, so `callers('Sub.m')` misses calls typed as the base. A receiver whose class is outside the repo (`io.StringIO()`, a literal) is `unresolved`, not a `name` guess.
- **`name`:** `obj.method()` on a receiver of unknown type, matched by method name only. Kept as one row per candidate when there are ≤10 candidates.
- **`ambiguous`:** more than 10 candidates, or a method that builtin types also have (`get`, `append`, `push`, `clone`…). The target is left NULL rather than guessed. `callers('Class.method')` ends with a row counting these calls.
- **`unresolved`:** no in-repo target found. That covers stdlib, builtins, third-party code, calls on a receiver bound to an external import (`json.dumps`), and imports duckgrep can't follow.

A bare name (`helper()`) resolves only through its file's scope and imports. It never matches another file's definition by name alone; Rust macros and `.d.ts` declarations are the exceptions.
</details>

<details>
<summary><b>How accurate is the call graph?</b></summary>

Measured against jedi's goto-definition on sampled calls in Python repos ([bench/RESULTS.md](https://github.com/ccf/duckgrep/blob/main/bench/RESULTS.md)):

| repo | calls with an in-repo target | resolved confidently | precision |
|---|---:|---:|---:|
| django | 1,411 | 73.4% | 99.8% |
| freqtrade | 1,646 | 71.9% | 100% |
| requests | 55 | 85.5% | 100% |

The rest fall to `name`, `ambiguous` or `unresolved`, which say so rather than guess. Calls on local variables are the main gap; see the [roadmap](https://github.com/ccf/duckgrep/blob/main/docs/roadmap.md).
</details>

<details>
<summary><b>Does it stay right while the agent edits code?</b></summary>

Yes. `freshen()` runs before every query: one `git ls-files`, a stat per file, then a content hash only for files whose size or mtime changed. Changed files have their rows swapped in one transaction.

- **Edges that depend on other files** (same name elsewhere, imports that move when a file appears or disappears) are marked dirty. They are recomputed lazily, and only when a query touches `edges`/`callers`/`callees`.
- **Tests check that the incremental index equals a full rebuild** after renames, shadowing modules, deletions, re-export and star-import changes, and `go.mod`/`Cargo.toml` renames.
- **A rebuild interrupted midway** is finished by the next query.
- **An upgrade** that changes the extractor, the grammars or the table layout triggers a full re-parse. One that changes only the resolution SQL rebuilds the call graph.
</details>

<details>
<summary><b>Which directory does it index, and where does the index live?</b></summary>

The root is `-C DIR`, else `$DUCKGREP_ROOT` (for the MCP server), else the nearest directory above the working directory with `.git` or `.duckgrep`. Outside a repository, duckgrep refuses to start; it never indexes a directory by guess. The index is one DuckDB file, `<root>/.duckgrep/index.duckdb`, which ignores itself in git.
</details>

<details>
<summary><b>How fast is it?</b></summary>

On django (7k files):
- a no-op refresh takes about 140 ms;
- one edited file takes about 330 ms, plus about 0.8 s of call-graph sync when the query needs it;
- typical queries take 30–90 ms.

On vscode (19.5k files, 6.5M refs), typical queries take 80–370 ms, and the full initial index takes about 2.5 minutes on 2 cores.
</details>

<details>
<summary><b>Where doesn't it help?</b></summary>

- **Finding where to fix a bug from an issue report.** On 230 SWE-bench tasks, agents with duckgrep were no faster or cheaper, because an issue usually names an error message or a function that one grep finds.
- **"Where is this string."** Grep is as good, and cheaper.

duckgrep pays off when the question is about how code connects.
</details>

<details>
<summary><b>How does it compare with Serena or a language server?</b></summary>

- **The approach:** Serena gives agents a fixed set of language-server tools (find symbol, find references). duckgrep gives them one query over the whole parse, the call graph and git history, so questions combine in a single call.
- **In the same benchmark:**
  - Serena added 23–56% tokens, and it cost 12% more on Rust.
  - duckgrep cut cost by 24–25%.
  - Accuracy was level across all three.
- **The trade-off:** a language server resolves types exactly; duckgrep's edges say when they're guessing.
</details>

<details>
<summary><b>How were the numbers measured?</b></summary>

- **The runs:** the real Claude Code CLI ran in a scrubbed environment, with Bash, Read, Grep and Glob, under five setups that differ only in one MCP server and an optional one-line instruction.
- **The tasks:** 724 in all.
  - Structural questions, with answer keys from jedi and rust-analyzer.
  - Issue localization from SWE-bench Lite, Multilingual and Live, with keys from the fix patches.
- **The statistics:** each setup is paired with plain Claude Code task by task, with bootstrap intervals and a Holm correction across all comparisons.

The harness, the suites and every number are in the repo: [bench/eval](https://github.com/ccf/duckgrep/tree/main/bench/eval) and [bench/RESULTS.md](https://github.com/ccf/duckgrep/blob/main/bench/RESULTS.md).
</details>

## Roadmap and contributing

- **[docs/roadmap.md](https://github.com/ccf/duckgrep/blob/main/docs/roadmap.md):** what's next and the known gaps.
- **[CONTRIBUTING.md](https://github.com/ccf/duckgrep/blob/main/CONTRIBUTING.md):** how to set up, test and benchmark.

## License

Apache License 2.0. See [LICENSE](https://github.com/ccf/duckgrep/blob/main/LICENSE).
