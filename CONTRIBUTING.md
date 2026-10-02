# Contributing

## Setup

```bash
uv sync                          # .venv with the dev tools: pytest, ruff, pre-commit
uv run pre-commit install        # ruff check --fix, ruff format and pytest on every commit
uv run pytest                    # the whole suite, under a minute
```

## Benchmarks

```bash
uv run python bench/latency.py <repo> <file-to-edit> <symbol> <qualname>
uv run python bench/vs_grep.py <django-checkout>
uv run --group bench python bench/accuracy.py <python-repo> [n]   # jedi as the reference
```

The agent A/B harness lives in `bench/eval/`. Its design is in `docs/specs/2026-09-29-ab-eval-harness-design.md` and `docs/specs/2026-09-30-ab-eval-full-run.md`, and its commands are in [CLAUDE.md](CLAUDE.md).
- **Cost:** `run` spends money, and everything else is free.
- **Cache:** set `DUCKGREP_EVAL_CACHE` to put its clones and worktrees on a big disk.

## Changes

- **Every change lands through a pull request.** Branch from an up-to-date `main`, commit through the pre-commit hook (never `--no-verify`), push, and open the PR. A hook refuses commits on `main`.
- **Every PR is reviewed by Bugbot and Greptile.** Fix what they find that's valid, and answer the rest with evidence.
- **Correctness of the index:** an incremental index must equal a full rebuild. A change to reference resolution or to what an edit marks dirty needs a test asserting `snapshot(root) == fresh_snapshot(root, tmp_path)` (see `tests/helpers.py`).
- **New tests** build their repos with `helpers.make_repo`; `tests/fixture/` is data whose line numbers tests assert.
- **Docs locations:** design specs go in `docs/specs/` and implementation plans in `docs/plans/`, named `YYYY-MM-DD-<topic>.md`.
- **Copy:** the brand's rules for copy (name, messaging, voice, colour, type) are in [brand/README.md](brand/README.md).

The architecture, the resolution tiers and the gotchas are in [CLAUDE.md](CLAUDE.md).

## Releasing

Releases go to PyPI from `.github/workflows/release.yml`, through PyPI's trusted publishing; there is no token to keep.
1. In a PR, bump `version` in `pyproject.toml` (and run `uv lock`), then merge it.
2. Publish a GitHub release from `main`, tagged `v<version>` (for example `v0.1.0`). The workflow checks that the tag matches the version, builds, installs the wheel in a clean environment and runs a smoke test, then uploads.

Running the workflow by hand (Actions → release → Run workflow) builds and smoke-tests without publishing.
