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


def test_restore_reverts_what_a_run_changed_but_keeps_ignored_files(tmp_path):
    src, commit = origin(tmp_path, {**FILES, ".gitignore": ".cache/\n"})
    wt = workspace.worktree("o/r", commit, "baseline", tmp_path / "cache", url=str(src))
    (wt / "pkg/a.py").write_text("changed\n")
    (wt / "notes.txt").write_text("scratch\n")
    (wt / ".cache").mkdir()
    (wt / ".cache/x").write_text("kept\n")
    assert sorted(workspace.restore(wt)) == [" M pkg/a.py", "?? notes.txt"]
    assert (wt / "pkg/a.py").read_text() == FILES["pkg/a.py"] and not (wt / "notes.txt").exists()
    assert (wt / ".cache/x").exists() and workspace.restore(wt) == []


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
