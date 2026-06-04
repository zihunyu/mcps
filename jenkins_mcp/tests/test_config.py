from __future__ import annotations

from pathlib import Path

import pytest

from jenkins_release_mcp.config import (
    ConfigError,
    ReleaseValidationError,
    load_environment,
    load_settings,
    validate_release_request,
)


def write_jobs(path: Path) -> None:
    path.write_text(
        """
jobs:
  app-prod:
    display_name: App Prod
    jenkins_path: release-folder/app-prod
    description: Production release.
    required_params:
      - VERSION
    allowed_params:
      - VERSION
      - CANARY
    parameter_options:
      CANARY:
        - "true"
        - "false"
""",
        encoding="utf-8",
    )


def test_load_settings_requires_jenkins_url(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.yml"
    write_jobs(jobs_file)

    with pytest.raises(ConfigError, match="JENKINS_URL"):
        load_settings(
            {
                "JENKINS_USER": "bot",
                "JENKINS_API_TOKEN": "token",
                "JENKINS_ALLOWED_JOBS_FILE": str(jobs_file),
            }
        )


def test_load_environment_reads_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JENKINS_URL=https://jenkins.example.com\n"
        "JENKINS_USER=from-file\n"
        "JENKINS_API_TOKEN=token\n",
        encoding="utf-8",
    )

    env = load_environment({"JENKINS_ENV_FILE": str(env_file), "JENKINS_USER": "from-env"})

    assert env["JENKINS_URL"] == "https://jenkins.example.com"
    assert env["JENKINS_USER"] == "from-env"
    assert env["JENKINS_API_TOKEN"] == "token"


def test_load_settings_can_read_dotenv_file(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.yml"
    write_jobs(jobs_file)
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"JENKINS_URL=https://jenkins.example.com\n"
        f"JENKINS_USER=bot\n"
        f"JENKINS_API_TOKEN=token\n"
        f"JENKINS_ALLOWED_JOBS_FILE={jobs_file.as_posix()}\n",
        encoding="utf-8",
    )

    settings = load_settings({"JENKINS_ENV_FILE": str(env_file)})

    assert settings.jenkins_url == "https://jenkins.example.com"
    assert settings.username == "bot"


def test_load_settings_reads_smtp_config(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.yml"
    write_jobs(jobs_file)

    settings = load_settings(
        {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "bot",
            "JENKINS_API_TOKEN": "token",
            "JENKINS_ALLOWED_JOBS_FILE": str(jobs_file),
            "SMTP_USERNAME": "sender@qq.com",
            "SMTP_PASSWORD": "auth-code",
            "SMTP_TO": "a@example.com,b@example.com",
            "MCP_BEARER_TOKEN": "secret-token",
        }
    )

    assert settings.smtp.host == "smtp.qq.com"
    assert settings.smtp.from_address == "sender@qq.com"
    assert settings.smtp.recipients == ["a@example.com", "b@example.com"]
    assert settings.smtp.is_configured() is True
    assert settings.mcp_bearer_token == "secret-token"


def test_validate_release_request_normalizes_allowed_params(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.yml"
    write_jobs(jobs_file)
    settings = load_settings(
        {
            "JENKINS_URL": "https://jenkins.example.com/",
            "JENKINS_USER": "bot",
            "JENKINS_API_TOKEN": "token",
            "JENKINS_ALLOWED_JOBS_FILE": str(jobs_file),
        }
    )

    job, params = validate_release_request(
        settings,
        "app-prod",
        {"VERSION": "1.2.3", "CANARY": True},
    )

    assert job.jenkins_path == "release-folder/app-prod"
    assert params == {"VERSION": "1.2.3", "CANARY": "true"}


def test_validate_release_request_rejects_unknown_job(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.yml"
    write_jobs(jobs_file)
    settings = load_settings(
        {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "bot",
            "JENKINS_API_TOKEN": "token",
            "JENKINS_ALLOWED_JOBS_FILE": str(jobs_file),
        }
    )

    with pytest.raises(ReleaseValidationError, match="not allowed"):
        validate_release_request(settings, "other-job", {"VERSION": "1.2.3"})


def test_validate_release_request_rejects_unknown_params(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.yml"
    write_jobs(jobs_file)
    settings = load_settings(
        {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "bot",
            "JENKINS_API_TOKEN": "token",
            "JENKINS_ALLOWED_JOBS_FILE": str(jobs_file),
        }
    )

    with pytest.raises(ReleaseValidationError, match="not allowed"):
        validate_release_request(settings, "app-prod", {"VERSION": "1.2.3", "BAD": "x"})


def test_validate_release_request_rejects_missing_required_params(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.yml"
    write_jobs(jobs_file)
    settings = load_settings(
        {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "bot",
            "JENKINS_API_TOKEN": "token",
            "JENKINS_ALLOWED_JOBS_FILE": str(jobs_file),
        }
    )

    with pytest.raises(ReleaseValidationError, match="Missing required"):
        validate_release_request(settings, "app-prod", {})


def test_validate_release_request_rejects_disallowed_parameter_value(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.yml"
    write_jobs(jobs_file)
    settings = load_settings(
        {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "bot",
            "JENKINS_API_TOKEN": "token",
            "JENKINS_ALLOWED_JOBS_FILE": str(jobs_file),
        }
    )

    with pytest.raises(ReleaseValidationError, match="Value 'maybe' is not allowed"):
        validate_release_request(settings, "app-prod", {"VERSION": "1.2.3", "CANARY": "maybe"})
