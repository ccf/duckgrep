"""Latency of the things an agent actually waits on: freshen (no-op and after one edit) and typical queries.

usage: python bench/latency.py <repo> <file-to-edit> <symbol> <qualname>
"""

import statistics
import sys
import time

from duckgrep import query as q
from duckgrep.index import connect, freshen, sync_edges


def timed(fn, n=5):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        out = fn()
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts), out


def main(root, edit_path, symbol, qualname):
    def noop(edges=True):
        con = connect(root)
        try:
            return freshen(con, root, edges=edges)
        finally:
            con.close()

    def sync():
        con = connect(root)
        try:
            return sync_edges(con)
        finally:
            con.close()

    ms, st = timed(noop)
    print(f"no-op freshen ({st.scanned} files)            {ms:8.1f} ms")

    full = f"{root}/{edit_path}"
    original = open(full).read()
    edits, syncs = [], []
    try:
        for i in range(5):
            with open(full, "w") as f:
                if full.endswith(".py"):
                    f.write(original + f"\n\ndef _bench_fn_{i}():\n    return {symbol}()\n")
                else:
                    f.write(original + f"\n\nexport function benchFn{i}() {{ return {symbol}(); }}\n")
            t = time.perf_counter()
            st = noop(edges=False)
            edits.append((time.perf_counter() - t) * 1000)
            assert st.changed == 1, st
            t = time.perf_counter()
            n = sync()
            syncs.append((time.perf_counter() - t) * 1000)
    finally:
        with open(full, "w") as f:
            f.write(original)
        noop()
    print(f"freshen after editing 1 file              {statistics.median(edits):8.1f} ms")
    print(f"  + call-graph sync (only if query needs it){statistics.median(syncs):7.1f} ms   ({n} refs)")

    queries = {
        "defs(symbol)": f"SELECT * FROM defs('{symbol}')",
        "callers(qualname), confident": f"SELECT * FROM callers('{qualname}') WHERE resolution <> 'name'",
        "grep(literal)": f"SELECT * FROM grep('{symbol}')",
        "callees(qualname)": f"SELECT * FROM callees('{qualname}')",
        "2-hop transitive callers": f"""
            WITH RECURSIVE up(q, d) AS (SELECT '{qualname}', 0 UNION
              SELECT e.src_scope, d + 1 FROM edges e JOIN up ON e.dst_qualname = up.q
              WHERE d < 2 AND e.resolution <> 'name' AND e.src_scope <> '')
            SELECT count(DISTINCT q) FROM up""",
        "unreferenced functions (whole repo)": """
            SELECT count(*) FROM symbols s WHERE s.kind = 'function'
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.dst_path = s.path AND e.dst_qualname = s.qualname)""",
        "churn x classes": """
            SELECT c.path, c.n_commits FROM file_churn c
            WHERE c.path IN (SELECT path FROM symbols WHERE kind = 'class') ORDER BY 2 DESC LIMIT 10""",
    }
    for name, sql in queries.items():
        ms, res = timed(lambda sql=sql: q.run(root, sql, max_rows=1000, fresh=False), n=3)
        print(f"{name:42s}{ms:8.1f} ms   rows={res.total}")


if __name__ == "__main__":
    main(*sys.argv[1:])
