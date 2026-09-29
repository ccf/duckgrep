"""Drive the MCP server over stdio: list the tool, run a query, show an error.

usage: python bench/mcp_smoke.py [repo]   (default: tests/fixture)
"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "tests", "fixture")


async def main():
    params = StdioServerParameters(command="duckgrep", args=["-C", os.path.abspath(ROOT), "mcp"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            t = (await s.list_tools()).tools[0]
            print(t.name, "| description chars:", len(t.description), "| params:", list(t.inputSchema["properties"]))
            res = await s.call_tool("query", {"sql": "SELECT * FROM callers('slugify')"})
            print(res.content[0].text)
            res = await s.call_tool("query", {"sql": "SELECT nope FROM symbols"})
            print(res.content[0].text[:120])


asyncio.run(main())
