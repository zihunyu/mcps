from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import respx

from jenkins_release_mcp.config import load_settings
from jenkins_release_mcp.jenkins_client import (
    JenkinsClient,
    build_job_api_path,
    parse_build_changes,
    parse_build_branch_from_log,
    parse_queue_id,
)


def make_settings(tmp_path: Path):
    jobs_file = tmp_path / "jobs.yml"
    jobs_file.write_text(
        """
jobs:
  app-prod:
    jenkins_path: release-folder/app-prod
    required_params:
      - VERSION
    allowed_params:
      - VERSION
      - CANARY
  no-params:
    jenkins_path: no-params-job
    allowed_params: []
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


def test_build_job_api_path_encodes_folders() -> None:
    assert build_job_api_path("release folder/app prod") == "job/release%20folder/job/app%20prod/"
    assert build_job_api_path("job/release-folder/job/app-prod") == "job/release-folder/job/app-prod/"


def test_parse_queue_id() -> None:
    assert parse_queue_id("https://jenkins.example.com/queue/item/123/") == 123
    assert parse_queue_id("https://jenkins.example.com/job/app/") is None


def test_trigger_build_with_parameters_uses_build_with_parameters(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        job = settings.jobs["app-prod"]
        with respx.mock(base_url=settings.jenkins_url) as router:
            route = router.post("/job/release-folder/job/app-prod/buildWithParameters").mock(
                return_value=httpx.Response(
                    201,
                    headers={"Location": "https://jenkins.example.com/queue/item/123/"},
                )
            )
            async with JenkinsClient(settings) as client:
                queue_url, queue_id = await client.trigger_build(job, {"VERSION": "1.2.3"})

        assert route.called
        assert queue_url == "https://jenkins.example.com/queue/item/123/"
        assert queue_id == 123

    asyncio.run(run())


def test_trigger_build_without_parameters_uses_build(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        job = settings.jobs["no-params"]
        with respx.mock(base_url=settings.jenkins_url) as router:
            route = router.post("/job/no-params-job/build").mock(
                return_value=httpx.Response(201, headers={"Location": "/queue/item/124/"})
            )
            async with JenkinsClient(settings) as client:
                queue_url, queue_id = await client.trigger_build(job, {})

        assert route.called
        assert queue_url == "https://jenkins.example.com/queue/item/124/"
        assert queue_id == 124

    asyncio.run(run())


def test_queue_build_status_and_log(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        job = settings.jobs["app-prod"]
        with respx.mock(base_url=settings.jenkins_url) as router:
            router.get("/queue/item/123/api/json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "cancelled": False,
                        "why": None,
                        "executable": {
                            "number": 45,
                            "url": "https://jenkins.example.com/job/release-folder/job/app-prod/45/",
                        },
                    },
                )
            )
            router.get("/job/release-folder/job/app-prod/45/api/json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "number": 45,
                        "building": False,
                        "result": "SUCCESS",
                        "duration": 1000,
                        "estimatedDuration": 1000,
                        "timestamp": 1710000000000,
                        "url": "https://jenkins.example.com/job/release-folder/job/app-prod/45/",
                        "fullDisplayName": "app-prod #45",
                    },
                )
            )
            router.get("/job/release-folder/job/app-prod/45/logText/progressiveText").mock(
                return_value=httpx.Response(
                    200,
                    text="hello log",
                    headers={"X-Text-Size": "9", "X-More-Data": "false"},
                )
            )

            async with JenkinsClient(settings) as client:
                queue = await client.get_queue_item(123)
                status = await client.get_build_status("app-prod", job, 45)
                log = await client.get_build_log("app-prod", job, 45)

        assert queue.build_number == 45
        assert status.result == "SUCCESS"
        assert log.text == "hello log"
        assert log.next_offset == 9

    asyncio.run(run())


def test_wait_for_build_from_queue(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        job = settings.jobs["app-prod"]
        with respx.mock(base_url=settings.jenkins_url) as router:
            router.get("/queue/item/123/api/json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "cancelled": False,
                        "executable": {
                            "number": 45,
                            "url": "https://jenkins.example.com/job/release-folder/job/app-prod/45/",
                        },
                    },
                )
            )
            router.get("/job/release-folder/job/app-prod/45/api/json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "number": 45,
                        "building": False,
                        "result": "SUCCESS",
                        "url": "https://jenkins.example.com/job/release-folder/job/app-prod/45/",
                    },
                )
            )

            async with JenkinsClient(settings) as client:
                result = await client.wait_for_build(
                    "app-prod",
                    job,
                    queue_id=123,
                    timeout_seconds=5,
                    poll_interval_seconds=1,
                )

        assert result.completed is True
        assert result.result == "SUCCESS"
        assert result.build is not None
        assert result.build.build_number == 45

    asyncio.run(run())


def test_get_build_changes_reads_changeset(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        job = settings.jobs["app-prod"]
        with respx.mock(base_url=settings.jenkins_url) as router:
            router.get("/job/release-folder/job/app-prod/45/api/json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "number": 45,
                        "changeSet": {
                            "kind": "git",
                            "items": [
                                {
                                    "commitId": "abc123",
                                    "msg": "fix cockpit release",
                                    "timestamp": 1710000000000,
                                    "author": {"fullName": "Alice"},
                                    "affectedPaths": ["src/app.py"],
                                    "paths": [{"file": "README.md"}],
                                }
                            ],
                        },
                    },
                )
            )

            async with JenkinsClient(settings) as client:
                result = await client.get_build_changes("app-prod", job, 45)

        assert result.authors == ["Alice"]
        assert result.changes[0].commit_id == "abc123"
        assert result.changes[0].affected_files == ["README.md", "src/app.py"]

    asyncio.run(run())


def test_get_build_changes_returns_empty_message_before_checkout(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        job = settings.jobs["app-prod"]
        with respx.mock(base_url=settings.jenkins_url) as router:
            router.get("/job/release-folder/job/app-prod/45/api/json").mock(
                return_value=httpx.Response(200, json={"number": 45, "changeSet": {"items": []}})
            )

            async with JenkinsClient(settings) as client:
                result = await client.get_build_changes("app-prod", job, 45)

        assert result.changes == []
        assert "No Jenkins changelog" in result.message

    asyncio.run(run())


def test_parse_build_changes_reads_pipeline_changesets() -> None:
    changes = parse_build_changes(
        {
            "changeSets": [
                {
                    "kind": "git",
                    "items": [
                        {
                            "id": "def456",
                            "msg": "pipeline change",
                            "author": {"fullName": "Bob"},
                            "paths": [{"file": "Jenkinsfile"}],
                        }
                    ],
                }
            ]
        }
    )

    assert len(changes) == 1
    assert changes[0].commit_id == "def456"
    assert changes[0].author == "Bob"


def test_parse_build_branch_from_log() -> None:
    branch, commit_hash, remote_url = parse_build_branch_from_log(
        """
 > /usr/bin/git config remote.origin.url http://git.example.com/app.git # timeout=10
 > /usr/bin/git rev-parse refs/remotes/origin/release/20250116_v5.40.0.25.01^{commit} # timeout=10
Checking out Revision 63073f1cc0c74fd2af14553e7a1a76b61f9bcd8c (refs/remotes/origin/release/20250116_v5.40.0.25.01)
"""
    )

    assert branch == "release/20250116_v5.40.0.25.01"
    assert commit_hash == "63073f1cc0c74fd2af14553e7a1a76b61f9bcd8c"
    assert remote_url == "http://git.example.com/app.git"


def test_parse_build_branch_from_log_returns_empty_when_missing() -> None:
    assert parse_build_branch_from_log("no checkout here") == (None, None, None)


def test_get_build_branch_reads_log(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        job = settings.jobs["app-prod"]
        with respx.mock(base_url=settings.jenkins_url) as router:
            router.get("/job/release-folder/job/app-prod/45/logText/progressiveText").mock(
                return_value=httpx.Response(
                    200,
                    text=(
                        " > /usr/bin/git config remote.origin.url http://git.example.com/app.git # timeout=10\n"
                        "Checking out Revision abc123 (refs/remotes/origin/release/test)\n"
                    ),
                    headers={"X-Text-Size": "140", "X-More-Data": "false"},
                )
            )

            async with JenkinsClient(settings) as client:
                result = await client.get_build_branch("app-prod", job, 45)

        assert result.branch == "release/test"
        assert result.commit_hash == "abc123"
        assert result.remote_url == "http://git.example.com/app.git"

    asyncio.run(run())


def test_list_recent_releases_reads_builds_and_parameters(tmp_path: Path) -> None:
    async def run() -> None:
        settings = make_settings(tmp_path)
        job = settings.jobs["app-prod"]
        with respx.mock(base_url=settings.jenkins_url) as router:
            router.get("/job/release-folder/job/app-prod/api/json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "builds": [
                            {
                                "number": 46,
                                "building": False,
                                "result": "SUCCESS",
                                "timestamp": 1710000000000,
                                "duration": 1000,
                                "url": "https://jenkins.example.com/job/release-folder/job/app-prod/46/",
                                "actions": [
                                    {"parameters": [{"name": "VERSION", "value": "1.2.3"}]},
                                    {},
                                ],
                            }
                        ]
                    },
                )
            )

            async with JenkinsClient(settings) as client:
                result = await client.list_recent_releases("app-prod", job, 10)

        assert result.releases[0].build_number == 46
        assert result.releases[0].parameters[0].name == "VERSION"

    asyncio.run(run())
