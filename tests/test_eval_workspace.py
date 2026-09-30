import os
import shutil
import subprocess
import sys
import time

import pytest
from eval_helpers import origin

from bench.eval import workspace
from bench.eval.suite import Task

FILES = {"pkg/__init__.py": "", "pkg/a.py": "def f():\n    return 1\n"}


def test_each_setup_gets_its_own_neutrally_named_worktree(tmp_path):
    src, commit = origin(tmp_path, FILES)
    cache = tmp_path / "cache"
    paths = {s: workspace.worktree("o/r", commit, s, cache, url=str(src)) for s in ("baseline", "duckgrep", "serena")}
    assert len(set(paths.values())) == 3
    for p in paths.values():
        assert (p / "pkg/a.py").read_text() == FILES["pkg/a.py"]
        assert not {"baseline", "duckgrep", "serena", "eval"} & set(str(p.relative_to(cache)).replace("/", " ").split())
    assert workspace.worktree("o/r", commit, "baseline", cache, url=str(src)) == paths["baseline"]  # made once


def commit_all(root, message):
    workspace.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", message, cwd=root)
    return workspace.git("rev-parse", "HEAD", cwd=root).strip()


def test_a_worktree_holds_no_history_after_its_commit(tmp_path):
    src, task_commit = origin(tmp_path, FILES)
    (src / "pkg/a.py").write_text("def f():\n    return 2\n")
    commit_all(src, "fix: the answer")
    workspace.git("tag", "v2", cwd=src)
    workspace.git("branch", "later", cwd=src)
    wt = workspace.worktree("o/r", task_commit, "baseline", tmp_path / "cache", url=str(src))
    assert workspace.git("rev-list", "--all", cwd=wt).split() == [task_commit]
    assert workspace.git("for-each-ref", cwd=wt) == ""
    assert "the answer" not in workspace.git("log", "--all", "--oneline", cwd=wt)


def test_reset_puts_back_the_commit_files_and_refs_but_keeps_the_index(tmp_path):
    src, _ = origin(tmp_path, {**FILES, ".gitignore": "__pycache__/\n"})
    (src / "pkg/a.py").write_text("def f():\n    return 2\n")
    task_commit = commit_all(src, "second")
    wt = workspace.worktree("o/r", task_commit, "baseline", tmp_path / "cache", url=str(src))
    (wt / ".duckgrep").mkdir()
    (wt / ".duckgrep/.gitignore").write_text("*\n")
    (wt / ".duckgrep/index.duckdb").write_text("index")
    # what an agent's Bash could leave behind
    workspace.git("checkout", "-q", "--detach", "HEAD~1", cwd=wt)
    workspace.git("branch", "scratch", cwd=wt)
    (wt / "pkg/a.py").write_text("edited\n")
    (wt / "notes.txt").write_text("scratch\n")
    (wt / "__pycache__").mkdir()
    (wt / "__pycache__/a.pyc").write_text("x")
    found, moved = workspace.reset(wt, task_commit)
    assert moved and {" M pkg/a.py", "?? notes.txt", "!! __pycache__/a.pyc"} <= set(found)
    assert not any(".duckgrep" in line for line in found)
    assert workspace.git("rev-parse", "HEAD", cwd=wt).strip() == task_commit
    assert (wt / "pkg/a.py").read_text() == "def f():\n    return 2\n"
    assert not (wt / "notes.txt").exists() and not (wt / "__pycache__").exists()
    assert workspace.git("for-each-ref", cwd=wt) == ""
    assert (wt / ".duckgrep/index.duckdb").read_text() == "index"
    assert workspace.reset(wt, task_commit) == ([], False)


def test_require_space(tmp_path):
    workspace.require_space(tmp_path, 0.001)
    with pytest.raises(RuntimeError, match="DUCKGREP_EVAL_CACHE"):
        workspace.require_space(tmp_path, 10**9)


def test_prepare_builds_the_duckgrep_index_once(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, FILES)
    real = workspace.worktree
    monkeypatch.setattr(workspace, "worktree", lambda repo, c, s, cache: real(repo, c, s, cache, url=str(src)))
    task = Task("t", "localization", "python", "o/r", commit, "p", ("pkg/a.py:f",), "s")
    built = workspace.prepare([task], ["baseline", "duckgrep"], tmp_path / "cache", log=lambda _: None)
    assert [b["setup"] for b in built] == ["duckgrep"] and built[0]["index_mb"] > 0
    wt = workspace.worktree_path(tmp_path / "cache", "duckgrep", "o/r", commit)
    assert (wt / ".duckgrep" / "index.duckdb").exists() and workspace.changes(wt) == []
    assert workspace.prepare([task], ["baseline", "duckgrep"], tmp_path / "cache", log=lambda _: None) == []


