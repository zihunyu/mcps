from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigError(ValueError):
    """Raised when required MCP/Jenkins configuration is missing or invalid."""


class ReleaseValidationError(ValueError):
    """Raised when a release request violates the configured safety policy."""


class ReleaseJobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    jenkins_path: str
    description: str = ""
    required_params: list[str] = Field(default_factory=list)
    allowed_params: list[str] = Field(default_factory=list)
    parameter_options: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("jenkins_path")
    @classmethod
    def validate_jenkins_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("jenkins_path must not be empty")
        return value.strip("/")

    @field_validator("required_params", "allowed_params")
    @classmethod
    def validate_param_names(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            name = str(item).strip()
            if not name:
                raise ValueError("parameter names must not be empty")
            if name not in seen:
                normalized.append(name)
                seen.add(name)
        return normalized

    @field_validator("parameter_options")
    @classmethod
    def validate_parameter_options(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        normalized: dict[str, list[str]] = {}
        for raw_name, raw_options in value.items():
            name = str(raw_name).strip()
            if not name:
                raise ValueError("parameter_options names must not be empty")
            if not isinstance(raw_options, list):
                raise ValueError(f"parameter_options.{name} must be a list")

            seen: set[str] = set()
            options: list[str] = []
            for raw_option in raw_options:
                option = str(raw_option).strip()
                if not option:
                    raise ValueError(f"parameter_options.{name} values must not be empty")
                if option not in seen:
                    options.append(option)
                    seen.add(option)
            normalized[name] = options
        return normalized

    @model_validator(mode="after")
    def validate_required_are_allowed(self) -> "ReleaseJobConfig":
        missing = sorted(set(self.required_params) - set(self.allowed_params))
        if missing:
            raise ValueError(f"required_params must be listed in allowed_params: {missing}")
        unknown_option_params = sorted(set(self.parameter_options) - set(self.allowed_params))
        if unknown_option_params:
            raise ValueError(
                "parameter_options keys must be listed in allowed_params: "
                f"{unknown_option_params}"
            )
        return self

    def public_display_name(self, name: str) -> str:
        return self.display_name or name


class JenkinsSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    jenkins_url: str
    username: str
    api_token: str
    allowed_jobs_file: Path
    jobs: dict[str, ReleaseJobConfig]
    request_timeout_seconds: float = 30.0
    verify_ssl: bool = True
    release_tasks_file: Path = Path(".jenkins_release_tasks.jsonl")
    mcp_bearer_token: str | None = None
    smtp: "SmtpSettings" = Field(default_factory=lambda: SmtpSettings())

    @field_validator("jenkins_url")
    @classmethod
    def normalize_jenkins_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("JENKINS_URL must not be empty")
        return value


class SmtpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "smtp.qq.com"
    port: int = 465
    username: str | None = None
    password: str | None = None
    sender: str | None = None
    recipients: list[str] = Field(default_factory=list)
    use_ssl: bool = True

    @property
    def from_address(self) -> str | None:
        return self.sender or self.username

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.host:
            missing.append("SMTP_HOST")
        if not self.username:
            missing.append("SMTP_USERNAME")
        if not self.password:
            missing.append("SMTP_PASSWORD")
        if not self.from_address:
            missing.append("SMTP_FROM")
        if not self.recipients:
            missing.append("SMTP_TO")
        return missing

    def is_configured(self) -> bool:
        return not self.missing_fields()


def load_settings(environ: Mapping[str, str] | None = None) -> JenkinsSettings:
    env = load_environment(environ)

    jenkins_url = _required_env(env, "JENKINS_URL")
    username = _required_env(env, "JENKINS_USER")
    api_token = _required_env(env, "JENKINS_API_TOKEN")

    allowed_jobs_file = Path(env.get("JENKINS_ALLOWED_JOBS_FILE", "config/allowed_jobs.yml"))
    release_tasks_file = Path(env.get("JENKINS_RELEASE_TASKS_FILE", ".jenkins_release_tasks.jsonl"))
    request_timeout_seconds = _float_env(env, "JENKINS_REQUEST_TIMEOUT_SECONDS", 30.0)
    verify_ssl = _bool_env(env, "JENKINS_VERIFY_SSL", True)
    mcp_bearer_token = _optional_env(env, "MCP_BEARER_TOKEN")
    smtp = load_smtp_settings(env)
    jobs = load_allowed_jobs(allowed_jobs_file)

    return JenkinsSettings(
        jenkins_url=jenkins_url,
        username=username,
        api_token=api_token,
        allowed_jobs_file=allowed_jobs_file,
        jobs=jobs,
        request_timeout_seconds=request_timeout_seconds,
        verify_ssl=verify_ssl,
        release_tasks_file=release_tasks_file,
        mcp_bearer_token=mcp_bearer_token,
        smtp=smtp,
    )


def load_smtp_settings(env: Mapping[str, str]) -> SmtpSettings:
    return SmtpSettings(
        host=env.get("SMTP_HOST", "smtp.qq.com").strip() or "smtp.qq.com",
        port=int(env.get("SMTP_PORT", "465")),
        username=_optional_env(env, "SMTP_USERNAME"),
        password=_optional_env(env, "SMTP_PASSWORD"),
        sender=_optional_env(env, "SMTP_FROM"),
        recipients=_list_env(env, "SMTP_TO"),
        use_ssl=_bool_env(env, "SMTP_USE_SSL", True),
    )


def load_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    base_env = dict(os.environ if environ is None else environ)
    env_file_name = base_env.get("JENKINS_ENV_FILE")
    env_file = Path(env_file_name) if env_file_name else Path(".env")

    if environ is not None and env_file_name is None:
        return base_env

    if not env_file.exists():
        return base_env

    dotenv_env = {
        key: value
        for key, value in dotenv_values(env_file).items()
        if value is not None
    }

    # Real process environment wins over .env so production deployments can override files.
    return {**dotenv_env, **base_env}


def load_allowed_jobs(path: str | Path) -> dict[str, ReleaseJobConfig]:
    allowed_jobs_path = Path(path)
    if not allowed_jobs_path.exists():
        raise ConfigError(
            f"Jenkins allowed jobs file does not exist: {allowed_jobs_path}. "
            "Set JENKINS_ALLOWED_JOBS_FILE or copy config/allowed_jobs.example.yml."
        )

    with allowed_jobs_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    if not isinstance(raw, dict) or "jobs" not in raw:
        raise ConfigError("Allowed jobs file must contain a top-level 'jobs' mapping.")
    if not isinstance(raw["jobs"], dict) or not raw["jobs"]:
        raise ConfigError("Allowed jobs file must define at least one job.")

    jobs: dict[str, ReleaseJobConfig] = {}
    for name, value in raw["jobs"].items():
        job_name = str(name).strip()
        if not job_name:
            raise ConfigError("Job names in the allowed jobs file must not be empty.")
        if not isinstance(value, dict):
            raise ConfigError(f"Job '{job_name}' must be a mapping.")
        try:
            jobs[job_name] = ReleaseJobConfig.model_validate(value)
        except ValueError as exc:
            raise ConfigError(f"Invalid config for job '{job_name}': {exc}") from exc

    return jobs


def validate_release_request(
    settings: JenkinsSettings,
    job_name: str,
    params: Mapping[str, Any] | None = None,
) -> tuple[ReleaseJobConfig, dict[str, str]]:
    name = job_name.strip()
    if name not in settings.jobs:
        allowed = ", ".join(sorted(settings.jobs))
        raise ReleaseValidationError(f"Job '{job_name}' is not allowed. Allowed jobs: {allowed}")

    job = settings.jobs[name]
    normalized_params = normalize_params(params or {})
    unknown_params = sorted(set(normalized_params) - set(job.allowed_params))
    if unknown_params:
        raise ReleaseValidationError(
            f"Parameters are not allowed for job '{name}': {unknown_params}. "
            f"Allowed parameters: {job.allowed_params}"
        )

    missing_required = sorted(set(job.required_params) - set(normalized_params))
    if missing_required:
        raise ReleaseValidationError(
            f"Missing required parameters for job '{name}': {missing_required}"
        )

    for param_name, allowed_values in job.parameter_options.items():
        if param_name not in normalized_params or not allowed_values:
            continue
        actual_value = normalized_params[param_name]
        if actual_value not in allowed_values:
            raise ReleaseValidationError(
                f"Value '{actual_value}' is not allowed for parameter '{param_name}' "
                f"on job '{name}'. Allowed values: {allowed_values}"
            )

    return job, normalized_params


def normalize_params(params: Mapping[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in params.items():
        name = str(key).strip()
        if not name:
            raise ReleaseValidationError("Parameter names must not be empty.")
        normalized[name] = normalize_param_value(value)
    return normalized


def normalize_param_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    if value is None:
        return ""
    raise ReleaseValidationError(
        "Only scalar parameter values are supported: string, int, float, bool, or null."
    )


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _optional_env(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _list_env(env: Mapping[str, str], name: str) -> list[str]:
    raw_value = env.get(name, "")
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _float_env(env: Mapping[str, str], name: str, default: float) -> float:
    raw_value = env.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number.") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than 0.")
    return value


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw_value = env.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean value.")
