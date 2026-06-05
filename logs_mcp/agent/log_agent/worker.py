"""Log Agent worker loop."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from .client import AgentClient
from .config import AgentSettings, load_config
from .models import AgentTask, TaskResultRequest
from .reader import read_tail_lines

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Start Log Agent")
    parser.add_argument("--once", action="store_true", help="Run one heartbeat/task cycle and exit")
    args = parser.parse_args(argv)

    settings = load_config()
    logging.basicConfig(
        level=getattr(logging, settings.runtime.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_agent(settings, once=args.once))


async def run_agent(settings: AgentSettings, once: bool = False) -> None:
    """Run the Agent heartbeat and task polling loops."""

    async with AgentClient(settings) as client:
        if once:
            await run_once(settings, client)
            return
        await asyncio.gather(
            heartbeat_loop(settings, client),
            task_loop(settings, client),
        )


async def heartbeat_loop(settings: AgentSettings, client: AgentClient) -> None:
    while True:
        try:
            await client.heartbeat()
            logger.info("heartbeat_ok server_id=%s log_count=%s", settings.server_id, len(settings.allow_logs))
        except Exception as exc:
            logger.warning("heartbeat_failed server_id=%s error=%s", settings.server_id, exc)
        await asyncio.sleep(settings.runtime.heartbeat_interval_seconds)


async def task_loop(settings: AgentSettings, client: AgentClient) -> None:
    while True:
        try:
            tasks = await client.fetch_tasks()
            for task in tasks.tasks:
                await process_task(settings, client, task)
        except Exception as exc:
            logger.warning("task_poll_failed server_id=%s error=%s", settings.server_id, exc)
        await asyncio.sleep(settings.runtime.task_poll_interval_seconds)


async def run_once(settings: AgentSettings, client: AgentClient) -> None:
    await client.heartbeat()
    tasks = await client.fetch_tasks()
    for task in tasks.tasks:
        await process_task(settings, client, task)


async def process_task(settings: AgentSettings, client: AgentClient, task: AgentTask) -> None:
    logger.info("task_start task_id=%s log_name=%s lines=%s", task.task_id, task.log_name, task.lines)
    try:
        log_path = get_allowed_log_path(settings, task.log_name)
        lines = read_tail_lines(log_path, task.lines, task.keyword)
        result = TaskResultRequest(task_id=task.task_id, status="finished", lines=lines)
    except Exception as exc:
        result = TaskResultRequest(task_id=task.task_id, status="failed", lines=[], error=str(exc))
    await client.upload_result(result)
    logger.info("task_uploaded task_id=%s status=%s line_count=%s", task.task_id, result.status, len(result.lines))


def get_allowed_log_path(settings: AgentSettings, log_name: str) -> Path:
    for log in settings.allow_logs:
        if log.name == log_name:
            return log.path
    raise PermissionError(f"log is not in allow_logs: {log_name}")


if __name__ == "__main__":
    main()
