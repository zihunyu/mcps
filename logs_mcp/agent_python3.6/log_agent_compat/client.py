"""HTTP client for the Python 3.6 compatible Log Agent."""

import json
from urllib import parse, request


class AgentClient(object):
    def __init__(self, settings):
        self._settings = settings

    def heartbeat(self):
        payload = {
            "server_id": self._settings.server_id,
            "hostname": self._settings.hostname,
            "ip": self._settings.ip,
            "env": self._settings.env,
            "logs": [
                {"name": log.name, "path": str(log.path)}
                for log in self._settings.allow_logs
            ],
        }
        self._request("POST", "/api/agent/heartbeat", body=payload)

    def fetch_tasks(self):
        return self._request(
            "GET",
            "/api/agent/tasks",
            params={"server_id": self._settings.server_id},
        ) or {"tasks": []}

    def upload_result(self, result):
        self._request("POST", "/api/agent/task/result", body=result)

    def _request(self, method, path, body=None, params=None):
        url = self._settings.center.base_url + path
        if params:
            url += "?" + parse.urlencode(params)
        data = None
        headers = {
            "Authorization": "Bearer {0}".format(self._settings.center.agent_token),
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, data=data, headers=headers, method=method)
        with request.urlopen(req, timeout=self._settings.center.timeout_seconds) as response:
            raw = response.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))
