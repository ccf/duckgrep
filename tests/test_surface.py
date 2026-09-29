"""The agent-facing surface: result limits, verbatim text, macro disambiguation, MCP startup, roots."""

import time
import tracemalloc

from helpers import make_repo, rows

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
