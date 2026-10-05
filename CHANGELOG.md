# Changelog

Notable changes to the `duckgrep` package, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). Benchmark numbers live in [bench/RESULTS.md](bench/RESULTS.md).

## [Unreleased]

### Fixed
- **Indexing large repos on many-core machines no longer runs out of memory.** DuckDB's working memory grows with its thread count, so indexing now uses at most 4 threads (`DUCKGREP_THREADS` overrides it, as `DUCKGREP_MEMORY` does the 2 GB limit). With one thread per core, django's full edge rebuild needed nearly all of the 2 GB on a 16-core machine; at 4 threads it fits in 1 GB at the same speed.

## [0.2.1] - 2026-10-03

### Changed
- **Catching up after many files change is faster.** After a branch switch, a big pull or a copied index, the next query re-indexes the changed files. Moving a django checkout 300 commits (698 files) now takes 5.8 s instead of 13.5 s, and a copied index on the same commit catches up in 0.6 s instead of 2.2 s.

## [0.2.0] - 2026-10-03

### Added
- **The `typed` tier:** Python receiver calls (`obj.method()`) get one confident edge when the receiver's class can be read from syntax. It covers:
  - `x = Foo()`;
  - annotations, including `Optional[...]`, `X | None` and string annotations;
  - `self.attr` set or annotated in the class;
  - one hop of return annotations;
  - imported module-level instances;
  - `self` and `super()` through base classes in other files.

  Against jedi, confident coverage of in-repo calls rose from 66.8% to 87.8% on django, 74.3% to 90.9% on freqtrade and 67.6% to 90.1% on requests, with every `typed` edge exact.
- **Calls on receivers whose class is outside the repo** (`io.StringIO()`, literals, classes with an external base) are now `unresolved`, not `name` guesses.

### Changed
- **The resolver refuses rather than guesses:** a name bound more than once in a file, Python's method resolution order where syntax can't settle it, and a name a nested def or class shares all get no `typed` edge and keep the older tiers.
- **Edge refresh after an edit** reads dirty refs in batches, and falls back to a batched rebuild if it runs out of memory, instead of leaving the index unable to answer call-graph queries.
- **The schema is at version 4** (a new `bindings` table and `symbols.returns`). Existing indexes rebuild themselves on first use.
- **The `query(sql)` tool description** names the `typed` tier.

### Fixed
- **Standard-library imports** (`import json`, `datetime`, `math` ...) no longer resolve to a nested repo module that happens to share the name.
- **`duckgrep.__version__`** now reports the installed package's version.

### Known gaps
- **A class or return that turns external in another file:** when a class's ancestry, or a function's return annotation, starts or stops being external in another file, calls on its instances keep their old `name`/`unresolved` tier until those refs are next recomputed.

## [0.1.0] - 2026-10-02

### Added
- **The first release.** A tree-sitter index of Python, TypeScript/TSX, JavaScript, Go and Rust in one DuckDB file, refreshed before every query. It's served as one read-only `query(sql)` MCP tool (`duckgrep mcp`), with a CLI (`duckgrep q`, `callers`, `callees`, `def`, `outline`, `grep`, `source`).
- **A call graph with resolution tiers:** `self`, `local`, `package`, `import`, `module`, `qualified`, `name`, `ambiguous` and `unresolved`. Git history comes in as `commits`, `file_changes` and `file_churn`.

[Unreleased]: https://github.com/ccf/duckgrep/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/ccf/duckgrep/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ccf/duckgrep/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ccf/duckgrep/releases/tag/v0.1.0
