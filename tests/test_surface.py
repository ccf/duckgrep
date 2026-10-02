"""The agent-facing surface: result limits, verbatim text, macro disambiguation, MCP startup, roots."""

import os
import subprocess
import threading
import time
import tracemalloc

import anyio
from helpers import make_repo, rows
from mcp.shared.memory import create_connected_server_and_client_session

from duckgrep import cli, mcp_server
from duckgrep import query as q


def test_oversized_results_stop_at_max_rows(repo):
    tracemalloc.start()
    t = time.perf_counter()
    res = q.run(repo, "SELECT * FROM range(5000000)", max_rows=10)
    elapsed = time.perf_counter() - t
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(res.rows) == 10 and res.more
    assert "there are more" in q.format_tsv(res)
    assert peak < 50_000_000 and elapsed < 5


def test_cells_keep_code_text_verbatim(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a.go": "package a\n\nfunc F() {\n\tif x := 1; x > 0 {\n\t\treturn\n\t}\n}\n",
            "b.py": 'PAT = r"\\d+\\\\"\n',
        },
    )
    out = q.format_tsv(q.run(root, "SELECT text FROM lines WHERE path IN ('a.go', 'b.py') ORDER BY path, line"))
    assert "\tif x := 1; x > 0 {" in out
    assert 'PAT = r"\\d+\\\\"' in out


TWIN = {
    "a/index.ts": "export function one() { return two(); }\nexport function two() { return 1; }\n",
    "b/index.ts": "export function three() { return 3; }\n",
    "a/check.py": "def check():\n    return helper_a()\n\n\ndef helper_a():\n    return 1\n",
    "b/check.py": "def check():\n    return helper_b()\n\n\ndef helper_b():\n    return 2\n",
}


def test_outline_names_the_file_when_ambiguous(tmp_path):
    root = make_repo(tmp_path / "r", TWIN)
    got = rows(root, "SELECT file, qualname FROM outline('index.ts')")
    assert got == [("a/index.ts", "one"), ("a/index.ts", "two"), ("b/index.ts", "three")]
    assert rows(root, "SELECT file, qualname FROM outline('a/index.ts')") == [(None, "one"), (None, "two")]


def test_callees_name_file_and_caller_when_ambiguous(tmp_path):
    root = make_repo(tmp_path / "r", TWIN)
    got = rows(root, "SELECT file, caller, name FROM callees('check')")
    assert got == [("a/check.py", "check", "helper_a"), ("b/check.py", "check", "helper_b")]


def test_source_says_which_match_it_shows(tmp_path):
    root = make_repo(tmp_path / "r", TWIN)
    got = rows(root, "SELECT file, line, text FROM source('check')")
    assert got[0] == ("a/check.py (1 of 2 matches)", 1, "def check():")
    assert all(r[0] is None for r in got[1:])


def test_serve_starts_before_the_index_is_built(repo, monkeypatch):
    from mcp.server.fastmcp import FastMCP

    started = []
    monkeypatch.setattr(q, "refresh", lambda root, **kw: time.sleep(2) or "")
    monkeypatch.setattr(FastMCP, "run", lambda self, *a, **k: started.append(time.perf_counter()))
    t = time.perf_counter()
    mcp_server.serve(repo)
    assert started and started[0] - t < 1.0


def test_first_query_waits_for_the_initial_index(repo, monkeypatch):
    q.refresh(repo)  # built up front, so a query that did not wait would answer at once
    gate = threading.Event()

    def refresh(root, **kw):
        if threading.current_thread().name == "duckgrep-index":
            gate.wait(10)  # the warm-up: blocked until the test releases it
        return ""  # later calls, from queries: nothing to do

    monkeypatch.setattr(q, "refresh", refresh)
    done = []

    async def main():
        server = mcp_server.build(repo)
        async with create_connected_server_and_client_session(server._mcp_server) as client:

            async def call():
                res = await client.call_tool("query", {"sql": "SELECT count(*) AS n FROM files"})
                done.append(res.content[0].text)

            try:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(call)
                    await anyio.sleep(0.5)
                    assert not done, f"the query answered before the index was built: {done}"
                    gate.set()
            finally:
                gate.set()

    anyio.run(main)
    assert int(done[0].splitlines()[-1]) == rows(repo, "SELECT count(*) FROM files")[0][0] > 0


def git_repo(path, files):
    root = make_repo(path, files)
    subprocess.run(["git", "init", "-q", root], check=True)
    return root


def test_explicit_root_is_used_as_given(tmp_path, capsys):
    outer = git_repo(tmp_path / "outer", {"sub/a.py": "def f():\n    pass\n", "b.py": "x = 1\n"})
    code = cli.main(["-C", os.path.join(outer, "sub"), "q", "SELECT count(*) AS n FROM files", "-f", "tsv"])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "1"


def test_missing_root_is_an_error(tmp_path, capsys):
    assert cli.main(["-C", str(tmp_path / "typo"), "q", "SELECT 1"]) == 2
    assert "no such directory" in capsys.readouterr().err
    assert not (tmp_path / "typo").exists()


def test_no_repository_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["q", "SELECT 1"]) == 2
    assert "not inside a repository" in capsys.readouterr().err


def test_mcp_root_comes_from_env(tmp_path, monkeypatch):
    target = git_repo(tmp_path / "target", {"a.py": "x = 1\n"})
    elsewhere = git_repo(tmp_path / "elsewhere", {"b.py": "y = 2\n"})
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("DUCKGREP_ROOT", target)
    seen = []
    monkeypatch.setattr(mcp_server, "serve", seen.append)
    assert cli.main(["mcp"]) == 0
    assert seen == [target]


def test_walk_fallback_skips_dotfiles(tmp_path):
    root = make_repo(tmp_path / "plain", {"a.py": "x = 1\n", ".env": "API_KEY=secret\n"})
    assert cli.main(["-C", root, "index"]) == 0
    assert rows(root, "SELECT path FROM files") == [("a.py",)]


def test_schema_doc_states_the_bare_name_and_unresolved_rules_accurately():
    from duckgrep.schema import SCHEMA_DOC

    assert (
        "A bare name resolves through its file's scope and imports only (Rust macros and .d.ts declarations excepted)."
        in SCHEMA_DOC
    )
    assert (
        "unresolved  no in-repo target found (stdlib, builtins, third party, or an import duckgrep can't follow); dst_* NULL"
        in SCHEMA_DOC
    )


def test_the_tool_description_documents_every_table_and_macro_within_its_budget():
    # every request an agent makes carries it: what it doesn't need costs on each one
    from duckgrep.mcp_server import QUERY_DOC
    from duckgrep.schema import SCHEMA_DOC

    for name in ("files", "symbols", "refs", "imports", "imports_resolved", "lines", "commits", "file_changes",
                 "file_churn", "edges", "defs(", "callers(", "callees(", "outline(", "grep(", "source("):  # fmt: skip
        assert name in SCHEMA_DOC, name
    assert len(QUERY_DOC) <= 3100, len(QUERY_DOC)
