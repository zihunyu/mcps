"""FastMCP tool registration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response

from .client import CenterClient, CenterClientError
from .config import AppConfig
from .downloads import DownloadRegistry, save_downloaded_log
from .models import CreateTaskRequest, DownloadLogResponse, ReadLogResponse, ToolError

logger = logging.getLogger(__name__)


CenterClientFactory = Callable[[], CenterClient]


def create_mcp_server(
    config: AppConfig,
    client_factory: CenterClientFactory | None = None,
) -> FastMCP:
    """Create and configure the FastMCP server."""

    mcp = FastMCP(
        "Log MCP",
        host=config.mcp.host,
        port=config.mcp.port,
        log_level=config.mcp.log_level,
    )
    make_client = client_factory or (lambda: CenterClient(config.center))
    download_registry = DownloadRegistry(config.download.token_ttl_seconds)

    @mcp.custom_route("/downloads/{token}", methods=["GET"], include_in_schema=False)
    async def download_saved_log(request: Request) -> Response:
        """Download a saved log file by temporary token."""

        token = request.path_params.get("token", "")
        record = download_registry.get(token)
        if record is None:
            return PlainTextResponse("download not found", status_code=404)
        logger.info(
            "download_route token_present=%s file_name=%s size_bytes=%s",
            bool(token),
            record.file_name,
            record.size_bytes,
        )
        return FileResponse(
            record.file_path,
            filename=record.file_name,
            media_type="text/plain",
        )

    @mcp.tool()
    async def list_log_servers() -> list[dict[str, Any]] | dict[str, str]:
        """List servers registered in Log Center."""

        logger.info("tool_call tool=list_log_servers")
        try:
            async with make_client() as client:
                servers = await client.list_servers()
            logger.info("tool_result tool=list_log_servers count=%s", len(servers))
            return [server.model_dump(exclude_none=True) for server in servers]
        except Exception as exc:
            return _tool_error("list_log_servers", exc)

    @mcp.tool()
    async def list_server_logs(server_id: str) -> list[dict[str, Any]] | dict[str, str]:
        """List logs registered for a server."""

        logger.info("tool_call tool=list_server_logs server_id=%s", server_id)
        try:
            async with make_client() as client:
                logs = await client.list_server_logs(server_id)
            logger.info("tool_result tool=list_server_logs server_id=%s count=%s", server_id, len(logs))
            return [log.model_dump(exclude_none=True) for log in logs]
        except Exception as exc:
            return _tool_error("list_server_logs", exc)

    @mcp.tool()
    async def read_log(
        server_id: str,
        log_name: str,
        lines: int | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """Read recent log lines through Log Center."""

        requested_lines = lines if lines is not None else config.limits.default_lines
        logger.info(
            "tool_call tool=read_log server_id=%s log_name=%s lines=%s keyword_present=%s",
            server_id,
            log_name,
            requested_lines,
            bool(keyword),
        )

        try:
            if requested_lines > config.limits.max_lines:
                raise ValueError(f"lines must be <= {config.limits.max_lines}")

            request = CreateTaskRequest(
                server_id=server_id,
                log_name=log_name,
                lines=requested_lines,
                keyword=keyword,
            )
            async with make_client() as client:
                result = await client.read_log(request)

            logger.info(
                "tool_result tool=read_log server_id=%s log_name=%s task_id=%s status=%s line_count=%s",
                request.server_id,
                request.log_name,
                result.task_id,
                result.status,
                len(result.lines),
            )
            return ReadLogResponse(
                task_id=result.task_id,
                status=result.status,
                lines=result.lines,
            ).model_dump()
        except Exception as exc:
            return _tool_error("read_log", exc)

    @mcp.tool()
    async def download_log(
        server_id: str,
        log_name: str,
        lines: int | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """Download recent log lines to a local file without returning log content."""

        requested_lines = lines if lines is not None else config.limits.default_lines
        logger.info(
            "tool_call tool=download_log server_id=%s log_name=%s lines=%s keyword_present=%s",
            server_id,
            log_name,
            requested_lines,
            bool(keyword),
        )

        try:
            if requested_lines > config.limits.max_lines:
                raise ValueError(f"lines must be <= {config.limits.max_lines}")

            request = CreateTaskRequest(
                server_id=server_id,
                log_name=log_name,
                lines=requested_lines,
                keyword=keyword,
            )
            async with make_client() as client:
                result = await client.read_log(request)

            if result.status != "finished":
                raise CenterClientError(f"Task {result.task_id} ended with status {result.status}")

            file_path, size_bytes = save_downloaded_log(
                config.download.dir,
                request.server_id,
                request.log_name,
                result,
            )
            download_record = download_registry.register(
                file_path=file_path,
                line_count=len(result.lines),
                size_bytes=size_bytes,
            )
            download_url = f"{config.download.public_base_url}/downloads/{download_record.token}"

            logger.info(
                (
                    "tool_result tool=download_log server_id=%s log_name=%s task_id=%s "
                    "file_path=%s line_count=%s size_bytes=%s expires_at=%s"
                ),
                request.server_id,
                request.log_name,
                result.task_id,
                file_path,
                len(result.lines),
                size_bytes,
                download_record.expires_at_iso,
            )
            return DownloadLogResponse(
                task_id=result.task_id,
                status=result.status,
                file_path=str(file_path),
                download_url=download_url,
                expires_at=download_record.expires_at_iso,
                line_count=len(result.lines),
                size_bytes=size_bytes,
            ).model_dump()
        except Exception as exc:
            return _tool_error("download_log", exc)

    return mcp


def _tool_error(tool_name: str, exc: Exception) -> dict[str, str]:
    if isinstance(exc, ValidationError):
        detail = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
    elif isinstance(exc, (ValueError, CenterClientError)):
        detail = str(exc)
    else:
        detail = "Unexpected tool error"
        logger.exception("tool_error tool=%s", tool_name)
    logger.warning("tool_error tool=%s detail=%s", tool_name, detail)
    return ToolError(error=tool_name, detail=detail).model_dump()
