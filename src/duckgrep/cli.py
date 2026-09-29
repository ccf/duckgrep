"""duckgrep command line."""

from __future__ import annotations

import argparse
import sys

from . import query as q
from .index import connect, find_root, freshen
from .schema import SCHEMA_DOC


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


SHORTCUTS = {
    "def": ("defs", "where a symbol is defined"),
    "callers": ("callers", "who references a symbol"),
    "callees": ("callees", "what a function references"),
    "outline": ("outline", "symbols in a file"),
    "grep": ("grep", "regex search with enclosing symbol"),
    "source": ("source", "print a symbol's code"),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="duckgrep", description="DuckDB index of a codebase for coding agents")
    ap.add_argument("-C", "--root", help="repo root (default: nearest .git / .duckgrep above cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="build or update the index")
    p.add_argument("--full", action="store_true", help="re-hash and re-parse every file")
    p.add_argument("--no-git", action="store_true", help="skip git history")

    for name in ("q", "query"):
        p = sub.add_parser(name, help="run SQL against the index")
        p.add_argument("sql")
        _common(p)

    for name, (_, help_) in SHORTCUTS.items():
        p = sub.add_parser(name, help=help_)
        p.add_argument("arg")
        _common(p)

    sub.add_parser("schema", help="print the schema and examples")
    sub.add_parser("mcp", help="run the MCP server on stdio")

    a = ap.parse_args(argv)
    root = find_root(a.root)

    if a.cmd == "schema":
        print(SCHEMA_DOC)
        return 0
    if a.cmd == "mcp":
        from .mcp_server import serve

        serve(root)
        return 0
    if a.cmd == "index":
        con = connect(root)
        try:
            st = freshen(con, root, full=a.full, git_history=not a.no_git)
            n = con.execute(
                "SELECT (SELECT count(*) FROM files), (SELECT count(*) FROM symbols), "
                "(SELECT count(*) FROM refs), (SELECT count(*) FROM commits)"
            ).fetchone()
        finally:
            con.close()
        print(st.summary())
        print(f"index: {n[0]} files, {n[1]} symbols, {n[2]} refs, {n[3]} commits")
        return 0

    if a.cmd in ("q", "query"):
        sql = a.sql
    else:
        macro = SHORTCUTS[a.cmd][0]
        sql = f"SELECT * FROM {macro}({_sql_str(a.arg)})"
    try:
        res = q.run(root, sql, max_rows=a.max_rows, fresh=not a.no_freshen)
    except Exception as e:  # show SQL errors plainly
        print(f"error: {e}", file=sys.stderr)
        return 1
    fmt = {"tsv": q.format_tsv, "table": q.format_table, "json": q.format_json}[a.format]
    print(fmt(res))
    return 0


def _common(p):
    p.add_argument("-n", "--max-rows", type=int, default=200)
    p.add_argument("-f", "--format", choices=["table", "tsv", "json"], default="table")
    p.add_argument("--no-freshen", action="store_true", help="skip the incremental update")


if __name__ == "__main__":
    sys.exit(main())
