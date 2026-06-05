from __future__ import annotations

from pathlib import Path

import pytest

from log_agent.config import AgentSettings, CenterApiConfig, LogDefinition
from log_agent.reader import read_tail_lines
from log_agent.worker import get_allowed_log_path


def test_read_tail_lines_with_keyword(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "\n".join(
            [
                "INFO start",
                "ERROR first",
                "INFO middle",
                "ERROR second",
                "INFO done",
            ]
        ),
        encoding="utf-8",
    )

    result = read_tail_lines(log_file, 4, keyword="ERROR")

    assert result == ["ERROR first", "ERROR second"]


def test_get_allowed_log_path_rejects_unregistered_log(tmp_path: Path) -> None:
    settings = AgentSettings(
        server_id="local-demo-01",
        center=CenterApiConfig(base_url="http://127.0.0.1:8000", agent_token="agent-token"),
        allow_logs=[LogDefinition(name="demo-log", path=tmp_path / "app.log")],
    )

    with pytest.raises(PermissionError, match="allow_logs"):
        get_allowed_log_path(settings, "other-log")
