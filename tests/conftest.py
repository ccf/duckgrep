import os
import shutil

import pytest
from helpers import FIXTURE


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """A commit hook exports GIT_DIR / GIT_INDEX_FILE. Git run by a test (or by duckgrep inside one) would then
    act on the outer repo instead of the test's temp repo, and `git init` would re-initialise it as bare."""
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns(".duckgrep"))
    return str(root)
