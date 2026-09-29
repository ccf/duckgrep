"""The agent-facing surface: result limits, verbatim text, macro disambiguation, MCP startup, roots."""

import time
import tracemalloc

from helpers import make_repo

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
