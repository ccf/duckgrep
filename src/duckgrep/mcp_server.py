"""MCP server exposing the index as one read-only SQL tool.

The whole schema is in the tool description, so the agent can write a correct
query on its first call without a discovery round-trip.
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import query as q
from .index import find_root
from .schema import SCHEMA_DOC

QUERY_DOC = f"""Query the codebase index with DuckDB SQL (read-only). Prefer this over grep/find for
locating definitions, callers, imports, and anything structural. Results are tab-separated.
The index is refreshed incrementally before each call, so it reflects your latest edits.

{SCHEMA_DOC}"""


def build(root: str) -> FastMCP:
    mcp = FastMCP("duckgrep")

    @mcp.tool(description=QUERY_DOC)
    def query(sql: str, max_rows: int = 100) -> str:
        try:
            return q.format_tsv(q.run(root, sql, max_rows=max_rows))
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"

    return mcp


def serve(root: str | None = None) -> None:
    root = root or find_root(os.environ.get("DUCKGREP_ROOT"))
    q.refresh(root)  # warm the index before the first call
    build(root).run()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
