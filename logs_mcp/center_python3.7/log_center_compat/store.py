"""Thread-safe in-memory storage for the Python 3.7 compatible Log Center."""

from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class InMemoryStore(object):
    def __init__(self, max_lines, running_timeout_seconds=300, server_offline_after_seconds=60):
        self._max_lines = max_lines
        self._running_timeout = timedelta(seconds=running_timeout_seconds)
        self._server_offline_after = timedelta(seconds=server_offline_after_seconds)
        self._servers = {}
        self._logs = {}
        self._tasks = {}
        self._lock = RLock()

    def heartbeat(self, server_id, hostname, ip, env, logs):
        with self._lock:
            now = utc_now()
            current = self._servers.get(server_id)
            created_at = current["created_at"] if current else now
            self._servers[server_id] = {
                "server_id": server_id,
                "hostname": hostname,
                "ip": ip,
                "env": env,
                "status": "online",
                "last_heartbeat": now,
                "created_at": created_at,
            }
            for log in logs:
                key = (server_id, log["name"])
                current_log = self._logs.get(key)
                self._logs[key] = {
                    "server_id": server_id,
                    "log_name": log["name"],
                    "log_path": log["path"],
                    "exists": log.get("exists"),
                    "size_bytes": log.get("size_bytes"),
                    "modified_at": log.get("modified_at"),
                    "enabled": True,
                    "created_at": current_log["created_at"] if current_log else now,
                }

    def list_servers(self):
        with self._lock:
            now = datetime.now(timezone.utc)
            return [
                {
                    "server_id": item["server_id"],
                    "env": item.get("env"),
                    "status": self._server_status(item, now),
                    "last_heartbeat": item["last_heartbeat"],
                }
                for item in sorted(self._servers.values(), key=lambda value: value["server_id"])
            ]

    def list_logs(self, server_id):
        with self._lock:
            return [
                {
                    "log_name": item["log_name"],
                    "exists": item.get("exists"),
                    "size_bytes": item.get("size_bytes"),
                    "modified_at": item.get("modified_at"),
                }
                for item in sorted(self._logs.values(), key=lambda value: value["log_name"])
                if item["server_id"] == server_id and item.get("enabled", True)
            ]

    def create_task(self, request_data):
        with self._lock:
            server_id = request_data["server_id"]
            log_name = request_data["log_name"]
            if server_id not in self._servers:
                raise KeyError("server not registered: {0}".format(server_id))
            if (server_id, log_name) not in self._logs:
                raise KeyError("log not registered: {0}/{1}".format(server_id, log_name))
            now = utc_now()
            task_id = uuid4().hex
            task = {
                "task_id": task_id,
                "server_id": server_id,
                "log_name": log_name,
                "keyword": request_data.get("keyword"),
                "lines": min(int(request_data["lines"]), self._max_lines),
                "status": "pending",
                "result_lines": [],
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            self._tasks[task_id] = task
            return task

    def fetch_tasks(self, server_id):
        with self._lock:
            now = utc_now()
            self._recover_timed_out_running_tasks(now)
            fetched = []
            for task in self._tasks.values():
                if task["server_id"] != server_id or task["status"] != "pending":
                    continue
                task["status"] = "running"
                task["updated_at"] = now
                task["run_started_at"] = now
                fetched.append(
                    {
                        "task_id": task["task_id"],
                        "log_name": task["log_name"],
                        "keyword": task.get("keyword"),
                        "lines": task["lines"],
                    }
                )
            return fetched

    def save_result(self, result):
        with self._lock:
            task = self._tasks.get(result["task_id"])
            if task is None:
                raise KeyError("task not found: {0}".format(result["task_id"]))
            task["status"] = result["status"]
            task["result_lines"] = result.get("lines") or []
            task["error"] = result.get("error")
            task["updated_at"] = utc_now()

    def get_task_result(self, task_id):
        with self._lock:
            self._recover_timed_out_running_tasks(utc_now())
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError("task not found: {0}".format(task_id))
            return {
                "task_id": task["task_id"],
                "status": task["status"],
                "lines": task["result_lines"],
                "error": task.get("error"),
            }

    def _recover_timed_out_running_tasks(self, now_text):
        now = parse_utc(now_text)
        for task in self._tasks.values():
            if task["status"] != "running":
                continue
            started_at = parse_utc(task.get("run_started_at") or task["updated_at"])
            if now - started_at > self._running_timeout:
                task["status"] = "pending"
                task["run_started_at"] = None
                task["updated_at"] = now_text

    def _server_status(self, item, now):
        last_heartbeat = parse_utc(item["last_heartbeat"])
        if now - last_heartbeat > self._server_offline_after:
            return "offline"
        return "online"


def parse_utc(value):
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)
