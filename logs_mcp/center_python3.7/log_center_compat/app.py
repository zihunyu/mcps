"""Flask app for the Python 3.7 compatible Log Center."""

import logging

from flask import Flask, jsonify, request

from .store import InMemoryStore


logger = logging.getLogger(__name__)


def create_app(settings):
    app = Flask(__name__)
    store = InMemoryStore(max_lines=settings.limits.max_lines)

    @app.route("/api/agent/heartbeat", methods=["POST"])
    def heartbeat():
        require_bearer_token(settings.auth.agent_token)
        payload = request_json()
        server_id = required_text(payload.get("server_id"), "server_id")
        logs = validate_logs(payload.get("logs") or [])
        store.heartbeat(
            server_id=server_id,
            hostname=optional_text(payload.get("hostname")),
            ip=optional_text(payload.get("ip")),
            env=optional_text(payload.get("env")),
            logs=logs,
        )
        logger.info("agent_heartbeat server_id=%s log_count=%s", server_id, len(logs))
        return jsonify({"status": "ok"})

    @app.route("/api/agent/tasks", methods=["GET"])
    def fetch_tasks():
        require_bearer_token(settings.auth.agent_token)
        server_id = required_text(request.args.get("server_id"), "server_id")
        tasks = store.fetch_tasks(server_id)
        logger.info("agent_fetch_tasks server_id=%s task_count=%s", server_id, len(tasks))
        return jsonify({"tasks": tasks})

    @app.route("/api/agent/task/result", methods=["POST"])
    def upload_result():
        require_bearer_token(settings.auth.agent_token)
        payload = request_json()
        result = validate_task_result(payload)
        try:
            store.save_result(result)
        except KeyError as exc:
            return error_response(str(exc), 404)
        logger.info(
            "agent_task_result task_id=%s status=%s line_count=%s",
            result["task_id"],
            result["status"],
            len(result["lines"]),
        )
        return jsonify({"status": "ok"})

    @app.route("/api/log/servers", methods=["GET"])
    def list_servers():
        require_bearer_token(settings.auth.api_token)
        return jsonify(store.list_servers())

    @app.route("/api/log/server/<server_id>/files", methods=["GET"])
    def list_logs(server_id):
        require_bearer_token(settings.auth.api_token)
        return jsonify(store.list_logs(server_id))

    @app.route("/api/log/task", methods=["POST"])
    def create_task():
        require_bearer_token(settings.auth.api_token)
        payload = validate_create_task(request_json(), settings.limits.default_lines)
        if payload["lines"] > settings.limits.max_lines:
            return error_response("lines must be <= {0}".format(settings.limits.max_lines), 400)
        try:
            task = store.create_task(payload)
        except KeyError as exc:
            return error_response(str(exc), 404)
        logger.info(
            "create_task task_id=%s server_id=%s log_name=%s lines=%s keyword_present=%s",
            task["task_id"],
            task["server_id"],
            task["log_name"],
            task["lines"],
            bool(task.get("keyword")),
        )
        return jsonify({"task_id": task["task_id"]})

    @app.route("/api/log/task/<task_id>", methods=["GET"])
    def get_task_result(task_id):
        require_bearer_token(settings.auth.api_token)
        try:
            return jsonify(store.get_task_result(task_id))
        except KeyError as exc:
            return error_response(str(exc), 404)

    @app.errorhandler(ValueError)
    def handle_value_error(exc):
        return error_response(str(exc), 400)

    @app.errorhandler(UnauthorizedError)
    def handle_unauthorized(exc):
        return error_response(str(exc), 401)

    return app


def require_bearer_token(expected_token):
    expected = "Bearer {0}".format(expected_token)
    if request.headers.get("Authorization") != expected:
        raise UnauthorizedError("invalid token")


def request_json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


def validate_logs(logs):
    if not isinstance(logs, list):
        raise ValueError("logs must be a list")
    validated = []
    for item in logs:
        if not isinstance(item, dict):
            raise ValueError("logs item must be an object")
        validated.append(
            {
                "name": required_text(item.get("name"), "logs.name"),
                "path": required_text(item.get("path"), "logs.path"),
            }
        )
    return validated


def validate_create_task(payload, default_lines):
    server_id = required_text(payload.get("server_id"), "server_id")
    log_name = required_text(payload.get("log_name"), "log_name")
    lines = positive_int(payload.get("lines", default_lines), "lines")
    return {
        "server_id": server_id,
        "log_name": log_name,
        "lines": lines,
        "keyword": optional_text(payload.get("keyword")),
    }


def validate_task_result(payload):
    status = required_text(payload.get("status"), "status")
    if status not in ("pending", "running", "finished", "failed"):
        raise ValueError("status is invalid")
    lines = payload.get("lines") or []
    if not isinstance(lines, list):
        raise ValueError("lines must be a list")
    return {
        "task_id": required_text(payload.get("task_id"), "task_id"),
        "status": status,
        "lines": [str(line) for line in lines],
        "error": optional_text(payload.get("error")),
    }


def required_text(value, name):
    if value is None:
        raise ValueError("{0} must be configured".format(name))
    text = str(value).strip()
    if not text:
        raise ValueError("{0} must not be empty".format(name))
    return text


def optional_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def positive_int(value, name):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("{0} must be a positive integer".format(name))
    if number < 1:
        raise ValueError("{0} must be >= 1".format(name))
    return number


def error_response(detail, status_code):
    return jsonify({"detail": detail}), status_code


class UnauthorizedError(Exception):
    pass
