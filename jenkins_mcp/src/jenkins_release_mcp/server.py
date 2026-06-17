from __future__ import annotations

import argparse
import asyncio
import hashlib
from datetime import datetime, timezone
import hmac
import logging
import os
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
import uvicorn

from .config import JenkinsSettings, load_settings, validate_release_request
from .jenkins_client import JenkinsClient, RETRYABLE_HTTP_ERRORS, build_job_url
from .models import (
    BackgroundReleaseTask,
    BackgroundReleaseTaskResult,
    BackgroundReleaseTasksResult,
    MultiModuleReleaseItem,
    MultiModuleReleaseResult,
    NotificationResult,
    ReleaseJobInfo,
    ReleaseJobsResult,
    TriggerReleaseResult,
    WaitMultiModuleNotifyItem,
    WaitMultiModuleNotifyResult,
    WaitReleaseNotifyResult,
)
from .notifier import EmailNotifier
from .storage import ReleaseTaskStore


MODULE_PARAM = "models"
ACTIVE_RELEASE_STATUSES = {"triggering", "queued", "running", "retrying", "finishing"}
SENSITIVE_PARAM_PATTERNS = ("token", "password", "secret", "key", "credential")
FAILURE_KEYWORDS = (
    "ERROR",
    "Exception",
    "Traceback",
    "BUILD FAILURE",
    "FAILED",
    "npm ERR",
    "Maven failure",
)


class JenkinsClientContext(Protocol):
    async def __aenter__(self) -> Any: ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


ClientFactory = Callable[[JenkinsSettings], JenkinsClientContext]


class IgnoreConnectionResetFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        exc_info = record.exc_info
        if not exc_info:
            return True
        exc = exc_info[1]
        return not isinstance(exc, (ConnectionResetError, BrokenPipeError))


