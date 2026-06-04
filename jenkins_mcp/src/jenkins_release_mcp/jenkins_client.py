from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from .config import JenkinsSettings, ReleaseJobConfig
from .models import (
    BuildChangeItem,
    BuildBranchResult,
    BuildChangesResult,
    BuildLogResult,
    BuildStatusResult,
    QueueItemResult,
    RecentReleaseItem,
    RecentReleaseParameter,
    RecentReleasesResult,
    WaitBuildResult,
)


class JenkinsAPIError(RuntimeError):
    """Raised when Jenkins returns an unexpected or failed API response."""


class JenkinsTimeoutError(TimeoutError):
    """Raised when a Jenkins queue item or build does not finish within a timeout."""


RETRYABLE_HTTP_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.WriteTimeout,
)


QUEUE_ID_RE = re.compile(r"/queue/item/(?P<queue_id>\d+)/?")
CHECKOUT_BRANCH_RE = re.compile(r"Checking out Revision\s+(?P<commit>[0-9a-fA-F]+)\s+\((?P<ref>[^)]+)\)")
REV_PARSE_BRANCH_RE = re.compile(r"refs/remotes/origin/(?P<branch>[^\s^]+)\^\{commit\}")
REMOTE_URL_RE = re.compile(r"git config remote\.origin\.url\s+(?P<url>\S+)")


