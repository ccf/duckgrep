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
- **CI** (`.github/workflows/ci.yml`) runs on every PR: `lint` (`uv lock --check`, `ruff check`), `format` (`ruff format --check`) and `test (3.10)` / `test (3.13)` (pytest), all through uv with the versions in `uv.lock`. `main` is protected: it takes changes only through a PR whose checks pass.
- **CHANGELOG:** a PR that changes what users of the package see (behaviour, the index, the tool, the CLI) adds a line under `## [Unreleased]` in `CHANGELOG.md`.
- **Every PR is reviewed by Bugbot and Greptile.** Fix what they find that's valid, and answer the rest with evidence.
- **Correctness of the index:** an incremental index must equal a full rebuild. A change to reference resolution or to what an edit marks dirty needs a test asserting `snapshot(root) == fresh_snapshot(root, tmp_path)` (see `tests/helpers.py`).
- **New tests** build their repos with `helpers.make_repo`; `tests/fixture/` is data whose line numbers tests assert.
- **Docs locations:** design specs go in `docs/specs/` and implementation plans in `docs/plans/`, named `YYYY-MM-DD-<topic>.md`.
- **Copy:** the brand's rules for copy (name, messaging, voice, colour, type) are in [brand/README.md](brand/README.md).

The architecture, the resolution tiers and the gotchas are in [CLAUDE.md](CLAUDE.md).

## Releasing

Releases go to PyPI from `.github/workflows/release.yml`, through PyPI's trusted publishing; there is no token to keep.

1. **Before the first release,** the repo must be public and duckgrep.dev live: PyPI shows the README's links and the project URLs, and a release's description can't be changed without a new version.
2. In a PR, bump `version` in `pyproject.toml` (and run `uv lock`), and move `CHANGELOG.md`'s Unreleased notes under a new `## [<version>] - YYYY-MM-DD` heading, with its compare link at the bottom. A test fails while the version has no entry. Merge it.
3. Dry run: Actions → release → Run workflow on `main` (`gh workflow run release.yml --ref main`). It runs the tests, builds, and smoke-tests the wheel and its MCP server, without publishing.
4. Publish a GitHub release from `main`, tagged `v<version>` (for example `v0.1.0`). The workflow checks that the tag matches the version and that the commit is on `main`, repeats the checks, then uploads. A GitHub prerelease needs a prerelease version (`0.2.0rc1`), or the workflow refuses it.

If a release fails before the upload (nothing reaches PyPI until every check passes), delete the GitHub release and its tag, fix `main`, and release the same version again. If only part of an upload landed, rerunning fails with "File already exists"; set `skip-existing: true` on the publish step for that one rerun.
