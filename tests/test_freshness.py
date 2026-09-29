"""Freshness under interruption, logic changes and killed parents."""

import os
import subprocess
import sys
import time

import pytest
from helpers import fresh_snapshot, make_repo, rows, snapshot, write

from duckgrep import index, schema


def lib_repo(tmp_path, n=40):
    files = {"lib/__init__.py": "", "lib/core.py": "def helper():\n    return 1\n"}
    for i in range(n):
        files[f"m/f{i}.py"] = f"from lib.core import helper\n\n\ndef fn{i}():\n    return helper()\n"
    return make_repo(tmp_path / "repo", files)


def rename_helper(root, n_callers):
    write(root, "lib/core.py", "def helper2():\n    return 1\n")
    for i in range(n_callers):
        write(root, f"m/f{i}.py", f"from lib.core import helper2\n\n\ndef fn{i}():\n    return helper2()\n")


def test_interrupted_edge_rebuild_is_redone(tmp_path, monkeypatch):
    root = lib_repo(tmp_path)
    rows(root, "SELECT 1")
    rename_helper(root, 16)  # 17 of 42 files changed: the >30% path, which rebuilds every edge
    monkeypatch.setattr(index, "EDGE_BATCH_REFS", 20)  # several batches, each committed
    real, calls = index._compute_edges, []

    def fail_second_batch(con, source, where="TRUE"):
        calls.append(where)
        if len(calls) == 2:
            raise RuntimeError("killed")
        real(con, source, where)

    monkeypatch.setattr(index, "_compute_edges", fail_second_batch)
    with pytest.raises(RuntimeError, match="killed"):
        rows(root, "SELECT 1")
    monkeypatch.undo()
    assert snapshot(root) == fresh_snapshot(root, tmp_path)


def test_interrupted_parse_on_rebuild_path_is_redone(tmp_path, monkeypatch):
    root = lib_repo(tmp_path)
    rows(root, "SELECT 1")
    rename_helper(root, 10)
    for i in range(30, 34):  # the deletions push the change over 30%
        os.remove(os.path.join(root, f"m/f{i}.py"))
    monkeypatch.setattr(index, "CHUNK", 4)
    real, calls = index._work, []

    def fail_in_second_chunk(args):
        calls.append(args[1])
        if len(calls) == 6:
            raise RuntimeError("killed")
        return real(args)

    monkeypatch.setattr(index, "_work", fail_in_second_chunk)
    with pytest.raises(RuntimeError, match="killed"):
        rows(root, "SELECT 1")
    monkeypatch.undo()
    assert snapshot(root) == fresh_snapshot(root, tmp_path)


def test_edge_logic_change_rebuilds_edges(tmp_path, monkeypatch):
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "class A:\n    def run(self):\n        pass\n",
            "b.py": "class B:\n    def run(self):\n        pass\n",
            "c.py": "def go(x):\n    x.run()\n",
        },
    )
    q = "SELECT resolution FROM edges WHERE src_path = 'c.py' AND name = 'run'"
    assert rows(root, q) == [("name",), ("name",)]
    monkeypatch.setattr(schema, "NAME_CAP", 1)  # 2 candidates is now over the cap
    assert rows(root, q) == [("ambiguous",)]


def test_grammar_upgrade_reparses(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "r", {"a.py": "def f():\n    pass\n"})
    rows(root, "SELECT 1")
    monkeypatch.setattr(index, "_grammar_versions", lambda: "tree-sitter-python==99")
    con = index.connect(root)
    try:
        st = index.freshen(con, root)
    finally:
        con.close()
    assert st.parsed == 1


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_pool_workers_exit_when_parent_dies(tmp_path):
    script = tmp_path / "parent.py"
    script.write_text(
        "import os, time\n"
        "from duckgrep import index\n"
        "ex = index._pool(2)\n"
        "futures = [ex.submit(time.sleep, 60) for _ in range(2)]\n"
        "time.sleep(1.5)\n"
        "print(' '.join(str(p) for p in ex._processes), flush=True)\n"
        "os._exit(0)\n"
    )
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60).stdout
    pids = [int(p) for p in out.split()]
    assert pids
    deadline = time.time() + 10
    while time.time() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.2)
    assert not any(_alive(p) for p in pids)