def test_prepare_saves_each_row_before_a_later_step_fails(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, FILES)
    real = workspace.worktree
    monkeypatch.setattr(workspace, "worktree", lambda repo, c, s, cache: real(repo, c, s, cache, url=str(src)))

    def index(path):
        if path.name.startswith("o__b"):
            raise RuntimeError("indexing o/b failed")
        return {"index_seconds": 1.0, "index_mb": 0.1}

    monkeypatch.setattr(workspace, "index_duckgrep", index)
    tasks = [Task(r, "localization", "python", f"o/{r}", commit, "p", ("pkg/a.py:f",), "s") for r in "ab"]
    saved = []
    with pytest.raises(RuntimeError, match="indexing o/b failed"):
        workspace.prepare(tasks, ["duckgrep"], tmp_path / "cache", log=lambda _: None, save=saved.append)
    assert [(r["setup"], r["repo"], r["index_seconds"]) for r in saved] == [("duckgrep", "o/a", 1.0)]


def fake_uvx(tmp_path, monkeypatch, body: str) -> None:
    """A `uvx` first on PATH that runs `body`, with the warmed-up worktree as `path`, in place of Serena."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    exe = bindir / "uvx"
    exe.write_text(
        f"#!{sys.executable}\nimport pathlib, subprocess, sys\n"
        "path = pathlib.Path(sys.argv[sys.argv.index('index') + 1])\n" + body + "\n"
    )
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def gone(pid: int, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_a_serena_warmup_that_leaves_a_file_behind_is_restored_and_noted(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, FILES)
    cache = tmp_path / "cache"
    real = workspace.worktree
    monkeypatch.setattr(workspace, "worktree", lambda repo, c, s, cache: real(repo, c, s, cache, url=str(src)))
    wt = workspace.worktree_path(cache, "serena", "o/r", commit)
    project = workspace.serena_project_file(cache, wt)
    fake_uvx(  # like rust-analyzer's cargo writing a Cargo.lock into a repo that commits none
        tmp_path,
        monkeypatch,
        f"p = pathlib.Path({str(project)!r}); p.parent.mkdir(parents=True); p.write_text('')\n"
        "(path / 'Cargo.lock').write_text('')",
    )
    task = Task("t", "localization", "python", "o/r", commit, "p", ("pkg/a.py:f",), "s")
    built = workspace.prepare([task], ["serena"], cache, log=lambda _: None)
    assert built[0]["restored"] == ["?? Cargo.lock"] and "serena_seconds" in built[0]
    assert workspace.changes(wt) == []


def test_a_serena_worktree_changed_before_preparing_is_an_error(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, FILES)
    cache = tmp_path / "cache"
    real = workspace.worktree
    monkeypatch.setattr(workspace, "worktree", lambda repo, c, s, cache: real(repo, c, s, cache, url=str(src)))
    wt = real("o/r", commit, "serena", cache, url=str(src))
    project = workspace.serena_project_file(cache, wt)
    project.parent.mkdir(parents=True)
    project.write_text("")  # warmed up already, so only a warm-up's own leftovers are restored
    (wt / "notes.txt").write_text("")  # say, from a run killed before it could restore its worktree
    task = Task("t", "localization", "python", "o/r", commit, "p", ("pkg/a.py:f",), "s")
    with pytest.raises(RuntimeError, match="notes.txt"):
        workspace.prepare([task], ["serena"], cache, log=lambda _: None)


def test_a_serena_warmup_that_hangs_is_stopped_and_shows_its_output(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, FILES)
    cache = tmp_path / "cache"
    wt = workspace.worktree("o/r", commit, "serena", cache, url=str(src))
    project = workspace.serena_project_file(cache, wt)
    fake_uvx(
        tmp_path,
        monkeypatch,
        f"p = pathlib.Path({str(project)!r}); p.parent.mkdir(parents=True); p.write_text('')\n"
        "print('indexed 40 of 100 files', flush=True)\nimport time; time.sleep(60)",
    )
    monkeypatch.setattr(workspace, "SERENA_TIMEOUT_S", 1)
    with pytest.raises(RuntimeError, match="(?s)1 s.*indexed 40 of 100 files"):
        workspace.serena_warmup(wt, "o/r", "python", cache)
    assert not project.exists()


def test_a_duckgrep_worktree_that_preparing_changed_is_an_error(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, FILES)
    real = workspace.worktree
    monkeypatch.setattr(workspace, "worktree", lambda repo, c, s, cache: real(repo, c, s, cache, url=str(src)))

    def index(path):
        (path / "stray.txt").write_text("")
        return {"index_seconds": 1.0, "index_mb": 0.1}

    monkeypatch.setattr(workspace, "index_duckgrep", index)
    task = Task("t", "localization", "python", "o/r", commit, "p", ("pkg/a.py:f",), "s")
    with pytest.raises(RuntimeError, match="stray.txt"):
        workspace.prepare([task], ["duckgrep"], tmp_path / "cache", log=lambda _: None)


@pytest.mark.parametrize("fails", [False, True])
def test_a_serena_warmup_leaves_no_process_and_keeps_only_a_finished_project(tmp_path, monkeypatch, fails):
    src, commit = origin(tmp_path, FILES)
    cache = tmp_path / "cache"
    wt = workspace.worktree("o/r", commit, "serena", cache, url=str(src))
    project = workspace.serena_project_file(cache, wt)
    pidfile = tmp_path / "server.pid"
    fake_uvx(
        tmp_path,
        monkeypatch,
        f"p = pathlib.Path({str(project)!r}); p.parent.mkdir(parents=True); p.write_text('')\n"
        # like Serena starting its language server in a session of its own
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'], start_new_session=True)\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        + ("sys.exit('the language server crashed')" if fails else ""),
    )
    started = time.monotonic()
    try:
        if fails:
            with pytest.raises(RuntimeError, match="the language server crashed"):
                workspace.serena_warmup(wt, "o/r", "python", cache)
        else:
            assert workspace.serena_warmup(wt, "o/r", "python", cache)["serena_seconds"] >= 0
        # the server holds Serena's output open, which must not keep the warm-up waiting for it
        assert time.monotonic() - started < 30
        assert project.exists() is not fails  # a half-built project would pass for a finished one next time
        assert gone(int(pidfile.read_text())), "the language server outlived the warm-up"
    finally:
        try:
            os.kill(int(pidfile.read_text()), 9)
        except (ProcessLookupError, FileNotFoundError):
            pass


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="needs git-lfs")
def test_a_worktree_checks_out_lfs_files_as_their_pointers(tmp_path):
    # a worktree has no remote to download LFS content from, and no task needs it (lakehq/sail tracks images)
    src = tmp_path / "origin"
    src.mkdir()
    (src / ".gitattributes").write_text("*.png filter=lfs diff=lfs merge=lfs -text\n")
    (src / "banner.png").write_bytes(b"\x89PNG not really")
    (src / "a.py").write_text("x = 1\n")
    lfs = [f"filter.lfs.{k}={v}" for k, v in (("clean", "git-lfs clean -- %f"), ("smudge", "git-lfs smudge -- %f"))]
    lfs += ["filter.lfs.process=git-lfs filter-process", "filter.lfs.required=true"]
    git = ["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t"] + [a for c in lfs for a in ("-c", c)]
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    commit = workspace.git("rev-parse", "HEAD", cwd=src).strip()
    wt = workspace.worktree("o/r", commit, "baseline", tmp_path / "cache", url=str(src))
    assert (wt / "banner.png").read_text().startswith("version https://git-lfs.github.com/spec/v1")
    assert workspace.changes(wt) == []


def test_a_commit_the_clone_lacks_is_fetched_by_id(tmp_path):
    src, first = origin(tmp_path, FILES)
    cache = tmp_path / "cache"
    workspace.worktree("o/r", first, "baseline", cache, url=str(src))  # clones
    (src / "pkg/a.py").write_text("def f():\n    return 2\n")
    git = ["-c", "user.name=t", "-c", "user.email=t@t"]
    workspace.git(*git, "commit", "-q", "-am", "second", cwd=src)
    second = workspace.git("rev-parse", "HEAD", cwd=src).strip()
    wt = workspace.worktree("o/r", second, "baseline", cache, url=str(src))
    assert (wt / "pkg/a.py").read_text() == "def f():\n    return 2\n"
