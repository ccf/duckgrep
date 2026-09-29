"""The agent-facing surface: result limits, verbatim text, macro disambiguation, MCP startup, roots."""

import time
import tracemalloc

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
