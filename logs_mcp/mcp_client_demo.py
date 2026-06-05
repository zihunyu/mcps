"""Call Log MCP tools through the MCP Python client."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from pprint import pprint

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT_DIR = Path(__file__).resolve().parent


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Demo MCP client for Log MCP")
    parser.add_argument("--server-id", default="local-demo-01")
    parser.add_argument("--log-name", default="demo-log")
    parser.add_argument("--lines", type=int, default=20)
    parser.add_argument("--keyword", default=None)
    parser.add_argument("--download", action="store_true", help="Call download_log instead of read_log")
    args = parser.parse_args(argv)

    asyncio.run(call_mcp(args.server_id, args.log_name, args.lines, args.keyword, args.download))


async def call_mcp(server_id: str, log_name: str, lines: int, keyword: str | None, download: bool) -> None:
    env = os.environ.copy()
    env.setdefault("LOG_MCP_CONFIG", str(ROOT_DIR / "mcp" / "config.yaml"))
    params = StdioServerParameters(
        command="python",
        args=["run.py"],
        cwd=ROOT_DIR,
        env=env,
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            print("MCP tools:")
            tools = await session.list_tools()
            for tool in tools.tools:
                print(f"- {tool.name}")

            print("\nlist_log_servers result:")
            pprint(await session.call_tool("list_log_servers", {}))

            print("\nlist_server_logs result:")
            pprint(await session.call_tool("list_server_logs", {"server_id": server_id}))

            tool_name = "download_log" if download else "read_log"
            print(f"\n{tool_name} result:")
            pprint(
                await session.call_tool(
                    tool_name,
                    {
                        "server_id": server_id,
                        "log_name": log_name,
                        "lines": lines,
                        "keyword": keyword,
                    },
                )
            )


if __name__ == "__main__":
    main()
