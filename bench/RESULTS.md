# Benchmark results

Run 2026-09-29 on a 2-core / 8 GB Linux VM, Python 3.11, DuckDB 1.5.6.
Repos: django @ 9332b163 (7,091 files), vscode @ 4b243603 (19,545 files), requests @ 611c616 (130 files).

## Index size

| repo | files | symbols | refs | commits | db size |
|---|---:|---:|---:|---:|---:|
| django | 7,087 | 51,864 | 849,470 | 2,000 | 99 MB |
| vscode | 19,544 | 364,778 | 6,545,413 | (no-git) | 549 MB |
| requests | 128 | 911 | 14,527 | 1,203 | 8 MB |

The full vscode (re)index took 144 s.

## Latency (`bench/latency.py`, medians)

| | django | vscode | requests |
|---|---:|---:|---:|
| no-op freshen | 138 ms | 242 ms | 42 ms |
| freshen after editing 1 file | 327 ms | 508 ms | 179 ms |
| + call-graph sync (only if the query needs edges) | 792 ms (63.6k refs) | 1,160 ms (22.3k refs) | 125 ms |
| `defs()` | 32 ms | 76 ms | 18 ms |
| `callers()`, confident | 37 ms | 209 ms | 29 ms |
| `grep()` literal | 89 ms | 367 ms | 40 ms |
| `callees()` | 40 ms | 99 ms | 19 ms |
| 2-hop transitive callers | 40 ms | 169 ms | 33 ms |
| unreferenced functions (whole repo) | 55 ms | 266 ms | 25 ms |
| churn × classes | 33 ms | 42 ms | 25 ms |

Symbols used: django `QuerySet.get_or_create` (edit `django/db/models/query.py`), vscode `createDecorator` (edit `src/vs/base/common/strings.ts`), requests `Session.request`.

## vs ripgrep (`bench/vs_grep.py`, django)

The grep side is simulated generously: every rg call is exact, and reading a hit's enclosing function counts as one call per distinct file.

| Question | grep: calls | grep: bytes | grep notes | duckgrep: calls | duckgrep: bytes | duckgrep result |
|---|---:|---:|---|---:|---:|---|
| Where is get_or_create defined? | 1 | 484 | 5 matching lines | 1 | 882 | 5 defs with signature + qualname |
| Who calls get_or_create, from which function? | 22 | 57,978 | 124 hits, 69 are call sites (55 docs/comments/defs); +21 file reads for the enclosing function | 1 | 9,143 | 73 call sites with caller |
| What does SQLCompiler.execute_sql call, and where are those defined? | 20 | 16,421 | read the function, then one rg per callee name (19) | 1 | 3,207 | 37 refs with targets |
| Everything that reaches SQLCompiler.execute_sql within 3 hops | 110 | 103,959 | iterative rg -w + reading each hit's function; 23 names explored | 1 | 113 | 4 functions via confident edges; 2,639 if name-only edges are followed too |
| Public functions in django/utils never referenced anywhere | 334 | — | one rg per function (334) | 1 | 64 | 1 candidate |

On a plain "where is X defined" question, grep is as good and cheaper. The win is on structural questions.

## Call-graph accuracy vs jedi (`bench/accuracy.py`, requests, 300 sampled calls)

jedi's goto-definition serves as the reference. 55 calls had an in-repo target; jedi resolved 245 outside the repo.

| duckgrep tier | share | contains jedi target | exactly it | avg candidates |
|---|---:|---:|---:|---:|
| self | 32.7% | 100% | 100% | 1.0 |
| import | 23.6% | 100% | 100% | 1.0 |
| local | 20.0% | 100% | 100% | 1.0 |
| name | 12.7% | 100% | 100% | 1.0 |
| module | 5.5% | 100% | 100% | 1.0 |
| qualified | 3.6% | 100% | 100% | 1.0 |
| unresolved | 1.8% | 0% | 0% | 0 |

Confident tiers covered 85.5% of in-repo calls with 100% precision. Of the calls jedi resolved outside the repo, duckgrep marked 86% `unresolved` and 14% `name`, meaning a false in-repo candidate. The sample is small; a django run is still to do.
