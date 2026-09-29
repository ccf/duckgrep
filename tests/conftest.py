import shutil

import pytest
from helpers import FIXTURE


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns(".duckgrep"))
    return str(root)
