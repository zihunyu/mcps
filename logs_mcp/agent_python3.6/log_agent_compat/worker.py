"""Worker loop for the Python 3.6 compatible Log Agent."""

import argparse
import logging
import time

from .client import AgentClient
from .config import load_config
from .reader import read_tail_lines


logger = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start Python 3.6 compatible Log Agent")
    parser.add_argument("--once", action="store_true", help="Run one heartbeat/task cycle and exit")
    args = parser.parse_args(argv)

    settings = load_config()
    logging.basicConfig(
        level=getattr(logging, settings.runtime.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_agent(settings, once=args.once)


def run_agent(settings, once=False):
    client = AgentClient(settings)
    if once:
        run_once(settings, client)
        return

    next_heartbeat = 0
    next_task_poll = 0
    while True:
        now = time.time()
        if now >= next_heartbeat:
            try:
                client.heartbeat()
                logger.info(
                    "heartbeat_ok server_id=%s log_count=%s",
                    settings.server_id,
                    len(settings.allow_logs),
                )
            except Exception as exc:
                logger.warning("heartbeat_failed server_id=%s error=%s", settings.server_id, exc)
            next_heartbeat = now + settings.runtime.heartbeat_interval_seconds

        if now >= next_task_poll:
            try:
                poll_and_process_tasks(settings, client)
            except Exception as exc:
                logger.warning("task_poll_failed server_id=%s error=%s", settings.server_id, exc)
            next_task_poll = now + settings.runtime.task_poll_interval_seconds

        sleep_seconds = min(next_heartbeat, next_task_poll) - time.time()
        time.sleep(max(0.1, min(sleep_seconds, 1.0)))


def run_once(settings, client):
    client.heartbeat()
    poll_and_process_tasks(settings, client)


def poll_and_process_tasks(settings, client):
    tasks_response = client.fetch_tasks()
    for task in tasks_response.get("tasks", []):
        process_task(settings, client, task)


def process_task(settings, client, task):
    task_id = task.get("task_id")
    log_name = task.get("log_name")
    lines = int(task.get("lines") or 1)
    keyword = task.get("keyword")
    logger.info("task_start task_id=%s log_name=%s lines=%s", task_id, log_name, lines)
    try:
        log_path = get_allowed_log_path(settings, log_name)
        result_lines = read_tail_lines(log_path, lines, keyword)
        result = {"task_id": task_id, "status": "finished", "lines": result_lines}
    except Exception as exc:
        result = {"task_id": task_id, "status": "failed", "lines": [], "error": str(exc)}
    client.upload_result(result)
    logger.info(
        "task_uploaded task_id=%s status=%s line_count=%s",
        task_id,
        result["status"],
        len(result["lines"]),
    )


def get_allowed_log_path(settings, log_name):
    for log in settings.allow_logs:
        if log.name == log_name:
            return log.path
    raise PermissionError("log is not in allow_logs: {0}".format(log_name))


if __name__ == "__main__":
    main()
