# Roadmap

What's next for duckgrep, and the gaps known today. The measured numbers behind each item are in [bench/RESULTS.md](../bench/RESULTS.md).

## Next

1. **Light local type inference for receiver calls.**
   - **The gap:** calls on local variables (`compiler.execute_sql()`) fall to `name` or `ambiguous`, and so do methods named like builtins (`cache.get()`) and methods of external objects (`con.execute()` on a duckdb connection). On django, a 3-hop caller walk finds 4 functions through confident edges, and 2,639 if name-only edges are followed.
   - **The fix:** inferring types from inherited `self`/`super()` methods, `x = Foo()`, annotated parameters, `self.attr`, imported module-level instances and return annotations.
   - **The payoff:** that would turn 20–65% of these receiver calls into correct confident edges, measured against jedi on five Python repos. It is the next big win.
2. **A TypeScript accuracy benchmark:** a TS equivalent of `bench/accuracy.py`, with tsserver as the reference.
3. **Optional SCIP ingestion** where an indexer exists, for exact edges.

## Known gaps

- **Finding where to fix an issue:** the agent A/B run measured no saving there, because an issue usually names something one grep finds. A second repetition would settle a possible small gain on Rust (cost ×0.89, not yet significant).
- **Refresh on edit** touches every ref sharing a name with the edited file's definitions: about 60k refs for django's `query.py`. The dirty set could be scoped tighter.
- **Aliased re-export chains** can keep a stale edge after an edit to the underlying module. That covers `from pkg import X as Y` where `pkg/__init__.py` re-exports `X`, and TS barrels.
  - The differential fuzzer still finds this on a re-export-heavy synthetic repo.
  - Real repos rarely hit it: 6 of 32,754 import edges in a large TS repo.
