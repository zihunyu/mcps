from __future__ import annotations

from pathlib import Path

import pytest

from log_mcp.config import CONFIG_ENV_VAR, load_config


def write_config(path: Path, token: str = "token") -> None:
    path.write_text(
        f"""
center:
  base_url: "http://center.local/"
  api_token: "{token}"
  timeout_seconds: 2
  poll_interval_seconds: 0.01
  poll_timeout_seconds: 0.05
limits:
  default_lines: 200
  max_lines: 5000
auth:
  bearer_token: "mcp-token"
download:
  dir: "./downloads"
  public_base_url: "http://mcp.local:8081/"
  token_ttl_seconds: 1800
""",
        encoding="utf-8",
    )


def test_load_config_from_explicit_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)

    config = load_config(config_path)

    assert config.center.base_url == "http://center.local"
    assert config.center.api_token == "token"
    assert config.limits.default_lines == 200
    assert config.auth.bearer_token == "mcp-token"
    assert config.download.public_base_url == "http://mcp.local:8081"
    assert config.download.token_ttl_seconds == 1800


def test_load_config_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    config = load_config()

    assert config.center.base_url == "http://center.local"


def test_missing_token_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path, token="change-me")

    with pytest.raises(ValueError, match="center.api_token"):
        load_config(config_path)


def test_download_token_ttl_must_be_positive(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("token_ttl_seconds: 1800", "token_ttl_seconds: 0"), encoding="utf-8")

    with pytest.raises(ValueError, match="token_ttl_seconds"):
        load_config(config_path)


def test_mcp_bearer_token_placeholder_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace('bearer_token: "mcp-token"', 'bearer_token: "change-me"'), encoding="utf-8")

    with pytest.raises(ValueError, match="auth.bearer_token"):
        load_config(config_path)