class JenkinsClient:
    def __init__(self, settings: JenkinsSettings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.jenkins_url,
            auth=(settings.username, settings.api_token),
            timeout=settings.request_timeout_seconds,
            verify=settings.verify_ssl,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "JenkinsClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def trigger_build(
        self,
        job: ReleaseJobConfig,
        params: Mapping[str, str],
    ) -> tuple[str | None, int | None]:
        endpoint = build_job_api_path(job.jenkins_path)
        endpoint += "buildWithParameters" if params else "build"

        response = await self._client.post(endpoint, data=dict(params))
        if response.status_code not in {200, 201, 202, 302, 303}:
            raise _response_error("Failed to trigger Jenkins build", response)

        queue_url = _absolute_url(self.settings.jenkins_url, response.headers.get("Location"))
        queue_id = parse_queue_id(queue_url) if queue_url else None
        return queue_url, queue_id

    async def get_queue_item(self, queue_id: int) -> QueueItemResult:
        response = await self._client.get(f"queue/item/{queue_id}/api/json")
        if response.status_code != 200:
            raise _response_error(f"Failed to read Jenkins queue item {queue_id}", response)
        data = response.json()
        executable = data.get("executable") or {}
        return QueueItemResult(
            queue_id=queue_id,
            cancelled=bool(data.get("cancelled", False)),
            blocked=data.get("blocked"),
            stuck=data.get("stuck"),
            why=data.get("why"),
            build_number=executable.get("number"),
            build_url=executable.get("url"),
            raw=data,
        )

    async def get_build_status(
        self,
        job_name: str,
        job: ReleaseJobConfig,
        build_number: int,
    ) -> BuildStatusResult:
        response = await self._client.get(
            f"{build_job_api_path(job.jenkins_path)}{build_number}/api/json",
            params={
                "tree": (
                    "building,result,duration,estimatedDuration,timestamp,url,number,"
                    "fullDisplayName"
                )
            },
        )
        if response.status_code != 200:
            raise _response_error(
                f"Failed to read Jenkins build {job_name} #{build_number}", response
            )
        data = response.json()
        return BuildStatusResult(
            job_name=job_name,
            build_number=int(data.get("number", build_number)),
            building=bool(data.get("building", False)),
            result=data.get("result"),
            timestamp_millis=data.get("timestamp"),
            duration_millis=data.get("duration"),
            estimated_duration_millis=data.get("estimatedDuration"),
            build_url=data.get("url") or build_job_url(self.settings.jenkins_url, job.jenkins_path),
            full_display_name=data.get("fullDisplayName"),
        )

    async def wait_for_build(
        self,
        job_name: str,
        job: ReleaseJobConfig,
        build_number: int | None = None,
        queue_id: int | None = None,
        timeout_seconds: int = 1800,
        poll_interval_seconds: int = 5,
    ) -> WaitBuildResult:
        if build_number is None and queue_id is None:
            raise ValueError("Either build_number or queue_id must be provided.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than 0.")

        deadline = time.monotonic() + timeout_seconds
        queue: QueueItemResult | None = None

        if build_number is None and queue_id is not None:
            while time.monotonic() < deadline:
                queue = await self.get_queue_item(queue_id)
                if queue.cancelled:
                    return WaitBuildResult(
                        job_name=job_name,
                        completed=True,
                        result="CANCELLED",
                        queue=queue,
                        build=None,
                        message=f"Jenkins queue item {queue_id} was cancelled.",
                    )
                if queue.build_number is not None:
                    build_number = queue.build_number
                    break
                await asyncio.sleep(poll_interval_seconds)

        if build_number is None:
            raise JenkinsTimeoutError(
                f"Timed out waiting for queue item {queue_id} to enter a build."
            )

        while time.monotonic() < deadline:
            build = await self.get_build_status(job_name, job, build_number)
            if not build.building:
                return WaitBuildResult(
                    job_name=job_name,
                    completed=True,
                    result=build.result or "UNKNOWN",
                    queue=queue,
                    build=build,
                    message=f"Jenkins build {job_name} #{build_number} finished.",
                )
            await asyncio.sleep(poll_interval_seconds)

        raise JenkinsTimeoutError(f"Timed out waiting for build {job_name} #{build_number}.")

    async def get_build_log(
        self,
        job_name: str,
        job: ReleaseJobConfig,
        build_number: int,
        start_offset: int = 0,
        max_chars: int = 12000,
    ) -> BuildLogResult:
        if start_offset < 0:
            raise ValueError("start_offset must be greater than or equal to 0.")
        if max_chars <= 0:
            raise ValueError("max_chars must be greater than 0.")

        response = await self._client.get(
            f"{build_job_api_path(job.jenkins_path)}{build_number}/logText/progressiveText",
            params={"start": start_offset},
        )
        if response.status_code != 200:
            raise _response_error(
                f"Failed to read Jenkins build log {job_name} #{build_number}", response
            )

        text = response.text
        has_more_data = response.headers.get("X-More-Data", "false").lower() == "true"
        header_next_offset = _optional_int(response.headers.get("X-Text-Size"))

        if len(text) > max_chars:
            text = text[:max_chars]
            next_offset = start_offset + len(text.encode(response.encoding or "utf-8", "replace"))
            has_more_data = True
        else:
            next_offset = header_next_offset
            if next_offset is None:
                next_offset = start_offset + len(
                    text.encode(response.encoding or "utf-8", "replace")
                )

        return BuildLogResult(
            job_name=job_name,
            build_number=build_number,
            start_offset=start_offset,
            next_offset=next_offset,
            has_more_data=has_more_data,
            text=text,
        )

    async def get_build_changes(
        self,
        job_name: str,
        job: ReleaseJobConfig,
        build_number: int,
    ) -> BuildChangesResult:
        response = await self._client.get(
            f"{build_job_api_path(job.jenkins_path)}{build_number}/api/json",
            params={
                "tree": (
                    "number,changeSet[kind,items[commitId,id,msg,date,timestamp,"
                    "author[fullName,absoluteUrl],affectedPaths,paths[file,editType]]],"
                    "changeSets[kind,items[commitId,id,msg,date,timestamp,"
                    "author[fullName,absoluteUrl],affectedPaths,paths[file,editType]]]"
                )
            },
        )
        if response.status_code != 200:
            raise _response_error(
                f"Failed to read Jenkins build changes {job_name} #{build_number}", response
            )

        data = response.json()
        changes = parse_build_changes(data)
        authors = sorted({change.author for change in changes if change.author})
        message = (
            "Jenkins changelog was found."
            if changes
            else (
                "No Jenkins changelog was found. The build may not have reached checkout yet, "
                "the job may not use SCM, or the pipeline may not record changelog."
            )
        )
        return BuildChangesResult(
            job_name=job_name,
            build_number=build_number,
            changes=changes,
            authors=authors,
            message=message,
        )

    async def get_build_branch(
        self,
        job_name: str,
        job: ReleaseJobConfig,
        build_number: int,
    ) -> BuildBranchResult:
        log = await self.get_build_log(
            job_name=job_name,
            job=job,
            build_number=build_number,
            start_offset=0,
            max_chars=12000,
        )
        branch, commit_hash, remote_url = parse_build_branch_from_log(log.text)
        message = (
            "Jenkins checkout branch was found."
            if branch or commit_hash
            else "No checkout branch was found in Jenkins console log."
        )
        return BuildBranchResult(
            job_name=job_name,
            build_number=build_number,
            branch=branch,
            commit_hash=commit_hash,
            remote_url=remote_url,
            build_url=f"{build_job_url(self.settings.jenkins_url, job.jenkins_path)}{build_number}/",
            message=message,
        )

    async def list_recent_releases(
        self,
        job_name: str,
        job: ReleaseJobConfig,
        limit: int = 10,
    ) -> RecentReleasesResult:
        if limit <= 0:
            raise ValueError("limit must be greater than 0.")
        if limit > 50:
            limit = 50

        response = await self._client.get(
            f"{build_job_api_path(job.jenkins_path)}api/json",
            params={
                "tree": (
                    f"builds[number,building,result,timestamp,duration,url,"
                    f"actions[parameters[name,value]]]{{0,{limit}}}"
                )
            },
        )
        if response.status_code != 200:
            raise _response_error(f"Failed to read recent Jenkins releases {job_name}", response)

        releases: list[RecentReleaseItem] = []
        for build in response.json().get("builds", []):
            parameters: list[RecentReleaseParameter] = []
            for action in build.get("actions") or []:
                for parameter in action.get("parameters") or []:
                    name = parameter.get("name")
                    if name is None:
                        continue
                    parameters.append(
                        RecentReleaseParameter(
                            name=str(name),
                            value="" if parameter.get("value") is None else str(parameter.get("value")),
                        )
                    )

            releases.append(
                RecentReleaseItem(
                    build_number=int(build.get("number")),
                    building=bool(build.get("building", False)),
                    result=build.get("result"),
                    timestamp_millis=build.get("timestamp"),
                    duration_millis=build.get("duration"),
                    build_url=build.get("url")
                    or f"{build_job_url(self.settings.jenkins_url, job.jenkins_path)}{build.get('number')}/",
                    parameters=parameters,
                )
            )

        return RecentReleasesResult(job_name=job_name, releases=releases)


