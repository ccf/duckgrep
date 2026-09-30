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
