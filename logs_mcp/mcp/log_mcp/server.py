"""Log MCP executable entrypoint."""

from __future__ import annotations

import argparse
import anyio
import logging
from collections.abc import Sequence

import uvicorn

from .auth import protect_http_app
from .config import AppConfig, CenterConfig, load_config
from .tools import create_mcp_server


def main(argv: Sequence[str] | None = None) -> None:
    """Run the Log MCP server."""

    parser = argparse.ArgumentParser(description="Start Log MCP server")
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print registered MCP tool names and exit",
    )
    args = parser.parse_args(argv)

    config = _tool_listing_config() if args.list_tools else load_config()
    logging.basicConfig(
        level=getattr(logging, config.mcp.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = create_mcp_server(config)

    if args.list_tools:
        tool_names = sorted(server._tool_manager._tools.keys())
        print("Registered MCP tools:")
        for tool_name in tool_names:
            print(f"- {tool_name}")
        return

    if config.mcp.transport == "stdio":
        server.run(transport=config.mcp.transport)
        return

    token = config.auth.bearer_token
    if token:
        logging.info("mcp_bearer_token enabled transport=%s", config.mcp.transport)
    else:
        logging.warning("mcp_bearer_token disabled transport=%s", config.mcp.transport)

    if config.mcp.transport == "streamable-http":
        app = protect_http_app(server.streamable_http_app(), token)
    else:
        app = protect_http_app(server.sse_app(), token)
    anyio.run(_serve_http_app, app, config)


def _tool_listing_config() -> AppConfig:
    """Return a placeholder config for offline tool introspection."""

    return AppConfig(
        center=CenterConfig(
            base_url="http://127.0.0.1:8000",
            api_token="list-tools-token",
        )
    )


async def _serve_http_app(app: object, config: AppConfig) -> None:
    server_config = uvicorn.Config(
        app,
        host=config.mcp.host,
        port=config.mcp.port,
        log_level=config.mcp.log_level.lower(),
    )
    http_server = uvicorn.Server(server_config)
    await http_server.serve()


if __name__ == "__main__":
    main()
