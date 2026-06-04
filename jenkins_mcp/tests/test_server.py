from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import httpx

from jenkins_release_mcp.config import load_settings
from jenkins_release_mcp.models import (
    BuildBranchResult,
    BuildChangeItem,
    BuildChangesResult,
    BuildStatusResult,
    QueueItemResult,
    WaitBuildResult,
)
from jenkins_release_mcp.server import ReleaseRuntime
from jenkins_release_mcp.server import BearerTokenMiddleware, build_release_email_body, build_release_email_subject


def make_settings(tmp_path: Path):
    jobs_file = tmp_path / "jobs.yml"
    jobs_file.write_text(
        """
jobs:
  app-prod:
    display_name: App Prod
    jenkins_path: release-folder/app-prod
    required_params:
      - models
    allowed_params:
      - models
    parameter_options:
      models:
        - service-api
        - service-web
""",
        encoding="utf-8",
    )
    return load_settings(
        {
            "JENKINS_URL": "https://jenkins.example.com",
            "JENKINS_USER": "bot",
            "JENKINS_API_TOKEN": "token",
            "JENKINS_ALLOWED_JOBS_FILE": str(jobs_file),
        }
    )


class FakeClient:
    def __init__(self) -> None:
        self.trigger_calls: list[tuple[Any, dict[str, str]]] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def trigger_build(self, job: Any, params: dict[str, str]):
        self.trigger_calls.append((job, params))
        return "https://jenkins.example.com/queue/item/123/", 123

    async def get_queue_item(self, queue_id: int) -> QueueItemResult:
        return QueueItemResult(
            queue_id=queue_id,
            cancelled=False,
            build_number=45,
            build_url="https://jenkins.example.com/job/app/45/",
        )

    async def get_build_status(
        self,
        job_name: str,
        job: Any,
        build_number: int,
    ) -> BuildStatusResult:
        return BuildStatusResult(
            job_name=job_name,
            build_number=build_number,
            building=False,
            result="SUCCESS",
            build_url=f"https://jenkins.example.com/job/app/{build_number}/",
        )

    async def wait_for_build(
        self,
        job_name: str,
        job: Any,
        build_number: int | None = None,
        queue_id: int | None = None,
        timeout_seconds: int = 1800,
        poll_interval_seconds: int = 5,
    ) -> WaitBuildResult:
        build_number = build_number or 45
        result = "FAILURE" if queue_id == 999 else "SUCCESS"
        return WaitBuildResult(
            job_name=job_name,
            completed=True,
            result=result,
            build=BuildStatusResult(
                job_name=job_name,
                build_number=build_number,
                building=False,
                result=result,
                build_url=f"https://jenkins.example.com/job/app/{build_number}/",
            ),
            message="done",
        )

    async def get_build_changes(
        self,
        job_name: str,
        job: Any,
        build_number: int,
    ) -> BuildChangesResult:
        return BuildChangesResult(
            job_name=job_name,
            build_number=build_number,
            authors=["Alice"],
            changes=[BuildChangeItem(commit_id="abc123", author="Alice", message="change")],
            message="Jenkins changelog was found.",
        )

    async def get_build_branch(
        self,
        job_name: str,
        job: Any,
        build_number: int,
    ) -> BuildBranchResult:
        return BuildBranchResult(
            job_name=job_name,
            build_number=build_number,
            branch="release/test",
            commit_hash="abc123",
            remote_url="http://git.example.com/app.git",
            build_url=f"https://jenkins.example.com/job/app/{build_number}/",
            message="Jenkins checkout branch was found.",
        )

    async def get_build_log(
        self,
        job_name: str,
        job: Any,
        build_number: int,
        start_offset: int = 0,
        max_chars: int = 12000,
    ):
        from jenkins_release_mcp.models import BuildLogResult

        return BuildLogResult(
            job_name=job_name,
            build_number=build_number,
            start_offset=start_offset,
            next_offset=18,
            has_more_data=False,
            text="failed build output",
        )


class FlakyStatusClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.status_calls = 0

    async def get_build_status(
        self,
        job_name: str,
        job: Any,
        build_number: int,
    ) -> BuildStatusResult:
        self.status_calls += 1
        if self.status_calls == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return await super().get_build_status(job_name, job, build_number)


def test_list_release_jobs(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runtime = ReleaseRuntime(settings=settings)

    result = runtime.list_release_jobs()

    assert result["jobs"][0]["name"] == "app-prod"
    assert result["jobs"][0]["job_url"] == (
        "https://jenkins.example.com/job/release-folder/job/app-prod/"
    )
    assert result["jobs"][0]["parameter_options"] == {
        "models": ["service-api", "service-web"]
    }


def test_trigger_release_dry_run_does_not_call_jenkins(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FakeClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        result = await runtime.trigger_release(
            "app-prod",
            {"models": "service-api"},
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert result["queue_id"] is None
        assert fake.trigger_calls == []

    asyncio.run(run())


def test_trigger_release_requires_confirm_for_real_release(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FakeClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        with pytest.raises(ValueError, match="confirm=true"):
            await runtime.trigger_release(
                "app-prod",
                {"models": "service-api"},
                dry_run=False,
                confirm=False,
            )

        assert fake.trigger_calls == []

    asyncio.run(run())


def test_trigger_release_calls_jenkins_when_confirmed(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FakeClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        result = await runtime.trigger_release(
            "app-prod",
            {"models": "service-api"},
            dry_run=False,
            confirm=True,
        )

        assert result["dry_run"] is False
        assert result["queue_id"] == 123
        assert fake.trigger_calls[0][1] == {"models": "service-api"}

    asyncio.run(run())


def test_trigger_multi_module_release_dry_run(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FakeClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        result = await runtime.trigger_multi_module_release(
            "app-prod",
            ["service-api", "service-web"],
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert len(result["releases"]) == 2
        assert fake.trigger_calls == []

    asyncio.run(run())


def test_trigger_multi_module_release_calls_jenkins(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FakeClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        result = await runtime.trigger_multi_module_release(
            "app-prod",
            ["service-api", "service-web"],
            dry_run=False,
            confirm=True,
        )

        assert len(result["releases"]) == 2
        assert fake.trigger_calls[0][1] == {"models": "service-api"}
        assert fake.trigger_calls[1][1] == {"models": "service-web"}

    asyncio.run(run())


def test_trigger_multi_module_release_rejects_unknown_module(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: FakeClient())

        with pytest.raises(Exception, match="not allowed"):
            await runtime.trigger_multi_module_release("app-prod", ["missing"], dry_run=True)

    asyncio.run(run())


def test_wait_release_and_notify_without_smtp_returns_error(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FakeClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        result = await runtime.wait_release_and_notify(
            "app-prod",
            queue_id=123,
            module="service-api",
            timeout_seconds=5,
        )

        assert result["wait"]["result"] == "SUCCESS"
        assert result["branch"]["branch"] == "release/test"
        assert result["changes"]["authors"] == ["Alice"]
        assert result["notification"]["sent"] is False
        assert "SMTP is not configured" in result["notification"]["error"]

    asyncio.run(run())


def test_release_email_template_is_chinese() -> None:
    wait = WaitBuildResult(
        job_name="app-prod",
        completed=True,
        result="SUCCESS",
        build=BuildStatusResult(
            job_name="app-prod",
            build_number=3176,
            building=False,
            result="SUCCESS",
            build_url="http://jenkins/job/3176/",
            duration_millis=1000,
        ),
        message="done",
    )
    branch = BuildBranchResult(
        job_name="app-prod",
        build_number=3176,
        branch="release/test",
        commit_hash="abc123",
        remote_url="http://git.example.com/app.git",
        build_url="http://jenkins/job/3176/",
        message="ok",
    )
    changes = BuildChangesResult(
        job_name="app-prod",
        build_number=3176,
        authors=["Alice"],
        changes=[BuildChangeItem(commit_id="abc123", author="Alice", message="修复问题")],
        message="ok",
    )

    subject = build_release_email_subject("app-prod", "service-api", wait)
    body = build_release_email_body("app-prod", "service-api", wait, branch, changes, None)

    assert subject == "【发版成功】app-prod / service-api / #3176"
    assert "代码分支：release/test" in body
    assert "Commit Hash：abc123" in body
    assert "提交人：Alice" in body


def test_bearer_token_middleware_rejects_missing_token() -> None:
    async def app(scope, receive, send):
        response = __import__("starlette.responses").responses.JSONResponse({"ok": True})
        await response(scope, receive, send)

    async def run(headers):
        sent = []
        async def send(message):
            sent.append(message)

        middleware = BearerTokenMiddleware(app, "secret")
        await middleware(
            {"type": "http", "path": "/mcp", "headers": headers},
            lambda: None,
            send,
        )
        return sent[0]["status"]

    assert asyncio.run(run([])) == 401
    assert asyncio.run(run([(b"authorization", b"Bearer wrong")])) == 401
    assert asyncio.run(run([(b"authorization", b"Bearer secret")])) == 200


def test_wait_release_and_notify_failure_includes_log(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FakeClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        result = await runtime.wait_release_and_notify(
            "app-prod",
            queue_id=999,
            module="service-api",
            timeout_seconds=5,
        )

        assert result["wait"]["result"] == "FAILURE"
        assert result["failure_log_excerpt"] == "failed build output"

    asyncio.run(run())


def test_release_and_notify_background_returns_task_and_completes(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FakeClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        result = await runtime.release_and_notify_background(
            "app-prod",
            {"models": "service-api"},
            confirm=True,
            timeout_seconds=5,
            poll_interval_seconds=0.01,
        )

        task_id = result["task"]["task_id"]
        assert result["task"]["status"] == "queued"
        for _ in range(50):
            task = runtime.get_release_task(task_id)["task"]
            if task["status"] == "success":
                break
            await asyncio.sleep(0.02)

        task = runtime.get_release_task(task_id)["task"]
        assert task["status"] == "success"
        assert task["build_number"] == 45
        assert task["branch"]["branch"] == "release/test"
        assert task["notification"]["sent"] is False

    asyncio.run(run())


def test_release_and_notify_background_requires_confirm(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: FakeClient())

        with pytest.raises(ValueError, match="confirm=true"):
            await runtime.release_and_notify_background(
                "app-prod",
                {"models": "service-api"},
                confirm=False,
            )

    asyncio.run(run())


def test_list_release_tasks_returns_recent_tasks(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FakeClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        await runtime.release_and_notify_background(
            "app-prod",
            {"models": "service-api"},
            confirm=True,
            timeout_seconds=5,
            poll_interval_seconds=0.01,
        )

        tasks = runtime.list_release_tasks()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["job_name"] == "app-prod"

    asyncio.run(run())


def test_background_task_retries_transient_jenkins_disconnect(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        fake = FlakyStatusClient()
        runtime = ReleaseRuntime(settings=settings, client_factory=lambda _: fake)

        result = await runtime.release_and_notify_background(
            "app-prod",
            {"models": "service-api"},
            confirm=True,
            timeout_seconds=5,
            poll_interval_seconds=0.01,
        )

        task_id = result["task"]["task_id"]
        for _ in range(50):
            task = runtime.get_release_task(task_id)["task"]
            if task["status"] in {"success", "error"}:
                break
            await asyncio.sleep(0.02)

        task = runtime.get_release_task(task_id)["task"]
        assert task["status"] == "success"
        assert fake.status_calls >= 2

    asyncio.run(run())