class BearerTokenMiddleware:
    def __init__(self, app: Any, token: str | None):
        self.app = app
        self.token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if not self.token or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        expected = f"Bearer {self.token}"
        actual = headers.get("authorization", "")
        if not hmac.compare_digest(actual, expected):
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class ReleaseRuntime:
    def __init__(
        self,
        settings: JenkinsSettings | None = None,
        client_factory: ClientFactory = JenkinsClient,
    ):
        self._settings = settings
        self._client_factory = client_factory
        self._background_tasks: dict[str, BackgroundReleaseTask] = {}
        self._task_store: ReleaseTaskStore | None = None
        if settings is not None:
            self._load_persisted_tasks()

    @property
    def settings(self) -> JenkinsSettings:
        if self._settings is None:
            self._settings = load_settings()
        if self._task_store is None:
            self._load_persisted_tasks()
        return self._settings

    def list_release_jobs(self) -> dict[str, Any]:
        jobs = [
            ReleaseJobInfo(
                name=name,
                display_name=job.public_display_name(name),
                description=job.description,
                jenkins_path=job.jenkins_path,
                job_url=build_job_url(self.settings.jenkins_url, job.jenkins_path),
                required_params=job.required_params,
                allowed_params=job.allowed_params,
                parameter_options=job.parameter_options,
            )
            for name, job in sorted(self.settings.jobs.items())
        ]
        return ReleaseJobsResult(jobs=jobs).model_dump()

    def get_allowed_job(self, job_name: str):
        name = job_name.strip()
        if name not in self.settings.jobs:
            allowed = ", ".join(sorted(self.settings.jobs))
            raise ValueError(f"Job '{job_name}' is not allowed. Allowed jobs: {allowed}")
        return self.settings.jobs[name]

    async def trigger_release(
        self,
        job_name: str,
        params: Mapping[str, Any] | None = None,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        job, normalized_params = validate_release_request(self.settings, job_name, params or {})
        job_url = build_job_url(self.settings.jenkins_url, job.jenkins_path)

        if dry_run:
            return TriggerReleaseResult(
                dry_run=True,
                job_name=job_name,
                jenkins_path=job.jenkins_path,
                job_url=job_url,
                params=normalized_params,
                message="Dry run only. No Jenkins build was triggered.",
            ).model_dump()

        if not confirm:
            raise ValueError("Real release requires confirm=true when dry_run=false.")

        async with self._client_factory(self.settings) as client:
            queue_url, queue_id = await client.trigger_build(job, normalized_params)

        return TriggerReleaseResult(
            dry_run=False,
            job_name=job_name,
            jenkins_path=job.jenkins_path,
            job_url=job_url,
            params=normalized_params,
            queue_url=queue_url,
            queue_id=queue_id,
            message="Jenkins build was triggered.",
        ).model_dump()

    async def get_queue_item(self, queue_id: int) -> dict[str, Any]:
        async with self._client_factory(self.settings) as client:
            result = await client.get_queue_item(queue_id)
        return result.model_dump()

    async def get_build_status(self, job_name: str, build_number: int) -> dict[str, Any]:
        job = self.get_allowed_job(job_name)
        async with self._client_factory(self.settings) as client:
            result = await client.get_build_status(job_name, job, build_number)
        return result.model_dump()

    async def wait_for_build(
        self,
        job_name: str,
        build_number: int | None = None,
        queue_id: int | None = None,
        timeout_seconds: int = 1800,
        poll_interval_seconds: int = 5,
    ) -> dict[str, Any]:
        job = self.get_allowed_job(job_name)
        async with self._client_factory(self.settings) as client:
            result = await client.wait_for_build(
                job_name=job_name,
                job=job,
                build_number=build_number,
                queue_id=queue_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
        return result.model_dump()

    async def get_build_log(
        self,
        job_name: str,
        build_number: int,
        start_offset: int = 0,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        job = self.get_allowed_job(job_name)
        async with self._client_factory(self.settings) as client:
            result = await client.get_build_log(
                job_name=job_name,
                job=job,
                build_number=build_number,
                start_offset=start_offset,
                max_chars=max_chars,
            )
        return result.model_dump()

    async def get_build_changes(self, job_name: str, build_number: int) -> dict[str, Any]:
        job = self.get_allowed_job(job_name)
        async with self._client_factory(self.settings) as client:
            result = await client.get_build_changes(job_name, job, build_number)
        return result.model_dump()

    async def get_build_branch(self, job_name: str, build_number: int) -> dict[str, Any]:
        job = self.get_allowed_job(job_name)
        async with self._client_factory(self.settings) as client:
            result = await client.get_build_branch(job_name, job, build_number)
        return result.model_dump()

    async def trigger_multi_module_release(
        self,
        job_name: str,
        modules: list[str],
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if not modules:
            raise ValueError("modules must not be empty.")

        job = self.get_allowed_job(job_name)
        module_param = "models"
        allowed_modules = job.parameter_options.get(module_param, [])
        if not allowed_modules:
            raise ValueError(
                f"Job '{job_name}' does not define parameter_options.{module_param}."
            )

        releases: list[MultiModuleReleaseItem] = []
        async with self._client_factory(self.settings) as client:
            for module in modules:
                _, params = validate_release_request(self.settings, job_name, {module_param: module})
                job_url = build_job_url(self.settings.jenkins_url, job.jenkins_path)
                if dry_run:
                    releases.append(
                        MultiModuleReleaseItem(
                            module=module,
                            dry_run=True,
                            params=params,
                            job_url=job_url,
                            message="Dry run only. No Jenkins build was triggered.",
                        )
                    )
                    continue

                if not confirm:
                    raise ValueError("Real release requires confirm=true when dry_run=false.")

                queue_url, queue_id = await client.trigger_build(job, params)
                releases.append(
                    MultiModuleReleaseItem(
                        module=module,
                        dry_run=False,
                        params=params,
                        queue_url=queue_url,
                        queue_id=queue_id,
                        job_url=job_url,
                        message="Jenkins build was triggered.",
                    )
                )

        return MultiModuleReleaseResult(
            job_name=job_name,
            module_param=module_param,
            dry_run=dry_run,
            releases=releases,
            message=(
                "Dry run only. No Jenkins builds were triggered."
                if dry_run
                else "Jenkins builds were triggered one module at a time."
            ),
        ).model_dump()

    async def wait_release_and_notify(
        self,
        job_name: str,
        queue_id: int | None = None,
        build_number: int | None = None,
        module: str | None = None,
        notify_to: str | list[str] | None = None,
        timeout_seconds: int = 1800,
        poll_interval_seconds: int = 5,
    ) -> dict[str, Any]:
        job = self.get_allowed_job(job_name)
        recipients = normalize_notify_to(notify_to)
        async with self._client_factory(self.settings) as client:
            wait_result = await client.wait_for_build(
                job_name=job_name,
                job=job,
                build_number=build_number,
                queue_id=queue_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            resolved_build_number = wait_result.build.build_number if wait_result.build else build_number
            changes = None
            failure_log_excerpt = None
            if resolved_build_number is not None:
                branch = await client.get_build_branch(job_name, job, resolved_build_number)
                changes = await client.get_build_changes(job_name, job, resolved_build_number)
                if wait_result.result != "SUCCESS":
                    log = await client.get_build_log(
                        job_name=job_name,
                        job=job,
                        build_number=resolved_build_number,
                        start_offset=0,
                        max_chars=8000,
                    )
                    failure_log_excerpt = summarize_failure_log(log.text, 4000)
            else:
                branch = None

        notification = EmailNotifier(self.settings.smtp).send(
            subject=build_release_email_subject(job_name, module, wait_result),
            body=build_release_email_body(
                job_name=job_name,
                module=module,
                wait_result=wait_result,
                branch=branch,
                changes=changes,
                failure_log_excerpt=failure_log_excerpt,
            ),
            recipients=recipients or None,
        )
        return WaitReleaseNotifyResult(
            job_name=job_name,
            module=module,
            wait=wait_result,
            branch=branch,
            changes=changes,
            failure_log_excerpt=failure_log_excerpt,
            notification=notification,
        ).model_dump()

    async def wait_multi_module_release_and_notify(
        self,
        job_name: str,
        releases: list[dict[str, Any]],
        notify_to: str | list[str] | None = None,
        timeout_seconds: int = 1800,
        poll_interval_seconds: int = 5,
    ) -> dict[str, Any]:
        if not releases:
            raise ValueError("releases must not be empty.")

        job = self.get_allowed_job(job_name)
        recipients = normalize_notify_to(notify_to)
        results: list[WaitMultiModuleNotifyItem] = []
        async with self._client_factory(self.settings) as client:
            for release in releases:
                module = release.get("module")
                queue_id = release.get("queue_id")
                build_number = release.get("build_number")
                wait_result = await client.wait_for_build(
                    job_name=job_name,
                    job=job,
                    build_number=build_number,
                    queue_id=queue_id,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
                resolved_build_number = (
                    wait_result.build.build_number if wait_result.build else build_number
                )
                changes = None
                failure_log_excerpt = None
                if resolved_build_number is not None:
                    branch = await client.get_build_branch(job_name, job, resolved_build_number)
                    changes = await client.get_build_changes(job_name, job, resolved_build_number)
                    if wait_result.result != "SUCCESS":
                        log = await client.get_build_log(
                            job_name=job_name,
                            job=job,
                            build_number=resolved_build_number,
                            start_offset=0,
                            max_chars=8000,
                        )
                        failure_log_excerpt = summarize_failure_log(log.text, 4000)
                else:
                    branch = None

                results.append(
                    WaitMultiModuleNotifyItem(
                        module=module,
                        queue_id=queue_id,
                        build_number=build_number,
                        wait=wait_result,
                        branch=branch,
                        changes=changes,
                        failure_log_excerpt=failure_log_excerpt,
                    )
                )

        notification = EmailNotifier(self.settings.smtp).send(
            subject=build_multi_release_email_subject(job_name, results),
            body=build_multi_release_email_body(job_name, results),
            recipients=recipients or None,
        )
        return WaitMultiModuleNotifyResult(
            job_name=job_name,
            results=results,
            notification=notification,
        ).model_dump()

    async def list_recent_releases(
        self,
        job_name: str,
        limit: int = 10,
        module: str | None = None,
        result: str | None = None,
    ) -> dict[str, Any]:
        job = self.get_allowed_job(job_name)
        async with self._client_factory(self.settings) as client:
            releases_result = await client.list_recent_releases(job_name, job, limit)
        releases = releases_result.releases
        if module:
            releases = [
                item
                for item in releases
                if any(param.name == MODULE_PARAM and param.value == module for param in item.parameters)
            ]
        if result:
            expected_result = result.upper()
            releases = [
                item
                for item in releases
                if (item.result or "").upper() == expected_result
            ]
        return releases_result.model_copy(update={"releases": releases}).model_dump()

    async def start_release(
        self,
        job_name: str,
        module: str,
        notify_to: str | list[str] | None = None,
        confirm_text: str = "",
        timeout_seconds: int = 1800,
        poll_interval_seconds: float = 5,
        force: bool = False,
    ) -> dict[str, Any]:
        job_name = job_name.strip()
        module = module.strip()
        expected_confirm_text = f"确认发版 {job_name} {module}"
        if confirm_text != expected_confirm_text:
            raise ValueError(
                "start_release requires confirm_text to exactly match: "
                f"{expected_confirm_text}"
            )
        return await self.release_and_notify_background(
            job_name=job_name,
            params={MODULE_PARAM: module},
            module=module,
            notify_to=notify_to,
            confirm=True,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            force=force,
        )

    async def release_and_notify_background(
        self,
        job_name: str,
        params: Mapping[str, Any] | None = None,
        module: str | None = None,
        notify_to: str | list[str] | None = None,
        confirm: bool = False,
        timeout_seconds: int = 1800,
        poll_interval_seconds: float = 5,
        force: bool = False,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("Background release requires confirm=true.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than 0.")

        job, normalized_params = validate_release_request(self.settings, job_name, params or {})
        module_name = module or normalized_params.get(MODULE_PARAM)
        recipients = normalize_notify_to(notify_to)
        lock_key = build_release_lock_key(job_name, module_name, normalized_params)
        self._ensure_no_active_release(lock_key, force)
        now = utc_now()
        task_id = uuid.uuid4().hex
        task = BackgroundReleaseTask(
            task_id=task_id,
            status="triggering",
            job_name=job_name,
            module=module_name,
            params=redact_params(normalized_params),
            lock_key=lock_key,
            notify_to=recipients,
            created_at=now,
            updated_at=now,
            message="正在触发 Jenkins 发版。",
        )
        self._remember_task(task)

        try:
            async with self._client_factory(self.settings) as client:
                queue_url, queue_id = await client.trigger_build(job, normalized_params)
        except Exception as exc:
            self._update_task(
                task_id,
                status="error",
                error=str(exc),
                message="触发 Jenkins 发版失败。",
            )
            raise

        self._update_task(
            task_id,
            status="queued",
            queue_url=queue_url,
            queue_id=queue_id,
            message="Jenkins 发版已触发，后台正在监控。",
        )
        asyncio.create_task(
            self._monitor_release_task(
                task_id=task_id,
                job_name=job_name,
                module=module_name,
                notify_to=recipients,
                queue_id=queue_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
        )
        return BackgroundReleaseTaskResult(task=self._background_tasks[task_id]).model_dump()

    def get_release_task(self, task_id: str) -> dict[str, Any]:
        task = self._background_tasks.get(task_id)
        if task is None:
            raise ValueError(f"Release task '{task_id}' was not found.")
        return BackgroundReleaseTaskResult(task=task).model_dump()

    def list_release_tasks(
        self,
        limit: int = 20,
        job_name: str | None = None,
        module: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0.")
        tasks = list(self._background_tasks.values())
        if job_name:
            tasks = [task for task in tasks if task.job_name == job_name]
        if module:
            tasks = [task for task in tasks if task.module == module]
        if status:
            tasks = [task for task in tasks if task.status == status]
        tasks = sorted(tasks, key=lambda item: item.created_at, reverse=True)[: min(limit, 100)]
        return BackgroundReleaseTasksResult(tasks=tasks).model_dump()

    async def _monitor_release_task(
        self,
        task_id: str,
        job_name: str,
        module: str | None,
        notify_to: list[str],
        queue_id: int | None,
        timeout_seconds: int,
        poll_interval_seconds: float,
    ) -> None:
        job = self.get_allowed_job(job_name)
        deadline = time.monotonic() + timeout_seconds
        build_number: int | None = None
        wait_result = None
        branch = None
        changes = None
        failure_log_excerpt = None
        notification = None

        try:
            async with self._client_factory(self.settings) as client:
                if queue_id is not None:
                    while time.monotonic() < deadline:
                        try:
                            queue = await client.get_queue_item(queue_id)
                        except RETRYABLE_HTTP_ERRORS as exc:
                            self._update_task(
                                task_id,
                                status="retrying",
                                message=f"读取 Jenkins 队列时连接中断，后台会继续重试：{exc}",
                            )
                            await asyncio.sleep(poll_interval_seconds)
                            continue
                        if queue.cancelled:
                            self._update_task(
                                task_id,
                                status="cancelled",
                                result="CANCELLED",
                                message="Jenkins 队列任务已取消。",
                            )
                            return
                        if queue.build_number is not None:
                            build_number = queue.build_number
                            self._update_task(
                                task_id,
                                status="running",
                                build_number=build_number,
                                build_url=queue.build_url,
                                message="Jenkins 构建已开始，后台正在等待完成。",
                            )
                            break
                        await asyncio.sleep(poll_interval_seconds)

                if build_number is None:
                    raise TimeoutError(f"Timed out waiting for queue item {queue_id}.")

                while time.monotonic() < deadline:
                    try:
                        build = await client.get_build_status(job_name, job, build_number)
                    except RETRYABLE_HTTP_ERRORS as exc:
                        self._update_task(
                            task_id,
                            status="retrying",
                            build_number=build_number,
                            message=f"读取 Jenkins 构建状态时连接中断，后台会继续重试：{exc}",
                        )
                        await asyncio.sleep(poll_interval_seconds)
                        continue
                    self._update_task(
                        task_id,
                        status="running" if build.building else "finishing",
                        build_number=build.build_number,
                        build_url=build.build_url,
                        result=build.result,
                        message="Jenkins 构建运行中。" if build.building else "Jenkins 构建已完成，正在整理通知。",
                    )
                    if not build.building:
                        from .models import WaitBuildResult

                        wait_result = WaitBuildResult(
                            job_name=job_name,
                            completed=True,
                            result=build.result or "UNKNOWN",
                            build=build,
                            message=f"Jenkins build {job_name} #{build.build_number} finished.",
                        )
                        break
                    await asyncio.sleep(poll_interval_seconds)

                if wait_result is None:
                    raise TimeoutError(f"Timed out waiting for build {job_name} #{build_number}.")

                branch = await retry_async(
                    lambda: client.get_build_branch(job_name, job, build_number),
                    attempts=3,
                    delay_seconds=poll_interval_seconds,
                )
                changes = await retry_async(
                    lambda: client.get_build_changes(job_name, job, build_number),
                    attempts=3,
                    delay_seconds=poll_interval_seconds,
                )
                if wait_result.result != "SUCCESS":
                    log = await retry_async(
                        lambda: client.get_build_log(
                            job_name=job_name,
                            job=job,
                            build_number=build_number,
                            start_offset=0,
                            max_chars=8000,
                        ),
                        attempts=3,
                        delay_seconds=poll_interval_seconds,
                    )
                    failure_log_excerpt = summarize_failure_log(log.text, 4000)

            notification = EmailNotifier(self.settings.smtp).send(
                subject=build_release_email_subject(job_name, module, wait_result),
                body=build_release_email_body(
                    job_name=job_name,
                    module=module,
                    wait_result=wait_result,
                    branch=branch,
                    changes=changes,
                    failure_log_excerpt=failure_log_excerpt,
                ),
                recipients=notify_to or None,
            )
            final_status = "success" if wait_result.result == "SUCCESS" else "failed"
            self._update_task(
                task_id,
                status=final_status,
                result=wait_result.result,
                branch=branch,
                changes=changes,
                failure_log_excerpt=failure_log_excerpt,
                notification=notification,
                message="后台发版监控已完成，邮件通知已处理。",
            )
        except Exception as exc:
            notification = EmailNotifier(self.settings.smtp).send(
                subject=f"【发版异常】{job_name} / {module or '-'} / #未知",
                body=(
                    f"任务名：{job_name}\n"
                    f"模块名：{module or '-'}\n"
                    f"后台任务：{task_id}\n"
                    f"异常信息：{exc}"
                ),
                recipients=notify_to or None,
            )
            self._update_task(
                task_id,
                status="error",
                error=str(exc),
                notification=notification,
                message="后台发版监控异常。",
            )

    def _remember_task(self, task: BackgroundReleaseTask) -> None:
        self._background_tasks[task.task_id] = task
        self._persist_task(task)
        if len(self._background_tasks) <= 100:
            return
        oldest = sorted(self._background_tasks.values(), key=lambda item: item.created_at)
        for item in oldest[: len(self._background_tasks) - 100]:
            self._background_tasks.pop(item.task_id, None)

    def _update_task(self, task_id: str, **updates: Any) -> None:
        task = self._background_tasks[task_id]
        updates["updated_at"] = utc_now()
        updated_task = task.model_copy(update=updates)
        self._background_tasks[task_id] = updated_task
        self._persist_task(updated_task)

    def _load_persisted_tasks(self) -> None:
        if self._settings is None:
            return
        self._task_store = ReleaseTaskStore(self._settings.release_tasks_file)
        self._background_tasks = self._task_store.load_latest()

    def _persist_task(self, task: BackgroundReleaseTask) -> None:
        if self._task_store is None:
            self._load_persisted_tasks()
        if self._task_store is not None:
            self._task_store.append(task)

    def _ensure_no_active_release(self, lock_key: str, force: bool) -> None:
        if force:
            return
        for task in self._background_tasks.values():
            if task.lock_key != lock_key or task.status not in ACTIVE_RELEASE_STATUSES:
                continue
            raise ValueError(
                "A release for the same job/module is already active: "
                f"task_id={task.task_id}, status={task.status}. Use force=true to override."
            )


def create_mcp(
    settings: JenkinsSettings | None = None,
    client_factory: ClientFactory = JenkinsClient,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    runtime = ReleaseRuntime(settings=settings, client_factory=client_factory)
    mcp = FastMCP(
        "Jenkins Release MCP",
        instructions=(
            "Safely trigger and monitor Jenkins release jobs from an allowlist. "
            "Use dry_run before triggering real releases."
        ),
        stateless_http=True,
        json_response=True,
    )
    mcp.settings.host = host
    mcp.settings.port = port

    @mcp.tool()
    def list_release_jobs() -> dict[str, Any]:
        """List Jenkins jobs this MCP server is allowed to release."""
        return runtime.list_release_jobs()

    @mcp.tool()
    async def trigger_release(
        job_name: str,
        params: dict[str, Any] | None = None,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Validate and optionally trigger an allowlisted Jenkins release job."""
        return await runtime.trigger_release(
            job_name=job_name,
            params=params or {},
            dry_run=dry_run,
            confirm=confirm,
        )

    @mcp.tool()
    async def get_queue_item(queue_id: int) -> dict[str, Any]:
        """Read Jenkins queue status by queue item id."""
        return await runtime.get_queue_item(queue_id=queue_id)

    @mcp.tool()
    async def get_build_status(job_name: str, build_number: int) -> dict[str, Any]:
        """Read Jenkins build status for an allowlisted job."""
        return await runtime.get_build_status(job_name=job_name, build_number=build_number)

    @mcp.tool()
    async def wait_for_build(
        job_name: str,
        build_number: int | None = None,
        queue_id: int | None = None,
        timeout_seconds: int = 1800,
        poll_interval_seconds: int = 5,
    ) -> dict[str, Any]:
        """Wait for a Jenkins queue item or build to finish."""
        return await runtime.wait_for_build(
            job_name=job_name,
            build_number=build_number,
            queue_id=queue_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    @mcp.tool()
    async def get_build_log(
        job_name: str,
        build_number: int,
        start_offset: int = 0,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        """Read a Jenkins progressive console log fragment."""
        return await runtime.get_build_log(
            job_name=job_name,
            build_number=build_number,
            start_offset=start_offset,
            max_chars=max_chars,
        )

    @mcp.tool()
    async def get_build_changes(job_name: str, build_number: int) -> dict[str, Any]:
        """Read commit authors and code changes recorded by Jenkins changelog."""
        return await runtime.get_build_changes(job_name=job_name, build_number=build_number)

    @mcp.tool()
    async def get_build_branch(job_name: str, build_number: int) -> dict[str, Any]:
        """Read the Git branch and commit checked out by a Jenkins build."""
        return await runtime.get_build_branch(job_name=job_name, build_number=build_number)

    @mcp.tool()
    async def trigger_multi_module_release(
        job_name: str,
        modules: list[str],
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Trigger one Jenkins build per module using the models parameter."""
        return await runtime.trigger_multi_module_release(
            job_name=job_name,
            modules=modules,
            dry_run=dry_run,
            confirm=confirm,
        )

    @mcp.tool()
    async def wait_release_and_notify(
        job_name: str,
        queue_id: int | None = None,
        build_number: int | None = None,
        module: str | None = None,
        notify_to: str | list[str] | None = None,
        timeout_seconds: int = 1800,
        poll_interval_seconds: int = 5,
    ) -> dict[str, Any]:
        """Wait for a release and send an email notification with changes and failure log."""
        return await runtime.wait_release_and_notify(
            job_name=job_name,
            queue_id=queue_id,
            build_number=build_number,
            module=module,
            notify_to=notify_to,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    @mcp.tool()
    async def wait_multi_module_release_and_notify(
        job_name: str,
        releases: list[dict[str, Any]],
        notify_to: str | list[str] | None = None,
        timeout_seconds: int = 1800,
        poll_interval_seconds: int = 5,
    ) -> dict[str, Any]:
        """Wait for multiple module releases and send one summary email notification."""
        return await runtime.wait_multi_module_release_and_notify(
            job_name=job_name,
            releases=releases,
            notify_to=notify_to,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    @mcp.tool()
    async def list_recent_releases(
        job_name: str,
        limit: int = 10,
        module: str | None = None,
        result: str | None = None,
    ) -> dict[str, Any]:
        """List recent Jenkins releases for an allowlisted job."""
        return await runtime.list_recent_releases(
            job_name=job_name,
            limit=limit,
            module=module,
            result=result,
        )

    @mcp.tool()
    async def start_release(
        job_name: str,
        module: str,
        notify_to: str | list[str] | None = None,
        confirm_text: str = "",
        timeout_seconds: int = 1800,
        poll_interval_seconds: float = 5,
        force: bool = False,
    ) -> dict[str, Any]:
        """Trigger one module release and let the MCP server monitor and email in the background."""
        return await runtime.start_release(
            job_name=job_name,
            module=module,
            notify_to=notify_to,
            confirm_text=confirm_text,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            force=force,
        )

    @mcp.tool()
    async def release_and_notify_background(
        job_name: str,
        params: dict[str, Any] | None = None,
        module: str | None = None,
        notify_to: str | list[str] | None = None,
        confirm: bool = False,
        timeout_seconds: int = 1800,
        poll_interval_seconds: float = 5,
        force: bool = False,
    ) -> dict[str, Any]:
        """Trigger a release and let the MCP server monitor and email in the background."""
        return await runtime.release_and_notify_background(
            job_name=job_name,
            params=params or {},
            module=module,
            notify_to=notify_to,
            confirm=confirm,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            force=force,
        )

    @mcp.tool()
    def get_release_task(task_id: str) -> dict[str, Any]:
        """Read the status of a background release task."""
        return runtime.get_release_task(task_id=task_id)

    @mcp.tool()
    def list_release_tasks(
        limit: int = 20,
        job_name: str | None = None,
        module: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List recent in-memory background release tasks."""
        return runtime.list_release_tasks(
            limit=limit,
            job_name=job_name,
            module=module,
            status=status,
        )

    return mcp


def main(argv: list[str] | None = None) -> None:
    configure_runtime_logging()
    configure_windows_event_loop()

    parser = argparse.ArgumentParser(description="Run the Jenkins Release MCP server.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="MCP transport to use.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HTTP_HOST", "127.0.0.1"),
        help="Host for streamable-http transport.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_HTTP_PORT", "8000")),
        help="Port for streamable-http transport.",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    mcp = create_mcp(settings=settings, host=args.host, port=args.port)
    if args.transport == "streamable-http":
        if settings.mcp_bearer_token:
            logging.getLogger(__name__).info("MCP Bearer Token authentication is enabled.")
        else:
            logging.getLogger(__name__).warning(
                "MCP Bearer Token authentication is disabled. Keep host as 127.0.0.1 "
                "unless this server is protected by another gateway."
            )
        app = BearerTokenMiddleware(mcp.streamable_http_app(), settings.mcp_bearer_token)
        uvicorn.run(app, host=args.host, port=args.port)
        return

    mcp.run(transport=args.transport)


def configure_runtime_logging() -> None:
    logging.getLogger("asyncio").addFilter(IgnoreConnectionResetFilter())


def configure_windows_event_loop() -> None:
    if sys.platform != "win32":
        return
    if not hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        return
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def tail_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def summarize_failure_log(text: str, max_chars: int = 4000) -> str:
    lines = text.splitlines()
    keyword_blocks: list[str] = []
    seen: set[int] = set()
    for index, line in enumerate(lines):
        if not any(keyword.lower() in line.lower() for keyword in FAILURE_KEYWORDS):
            continue
        start = max(0, index - 3)
        end = min(len(lines), index + 4)
        block_indexes = range(start, end)
        block_lines = [lines[item] for item in block_indexes if item not in seen]
        seen.update(block_indexes)
        if block_lines:
            keyword_blocks.append("\n".join(block_lines))
    if keyword_blocks:
        summary = "\n\n--- failure context ---\n".join(keyword_blocks)
        tail = tail_text(text, min(1200, max_chars))
        combined = f"失败关键词上下文：\n{summary}\n\n日志尾部：\n{tail}"
        return tail_text(combined, max_chars)
    return tail_text(text, max_chars)


def normalize_notify_to(notify_to: str | list[str] | None) -> list[str]:
    if notify_to is None:
        return []
    if isinstance(notify_to, str):
        return [item.strip() for item in notify_to.split(",") if item.strip()]
    recipients: list[str] = []
    for item in notify_to:
        value = str(item).strip()
        if value:
            recipients.append(value)
    return recipients


def build_release_lock_key(
    job_name: str,
    module: str | None,
    normalized_params: Mapping[str, str],
) -> str:
    if module:
        return f"job={job_name.strip()}|module={module.strip()}"
    params_text = "&".join(f"{key}={normalized_params[key]}" for key in sorted(normalized_params))
    params_hash = hashlib.sha256(params_text.encode("utf-8")).hexdigest()[:16]
    return f"job={job_name.strip()}|params={params_hash}"


def redact_params(params: Mapping[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in params.items():
        key_lower = key.lower()
        if any(pattern in key_lower for pattern in SENSITIVE_PARAM_PATTERNS):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def retry_async(call: Any, attempts: int, delay_seconds: float) -> Any:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await call()
        except RETRYABLE_HTTP_ERRORS as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            await asyncio.sleep(delay_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("retry_async called with no attempts.")


def build_release_email_subject(job_name: str, module: str | None, wait_result: Any) -> str:
    status = "发版成功" if wait_result.result == "SUCCESS" else "发版失败"
    build_text = f"#{wait_result.build.build_number}" if wait_result.build else "#未知"
    module_text = module or "-"
    return f"【{status}】{job_name} / {module_text} / {build_text}"


def build_multi_release_email_subject(
    job_name: str,
    results: list[WaitMultiModuleNotifyItem],
) -> str:
    failed = [item for item in results if item.wait.result != "SUCCESS"]
    success_count = len(results) - len(failed)
    return f"【发版汇总】{job_name} / 成功 {success_count} / 失败 {len(failed)}"


def build_release_email_body(
    job_name: str,
    module: str | None,
    wait_result: Any,
    branch: Any,
    changes: Any,
    failure_log_excerpt: str | None,
) -> str:
    lines = [
        f"任务名：{job_name}",
        f"模块名：{module or '-'}",
        f"发版结果：{format_result_cn(wait_result.result)}",
    ]
    if wait_result.build:
        lines.extend(
            [
                f"构建号：#{wait_result.build.build_number}",
                f"构建地址：{wait_result.build.build_url}",
                f"耗时：{wait_result.build.duration_millis or 0} ms",
            ]
        )
    if branch:
        lines.extend(
            [
                f"代码分支：{branch.branch or '-'}",
                f"Commit Hash：{branch.commit_hash or '-'}",
                f"Git 地址：{branch.remote_url or '-'}",
            ]
        )
    if changes:
        lines.append(f"提交人：{', '.join(changes.authors) if changes.authors else '-'}")
        lines.append(f"代码变动数量：{len(changes.changes)}")
        lines.append("代码变动摘要：")
        for change in changes.changes[:10]:
            lines.append(
                f"- {change.commit_id or '-'} / {change.author or '-'} / {change.message}"
            )
            if change.affected_files:
                lines.append(f"  影响文件：{', '.join(change.affected_files[:8])}")
    if failure_log_excerpt:
        lines.extend(["", "失败日志摘要：", failure_log_excerpt])
    return "\n".join(lines)


def build_multi_release_email_body(
    job_name: str,
    results: list[WaitMultiModuleNotifyItem],
) -> str:
    failed = [item for item in results if item.wait.result != "SUCCESS"]
    lines = [
        f"任务名：{job_name}",
        f"模块数量：{len(results)}",
        f"成功数量：{len(results) - len(failed)}",
        f"失败数量：{len(failed)}",
        "",
    ]
    for item in results:
        build = item.wait.build
        lines.append(f"模块名：{item.module or '-'}")
        lines.append(f"发版结果：{format_result_cn(item.wait.result)}")
        if build:
            lines.append(f"构建：#{build.build_number} {build.build_url}")
        if item.branch:
            lines.append(f"代码分支：{item.branch.branch or '-'}")
            lines.append(f"Commit Hash：{item.branch.commit_hash or '-'}")
        if item.changes:
            lines.append(
                f"提交人：{', '.join(item.changes.authors) if item.changes.authors else '-'}"
            )
        if item.failure_log_excerpt:
            lines.append("失败日志摘要：")
            lines.append(item.failure_log_excerpt)
        lines.append("")
    return "\n".join(lines)


def format_result_cn(result: str | None) -> str:
    mapping = {
        "SUCCESS": "成功",
        "FAILURE": "失败",
        "ABORTED": "已中止",
        "CANCELLED": "已取消",
    }
    return mapping.get(result or "", result or "未知")


if __name__ == "__main__":
    main()
