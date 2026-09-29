"""MCP server exposing the index as one read-only SQL tool.

The whole schema is in the tool description, so the agent can write a correct
query on its first call without a discovery round-trip.
"""

from __future__ import annotations

import os
import threading

import anyio
from mcp.server.fastmcp import FastMCP

from . import query as q
from .index import find_root
from .schema import SCHEMA_DOC

QUERY_DOC = f"""Query the codebase index with DuckDB SQL (read-only). Prefer this over grep/find for
locating definitions, callers, imports, and anything structural. Results are tab-separated; cell text is verbatim (tabs and backslashes
included) except newlines, shown as ⏎, and cells over 300 characters, cut with ….
The index is refreshed incrementally before each call, so it reflects your latest edits.

{SCHEMA_DOC}"""


def build(root: str) -> FastMCP:
    """The server. The index is built on a background thread while the handshake is answered; the first
    query waits for it."""
    mcp = FastMCP("duckgrep")
    ready = threading.Event()

    def warm():
        try:
            q.refresh(root)
        finally:
            ready.set()

    threading.Thread(target=warm, name="duckgrep-index", daemon=True).start()

    def answer(sql: str, max_rows: int) -> str:
        ready.wait()
        try:
            return q.format_tsv(q.run(root, sql, max_rows=max_rows))
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"

    @mcp.tool(description=QUERY_DOC)
    async def query(sql: str, max_rows: int = 100) -> str:
        return await anyio.to_thread.run_sync(answer, sql, max_rows)

    return mcp


def serve(root: str | None = None) -> None:
    root = root or find_root(os.environ.get("DUCKGREP_ROOT"))
    build(root).run()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
