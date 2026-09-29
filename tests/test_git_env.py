"""duckgrep's own git calls ignore GIT_DIR and friends inherited from the environment."""

import subprocess

from helpers import make_repo

from duckgrep import index


def init_repo(path, files):
    root = make_repo(path, files)
    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    return root


def test_list_files_ignores_inherited_git_dir(tmp_path, monkeypatch):
    a = init_repo(tmp_path / "a", {"a.py": "x = 1\n"})
    b = init_repo(tmp_path / "b", {"b.py": "y = 1\n"})
    monkeypatch.setenv("GIT_DIR", f"{b}/.git")
    monkeypatch.setenv("GIT_WORK_TREE", b)
    assert index.list_files(a) == ["a.py"]
