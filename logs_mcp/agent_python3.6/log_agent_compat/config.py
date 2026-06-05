"""Configuration loading for the Python 3.6 compatible Log Agent."""

import os
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
AGENT_CONFIG_ENV_VAR = "LOG_AGENT_CONFIG"


class CenterApiConfig(object):
    def __init__(self, base_url, agent_token, timeout_seconds=10):
        self.base_url = _required_text(base_url, "center.base_url").rstrip("/")
        self.agent_token = _required_text(agent_token, "center.agent_token")
        if self.agent_token == "change-me":
            raise ValueError("center.agent_token must be configured")
        self.timeout_seconds = _positive_float(timeout_seconds, "center.timeout_seconds")


class LogDefinition(object):
    def __init__(self, name, path):
        self.name = _required_text(name, "allow_logs.name")
        self.path = Path(_required_text(path, "allow_logs.path"))


class AgentRuntimeConfig(object):
    def __init__(
        self,
        heartbeat_interval_seconds=10,
        task_poll_interval_seconds=3,
        log_level="INFO",
    ):
        self.heartbeat_interval_seconds = _positive_float(
            heartbeat_interval_seconds,
            "runtime.heartbeat_interval_seconds",
        )
        self.task_poll_interval_seconds = _positive_float(
            task_poll_interval_seconds,
            "runtime.task_poll_interval_seconds",
        )
        self.log_level = _log_level(log_level)


class AgentSettings(object):
    def __init__(
        self,
        server_id,
        center,
        hostname=None,
        ip=None,
        env=None,
        allow_logs=None,
        runtime=None,
    ):
        self.server_id = _required_text(server_id, "server_id")
        self.hostname = _optional_text(hostname)
        self.ip = _optional_text(ip)
        self.env = _optional_text(env)
        self.center = center
        self.allow_logs = allow_logs or []
        self.runtime = runtime or AgentRuntimeConfig()


def get_config_path():
    configured = os.getenv(AGENT_CONFIG_ENV_VAR)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_CONFIG_PATH


def load_config(path=None):
    config_path = Path(path).expanduser().resolve() if path else get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(
            "Config file not found: {0}. Create it from agent/config.example.yaml "
            "or set {1}.".format(config_path, AGENT_CONFIG_ENV_VAR)
        )
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML object")

    center_raw = raw.get("center") or {}
    if not isinstance(center_raw, dict):
        raise ValueError("center must be a YAML object")
    runtime_raw = raw.get("runtime") or {}
    if not isinstance(runtime_raw, dict):
        raise ValueError("runtime must be a YAML object")
    logs_raw = raw.get("allow_logs") or []
    if not isinstance(logs_raw, list):
        raise ValueError("allow_logs must be a YAML list")

    center = CenterApiConfig(
        base_url=center_raw.get("base_url"),
        agent_token=center_raw.get("agent_token"),
        timeout_seconds=center_raw.get("timeout_seconds", 10),
    )
    runtime = AgentRuntimeConfig(
        heartbeat_interval_seconds=runtime_raw.get("heartbeat_interval_seconds", 10),
        task_poll_interval_seconds=runtime_raw.get("task_poll_interval_seconds", 3),
        log_level=runtime_raw.get("log_level", "INFO"),
    )
    allow_logs = []
    for item in logs_raw:
        if not isinstance(item, dict):
            raise ValueError("allow_logs item must be a YAML object")
        allow_logs.append(LogDefinition(name=item.get("name"), path=item.get("path")))

    return AgentSettings(
        server_id=raw.get("server_id"),
        hostname=raw.get("hostname"),
        ip=raw.get("ip"),
        env=raw.get("env"),
        center=center,
        allow_logs=allow_logs,
        runtime=runtime,
    )


def _required_text(value, name):
    if value is None:
        raise ValueError("{0} must be configured".format(name))
    text = str(value).strip()
    if not text:
        raise ValueError("{0} must not be empty".format(name))
    return text


def _optional_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_float(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("{0} must be a positive number".format(name))
    if number <= 0:
        raise ValueError("{0} must be > 0".format(name))
    return number


def _log_level(value):
    level = _required_text(value, "runtime.log_level").upper()
    allowed = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    if level not in allowed:
        raise ValueError("runtime.log_level must be one of: {0}".format(", ".join(allowed)))
    return level