def build_job_api_path(jenkins_path: str) -> str:
    path = jenkins_path.strip("/")
    if not path:
        raise ValueError("jenkins_path must not be empty")

    parts = [part for part in path.split("/") if part]
    if parts and parts[0] == "job":
        encoded_parts = [
            "job" if index % 2 == 0 else quote(part, safe="%")
            for index, part in enumerate(parts)
        ]
        return "/".join(encoded_parts).strip("/") + "/"

    encoded_segments = [quote(part, safe="%") for part in parts]
    return "".join(f"job/{segment}/" for segment in encoded_segments)


def build_job_url(jenkins_url: str, jenkins_path: str) -> str:
    return f"{jenkins_url.rstrip('/')}/{build_job_api_path(jenkins_path)}"


def parse_queue_id(queue_url: str) -> int | None:
    match = QUEUE_ID_RE.search(queue_url)
    if match is None:
        return None
    return int(match.group("queue_id"))


def parse_build_changes(data: Mapping[str, Any]) -> list[BuildChangeItem]:
    change_sets: list[Mapping[str, Any]] = []
    if isinstance(data.get("changeSet"), dict):
        change_sets.append(data["changeSet"])
    if isinstance(data.get("changeSets"), list):
        change_sets.extend(item for item in data["changeSets"] if isinstance(item, dict))

    changes: list[BuildChangeItem] = []
    for change_set in change_sets:
        kind = change_set.get("kind")
        for item in change_set.get("items") or []:
            if not isinstance(item, dict):
                continue
            author = item.get("author") or {}
            affected_files = parse_affected_files(item)
            changes.append(
                BuildChangeItem(
                    commit_id=item.get("commitId") or item.get("id"),
                    author=author.get("fullName") if isinstance(author, dict) else None,
                    message=item.get("msg") or "",
                    timestamp_millis=item.get("timestamp"),
                    affected_files=affected_files,
                    scm_kind=kind,
                )
            )
    return changes


def parse_build_branch_from_log(text: str) -> tuple[str | None, str | None, str | None]:
    branch: str | None = None
    commit_hash: str | None = None
    remote_url: str | None = None

    remote_match = REMOTE_URL_RE.search(text)
    if remote_match:
        remote_url = remote_match.group("url")

    checkout_match = CHECKOUT_BRANCH_RE.search(text)
    if checkout_match:
        commit_hash = checkout_match.group("commit")
        branch = normalize_jenkins_ref(checkout_match.group("ref"))

    if branch is None:
        rev_parse_match = REV_PARSE_BRANCH_RE.search(text)
        if rev_parse_match:
            branch = rev_parse_match.group("branch")

    return branch, commit_hash, remote_url


def normalize_jenkins_ref(ref: str) -> str:
    value = ref.strip()
    prefixes = ("refs/remotes/origin/", "origin/")
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def parse_affected_files(item: Mapping[str, Any]) -> list[str]:
    paths: list[str] = []
    for affected_path in item.get("affectedPaths") or []:
        if affected_path is not None:
            paths.append(str(affected_path))
    for path in item.get("paths") or []:
        if isinstance(path, dict) and path.get("file") is not None:
            paths.append(str(path["file"]))
    return sorted(set(paths))


def _absolute_url(base_url: str, maybe_url: str | None) -> str | None:
    if not maybe_url:
        return None
    return str(httpx.URL(base_url.rstrip("/") + "/").join(maybe_url))


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _response_error(message: str, response: httpx.Response) -> JenkinsAPIError:
    detail = response.text.strip()
    if len(detail) > 500:
        detail = detail[:500] + "..."
    return JenkinsAPIError(f"{message}: HTTP {response.status_code} {detail}")
