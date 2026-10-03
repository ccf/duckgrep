# Receiver-type census

These scripts measure how much of the receiver-call gap syntax-only type rules can close, scored against jedi. They sized the typed tier in `docs/specs/2026-10-02-local-type-inference.md`, and remain its ceiling: Python's `ast` with full scope analysis, the rules simulated exactly.

Run from the repo root:

1. **jedi's answer for every gap call:** `<venv>` is a virtualenv with the repo's dependencies installed, so jedi can follow them. On django, set `NO_DJANGO_STUBS=1`, so jedi reads django's source and not its bundled stubs.

   ```bash
   uv run --group bench python bench/typecensus/jedi_truth.py <repo> <venv> truth-<r>.jsonl 0 14
   ```

2. **Classify each call's binding and score the strict and lenient rules:**

   ```bash
   uv run python bench/typecensus/census.py <repo> truth-<r>.jsonl --examples ex-<r>.txt --json census-<r>.json
   ```

3. **Merge the per-repo tables:**

   ```bash
   python3 bench/typecensus/summarize.py ex-*.rows.jsonl
   ```

4. **The cross-file and override shares:**

   ```bash
   uv run python bench/typecensus/overrides.py <repo> ex-<r>.txt.rows.jsonl
   ```

The 2026-10-02 run (django 0ae93a0, freqtrade f2ec745, requests 611c616, jedi 0.20.0) is summarised in the spec's Evidence section.
