"""Configuration loading for the Python 3.7 compatible Log Center."""

import os
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
CENTER_CONFIG_ENV_VAR = "LOG_CENTER_CONFIG"


class ServerConfig(object):
    def __init__(self, host="127.0.0.1", port=8000, log_level="info"):
        self.host = _required_text(host, "server.host")
        self.port = _port(port, "server.port")
        self.log_level = _log_level(log_level)


class AuthConfig(object):
    def __init__(self, api_token, agent_token):
        self.api_token = _token(api_token, "auth.api_token")
        self.agent_token = _token(agent_token, "auth.agent_token")


class LimitsConfig(object):
    def __init__(self, default_lines=200, max_lines=5000):
        self.default_lines = _positive_int(default_lines, "limits.default_lines")
        self.max_lines = _positive_int(max_lines, "limits.max_lines")
        if self.max_lines < self.default_lines:
            raise ValueError("limits.max_lines must be >= limits.default_lines")


class CenterSettings(object):
    def __init__(self, server, auth, limits):
        self.server = server
        self.auth = auth
        self.limits = limits


def get_config_path():
    configured = os.getenv(CENTER_CONFIG_ENV_VAR)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_CONFIG_PATH


def load_config(path=None):
    config_path = Path(path).expanduser().resolve() if path else get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(
            "Config file not found: {0}. Create it from center/config.example.yaml "
            "or set {1}.".format(config_path, CENTER_CONFIG_ENV_VAR)
        )
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML object")

    server_raw = raw.get("server") or {}
    auth_raw = raw.get("auth") or {}
    limits_raw = raw.get("limits") or {}
    if not isinstance(server_raw, dict):
        raise ValueError("server must be a YAML object")
    if not isinstance(auth_raw, dict):
        raise ValueError("auth must be a YAML object")
    if not isinstance(limits_raw, dict):
        raise ValueError("limits must be a YAML object")

    return CenterSettings(
        server=ServerConfig(
            host=server_raw.get("host", "127.0.0.1"),
            port=server_raw.get("port", 8000),
            log_level=server_raw.get("log_level", "info"),
        ),
        auth=AuthConfig(
            api_token=auth_raw.get("api_token"),
            agent_token=auth_raw.get("agent_token"),
        ),
        limits=LimitsConfig(
            default_lines=limits_raw.get("default_lines", 200),
            max_lines=limits_raw.get("max_lines", 5000),
        ),
    )


def _required_text(value, name):
    if value is None:
        raise ValueError("{0} must be configured".format(name))
    text = str(value).strip()
    if not text:
        raise ValueError("{0} must not be empty".format(name))
    return text


def _token(value, name):
    token = _required_text(value, name)
    if token == "change-me":
        raise ValueError("auth tokens must be configured")
    return token


def _positive_int(value, name):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("{0} must be a positive integer".format(name))
    if number < 1:
        raise ValueError("{0} must be >= 1".format(name))
    return number


def _port(value, name):
    number = _positive_int(value, name)
    if number > 65535:
        raise ValueError("{0} must be <= 65535".format(name))
    return number


def _log_level(value):
    level = _required_text(value, "server.log_level").lower()
    allowed = ("debug", "info", "warning", "error", "critical")
    if level not in allowed:
        raise ValueError("server.log_level must be one of: {0}".format(", ".join(allowed)))
    return level
