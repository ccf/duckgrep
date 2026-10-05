# Roadmap

What's next for duckgrep, and the gaps known today. The measured numbers behind each item are in [bench/RESULTS.md](../bench/RESULTS.md).

## Next

1. **Type inference, Phase 3 (Python):**
   - tuple unpacking;
   - several bindings that agree once resolved to a class, with `None` as neutral;
   - imports inside functions recorded as imports;
   - what well-known external bases (the stdlib) define, so they stop blocking the method search;
   - container element types.

   Then TypeScript, Go and Rust, each in its own spec.
2. **A TypeScript accuracy benchmark:** a TS equivalent of `bench/accuracy.py`, with tsserver as the reference.
3. **Optional SCIP ingestion** where an indexer exists, for exact edges.

## Known gaps

- **Typed receivers that turn external:** when a class's ancestry gains or loses an external base in another file, or a callee's return (annotated, or since Phase 2 inferred from its body) becomes external, calls on its instances should flip between `name` and `unresolved`. The incremental index doesn't mark them, so they keep the old tier until those refs are recomputed. A full rebuild gets them right. The fix needs a narrow trigger: one that compares each seed class's external status before and after, because marking every descendant's users was too broad on django.

- **Finding where to fix an issue:** the agent A/B run measured no saving there, because an issue usually names something one grep finds. A second repetition would settle a possible small gain on Rust (cost ×0.89, not yet significant).
- **Refresh on edit** touches every ref sharing a name with the edited file's definitions: about 60k refs for django's `query.py`. The dirty set could be scoped tighter.
- **Aliased re-export chains** can keep a stale edge after an edit to the underlying module. That covers `from pkg import X as Y` where `pkg/__init__.py` re-exports `X`, and TS barrels.
  - The differential fuzzer still finds this on a re-export-heavy synthetic repo.
  - Real repos rarely hit it: 6 of 32,754 import edges in a large TS repo.

## Done

- **Python receiver types (the `typed` tier):** spec `docs/specs/2026-10-02-local-type-inference.md`, results in `bench/RESULTS.md`.
- **Type inference, Phase 2:** named and star re-exports up to three hops, class-body aliases, one attribute hop, call-result receivers, inferred returns, and function-local classes. Spec `docs/specs/2026-10-04-type-inference-phase-2.md`.
