"""Run a read-only query against a freshly updated index and format it compactly."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass

import duckdb

from .index import connect, freshen

MAX_CELL = 300


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    total: int
    note: str = ""
    more: bool = False


EDGE_USERS = re.compile(r"\b(edges|callers|callees)\b", re.I)


def needs_edges(sql: str) -> bool:
    return bool(EDGE_USERS.search(sql))


def refresh(root: str, **kw) -> str:
    """Bring the index up to date. Returns a one-line note ('' if nothing changed)."""
    try:
        con = connect(root)
    except duckdb.IOException:
        return "note: index locked by another process; results may be stale"
    try:
        st = freshen(con, root, **kw)
    finally:
        con.close()
    if st.dirty:
        return f"reindexed: {st.summary()}"
    return ""


def run(root: str, sql: str, max_rows: int = 200, timeout: float = 30.0, fresh: bool = True) -> Result:
    note = refresh(root, edges=needs_edges(sql)) if fresh else ""
    con = connect(root, read_only=True)
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        con.execute("SET enable_external_access = false")
        cur = con.execute(sql)
        if cur.description is None:
            return Result([], [], 0, note)
        cols = [d[0] for d in cur.description]
        max_rows = max(1, max_rows)
        rows = cur.fetchmany(max_rows + 1)
        more = len(rows) > max_rows
        rows = rows[:max_rows]
        return Result(cols, rows, len(rows), note, more)
    except duckdb.InterruptException:
        raise TimeoutError(f"query exceeded {timeout:.0f}s") from None
    finally:
        timer.cancel()
        con.close()


def _cell(v) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else str(v)
    s = s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
    return s if len(s) <= MAX_CELL else s[: MAX_CELL - 1] + "…"


def format_tsv(res: Result) -> str:
    out = []
    if res.note:
        out.append(f"# {res.note}")
    if not res.columns:
        out.append("(ok)")
        return "\n".join(out)
    out.append("\t".join(res.columns))
    out.extend("\t".join(_cell(v) for v in r) for r in res.rows)
    if res.more:
        out.append(f"# showing the first {len(res.rows)} rows; there are more. Add LIMIT/WHERE or raise max_rows")
    elif not res.rows:
        out.append("# 0 rows")
    return "\n".join(out)


def format_table(res: Result) -> str:
    if not res.columns:
        return res.note or "(ok)"
    cells = [[_cell(v) for v in r] for r in res.rows]
    widths = [min(80, max([len(c)] + [len(r[i]) for r in cells])) for i, c in enumerate(res.columns)]

    def fmt(row):
        return "  ".join(v[:w].ljust(w) for v, w in zip(row, widths, strict=True)).rstrip()

    lines = ([f"# {res.note}"] if res.note else []) + [fmt(res.columns), fmt(["-" * w for w in widths])]
    lines += [fmt(r) for r in cells]
    if res.more:
        lines.append(f"# showing the first {len(res.rows)} rows; there are more")
    return "\n".join(lines)


def format_json(res: Result) -> str:
    return json.dumps(
        {
            "columns": res.columns,
            "rows": [list(r) for r in res.rows],
            "total": res.total,
            "more": res.more,
            "note": res.note,
        },
        default=str,
    )
