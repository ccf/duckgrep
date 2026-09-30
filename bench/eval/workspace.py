"""Local copies of the task repos: one full clone per repo, one worktree per setup and commit, the duckgrep
index and Serena warm-up for each worktree, and the pinned rust-analyzer."""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import config, datasets, setups


def git_env() -> dict[str, str]:
    """The environment without GIT_* variables: a commit hook exports GIT_DIR, which would redirect every git call.
    LFS files check out as their pointers: a worktree has no remote to download their content from, and no task
    needs it."""
    return {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_LFS_SKIP_SMUDGE": "1"}


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, env=git_env(), capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8", "replace")  # repos hold files in other encodings


def slug(repo: str) -> str:
    return repo.lower().replace("/", "__")


def clone(repo: str, cache: Path, url: str | None = None) -> Path:
    """A full bare clone of `repo`, made once. Not a partial clone: duckgrep indexes `git log --numstat`, and an
    agent may read history too, and in a blobless clone each old blob is a separate network fetch."""
    dest = cache / "repos" / f"{slug(repo)}.git"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        if tmp.exists():
            shutil.rmtree(tmp)
        git("clone", "--bare", url or f"https://github.com/{repo}.git", str(tmp))
        tmp.rename(dest)
    return dest


# The agent sees its working directory, so the path to a worktree must not name the setup or the tool under test.
GROUPS = {"baseline": "t1", "duckgrep": "t2", "serena": "t3"}


def worktree_path(cache: Path, setup: str, repo: str, commit: str) -> Path:
    return cache / "wt" / GROUPS.get(setup, setup) / f"{slug(repo)}@{commit[:12]}"


def worktree(repo: str, commit: str, setup: str, cache: Path, url: str | None = None) -> Path:
    """A checkout of `repo` at `commit` for one setup. It is a repository of its own that borrows the clone's
    objects and has no ref but a detached HEAD, so nothing after `commit` (the fix, later tags, other branches) is
    reachable from it, and nothing one setup's agent does to git is visible to another's."""
    path = worktree_path(cache, setup, repo, commit)
    if (path / ".git").exists():
        return path
    bare = clone(repo, cache, url)
    if git("cat-file", "-t", commit, cwd=bare, check=False).strip() != "commit":
        git("fetch", "origin", f"{commit}:refs/eval/{commit}", cwd=bare)  # a ref keeps gc from pruning it
    tmp = path.with_name(path.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    git("init", "-q", str(tmp))
    (tmp / ".git" / "objects" / "info" / "alternates").write_text(f"{(bare / 'objects').resolve()}\n")
    git("checkout", "-q", "--detach", commit, cwd=tmp)
    tmp.rename(path)
    return path


def changes(path: Path) -> list[str]:
    """Tracked, untracked and ignored changes; the duckgrep index is not a change."""
    out = git("status", "--porcelain", "--ignored", "--untracked-files=all", cwd=path)
    return [line for line in out.splitlines() if line and not line[3:].startswith(".duckgrep/")]


def reset(path: Path, commit: str) -> tuple[list[str], bool]:
    """Put a worktree back exactly at `commit`, before and after every run: files (untracked and ignored ones
    too, but not the duckgrep index), HEAD, and any ref or reflog a run created. Returns what had changed and
    whether HEAD had moved."""
    found = changes(path)
    moved = git("rev-parse", "HEAD", cwd=path).strip() != commit
    git("checkout", "-q", "--force", "--detach", commit, cwd=path)
    git("clean", "-ffdxq", "-e", ".duckgrep", cwd=path)
    for ref in git("for-each-ref", "--format=%(refname)", cwd=path).split():
        git("update-ref", "-d", ref, cwd=path)
    git("reflog", "expire", "--expire=now", "--all", cwd=path)
    return found, moved


def free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1e9


def require_space(path: Path, minimum: float = config.MIN_FREE_GB) -> None:
    free = free_gb(path)
    if free < minimum:
        raise RuntimeError(f"only {free:.1f} GB free under {path}; need {minimum} GB (set DUCKGREP_EVAL_CACHE)")


def _build(what: str, argv: list[str], env: dict[str, str], partial: Path, timeout: float | None = None) -> None:
    """Run one build step of `prepare`. Whatever it starts is stopped when it ends, even what left its process
    group, and its output goes to a file, so nothing it leaves running can keep the step waiting. If it fails or
    is interrupted, `partial`, what it built so far, is removed: it would pass for a finished one next time."""
    marker = f"prepare-{uuid.uuid4().hex}"
    done = None
    with tempfile.TemporaryFile() as out:
        try:
            done = subprocess.run(
                argv,
                env={**env, setups.RUN_MARKER: marker},
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        finally:
            setups.sweep(marker)
            if done is None or done.returncode:
                shutil.rmtree(partial, ignore_errors=True)
        if done.returncode:
            out.seek(0)
            tail = out.read().decode("utf-8", "replace").strip()[-3000:]
            raise RuntimeError(f"{what} failed (exit {done.returncode}):\n{tail}")


def index_duckgrep(path: Path) -> dict:
    """Build the duckgrep index of a worktree; report the build time and index size."""
    t = time.monotonic()
    argv = [sys.executable, "-m", "duckgrep", "-C", str(path), "index"]
    _build(f"indexing {path}", argv, git_env(), path / ".duckgrep")
    db = path / ".duckgrep" / "index.duckdb"
    return {"index_seconds": round(time.monotonic() - t, 1), "index_mb": round(db.stat().st_size / 1e6, 1)}


def rust_analyzer(cache: Path) -> Path:
    """The pinned standalone rust-analyzer, plus a `rustup` stub beside it that makes Serena use it rather than
    asking rustup (whose proxy on this machine is broken, and must not be changed)."""
    bindir = cache / "bin"
    exe = bindir / "rust-analyzer"
    if not exe.exists():
        gz = datasets.download(
            config.RUST_ANALYZER_URL, cache / "downloads" / "rust-analyzer.gz", config.RUST_ANALYZER_GZ_SHA256
        )
        bindir.mkdir(parents=True, exist_ok=True)
        with gzip.open(gz) as src, open(exe, "wb") as dst:
            shutil.copyfileobj(src, dst)
        exe.chmod(0o755)
        stub = bindir / "rustup"
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)
    version = subprocess.run([str(exe), "--version"], capture_output=True, text=True).stdout.strip()
    if version != config.RUST_ANALYZER_VERSION:
        raise RuntimeError(f"unexpected rust-analyzer: {version!r}")
    return exe


def serena_warmup(path: Path, repo: str, lang: str, cache: Path) -> dict:
    """Create the Serena project with its language pinned (auto-detection asks a question on stdin when a repo
    has files in a second language) and fill its symbol cache, which lives outside the worktree."""
    (cache / "tmp").mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(dir=cache / "tmp") as tmp:
        env = {**git_env(), "SERENA_HOME": str(setups.serena_home(Path(tmp) / "home", cache))}
        env.update(setups.rust_env(cache, repo))
        argv = [shutil.which("uvx") or "uvx", "--from", config.SERENA, "serena", "project", "index", str(path)]
        argv += ["--language", lang, "--log-level", "WARNING"]
        project = serena_project_file(cache, path).parent.parent
        _build(f"warming Serena up on {path}", argv, env, project, timeout=3600)
    return {"serena_seconds": round(time.monotonic() - started, 1)}


def serena_project_file(cache: Path, path: Path) -> Path:
    return cache / "serena-projects" / path.name / ".serena" / "project.yml"


def prepare(tasks, setup_names: list[str], cache: Path, log=print, save=None) -> list[dict]:
    """Worktrees for every (repo, commit, setup), with the duckgrep index or the Serena warm-up. Returns what was
    newly built, with its cost, and hands each row to `save` as soon as it is measured, so a later failure keeps
    it; what exists already is left alone."""
    built = []
    todo = sorted({(t.repo, t.commit, t.lang) for t in tasks})
    if "serena" in setup_names and any(lang == "rust" for *_, lang in todo):
        rust_analyzer(cache)
    for repo, commit, lang in todo:
        for setup in setup_names:
            require_space(cache)
            path = worktree(repo, commit, setup, cache)
            row: dict = {}
            if setup == "duckgrep" and not (path / ".duckgrep" / "index.duckdb").exists():
                row = index_duckgrep(path)
            elif setup == "serena" and not serena_project_file(cache, path).exists():
                row = serena_warmup(path, repo, lang, cache)
            leftover = changes(path)
            if leftover and setup != "serena":
                raise RuntimeError(f"preparing {path} changed it: {leftover[:5]}")
            if leftover:  # rust-analyzer's cargo writes a Cargo.lock into a repo that commits none; runs restore it too
                reset(path, commit)
            if row:
                row = {"setup": setup, "repo": repo, "commit": commit, **row}
                if leftover:
                    row["restored"] = leftover
                built.append(row)
                if save:
                    save(row)
                log(f"prepared {setup} {repo}@{commit[:12]}: {row}")
    return built
