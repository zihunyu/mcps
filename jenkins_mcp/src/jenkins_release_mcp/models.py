from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReleaseJobInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    description: str = ""
    jenkins_path: str
    job_url: str
    required_params: list[str] = Field(default_factory=list)
    allowed_params: list[str] = Field(default_factory=list)
    parameter_options: dict[str, list[str]] = Field(default_factory=dict)


class ReleaseJobsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[ReleaseJobInfo]


class BuildChangeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit_id: str | None = None
    author: str | None = None
    message: str = ""
    timestamp_millis: int | None = None
    affected_files: list[str] = Field(default_factory=list)
    scm_kind: str | None = None


class BuildChangesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    build_number: int
    changes: list[BuildChangeItem] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    message: str


class BuildBranchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    build_number: int
    branch: str | None = None
    commit_hash: str | None = None
    remote_url: str | None = None
    build_url: str
    message: str


class TriggerReleaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    job_name: str
    jenkins_path: str
    job_url: str
    params: dict[str, str] = Field(default_factory=dict)
    queue_url: str | None = None
    queue_id: int | None = None
    message: str


class MultiModuleReleaseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str
    dry_run: bool
    params: dict[str, str] = Field(default_factory=dict)
    queue_url: str | None = None
    queue_id: int | None = None
    job_url: str
    message: str


class MultiModuleReleaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    module_param: str
    dry_run: bool
    releases: list[MultiModuleReleaseItem] = Field(default_factory=list)
    message: str


class QueueItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_id: int
    cancelled: bool = False
    blocked: bool | None = None
    stuck: bool | None = None
    why: str | None = None
    build_number: int | None = None
    build_url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class BuildStatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    build_number: int
    building: bool
    result: str | None = None
    timestamp_millis: int | None = None
    duration_millis: int | None = None
    estimated_duration_millis: int | None = None
    build_url: str
    full_display_name: str | None = None


class WaitBuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    completed: bool
    result: str | None = None
    queue: QueueItemResult | None = None
    build: BuildStatusResult | None = None
    message: str


class BuildLogResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    build_number: int
    start_offset: int
    next_offset: int
    has_more_data: bool
    text: str


class NotificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sent: bool
    channel: str = "email"
    recipients: list[str] = Field(default_factory=list)
    subject: str | None = None
    error: str | None = None


class BackgroundReleaseTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str
    job_name: str
    module: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    lock_key: str | None = None
    notify_to: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    queue_url: str | None = None
    queue_id: int | None = None
    build_number: int | None = None
    build_url: str | None = None
    result: str | None = None
    message: str
    branch: BuildBranchResult | None = None
    changes: BuildChangesResult | None = None
    failure_log_excerpt: str | None = None
    notification: NotificationResult | None = None
    error: str | None = None


class BackgroundReleaseTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: BackgroundReleaseTask


class BackgroundReleaseTasksResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[BackgroundReleaseTask] = Field(default_factory=list)


class WaitReleaseNotifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    module: str | None = None
    wait: WaitBuildResult
    branch: BuildBranchResult | None = None
    changes: BuildChangesResult | None = None
    failure_log_excerpt: str | None = None
    notification: NotificationResult


class WaitMultiModuleNotifyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str | None = None
    queue_id: int | None = None
    build_number: int | None = None
    wait: WaitBuildResult
    branch: BuildBranchResult | None = None
    changes: BuildChangesResult | None = None
    failure_log_excerpt: str | None = None


class WaitMultiModuleNotifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    results: list[WaitMultiModuleNotifyItem] = Field(default_factory=list)
    notification: NotificationResult


class RecentReleaseParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str


class RecentReleaseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_number: int
    building: bool
    result: str | None = None
    timestamp_millis: int | None = None
    duration_millis: int | None = None
    build_url: str
    parameters: list[RecentReleaseParameter] = Field(default_factory=list)


class RecentReleasesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    releases: list[RecentReleaseItem] = Field(default_factory=list)
