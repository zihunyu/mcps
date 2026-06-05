"""Thread-safe in-memory storage for the Python 3.7 compatible Log Center."""

from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class InMemoryStore(object):
    def __init__(self, max_lines):
        self._max_lines = max_lines
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
                    "enabled": True,
                    "created_at": current_log["created_at"] if current_log else now,
                }

    def list_servers(self):
        with self._lock:
            return [
                {
                    "server_id": item["server_id"],
                    "env": item.get("env"),
                    "status": item["status"],
                }
                for item in sorted(self._servers.values(), key=lambda value: value["server_id"])
            ]

    def list_logs(self, server_id):
        with self._lock:
            return [
                {"log_name": item["log_name"]}
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
            fetched = []
            for task in self._tasks.values():
                if task["server_id"] != server_id or task["status"] != "pending":
                    continue
                task["status"] = "running"
                task["updated_at"] = now
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
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError("task not found: {0}".format(task_id))
            return {
                "task_id": task["task_id"],
                "status": task["status"],
                "lines": task["result_lines"],
                "error": task.get("error"),
            }
