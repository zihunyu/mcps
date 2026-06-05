"""Utilities for saving downloaded logs."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from .models import TaskResult


SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class DownloadRecord:
    """Temporary metadata for a downloadable log file."""

    token: str
    file_path: Path
    file_name: str
    expires_at: datetime
    line_count: int
    size_bytes: int

    @property
    def expires_at_iso(self) -> str:
        return self.expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class DownloadRegistry:
    """In-memory token registry for temporary downloads."""

    def __init__(self, token_ttl_seconds: int) -> None:
        if token_ttl_seconds <= 0:
            raise ValueError("download.token_ttl_seconds must be > 0")
        self._ttl = timedelta(seconds=token_ttl_seconds)
        self._records: dict[str, DownloadRecord] = {}
        self._lock = RLock()

    def register(
        self,
        file_path: Path,
        line_count: int,
        size_bytes: int,
    ) -> DownloadRecord:
        """Create a temporary token for a saved file."""

        resolved_path = file_path.resolve()
        now = datetime.now(timezone.utc)
        record = DownloadRecord(
            token=secrets.token_urlsafe(32),
            file_path=resolved_path,
            file_name=resolved_path.name,
            expires_at=now + self._ttl,
            line_count=line_count,
            size_bytes=size_bytes,
        )
        with self._lock:
            self._cleanup_expired_locked(now)
            self._records[record.token] = record
        return record

    def get(self, token: str) -> DownloadRecord | None:
        """Return a valid record by token, or None if missing or expired."""

        now = datetime.now(timezone.utc)
        with self._lock:
            self._cleanup_expired_locked(now)
            record = self._records.get(token)
            if record is None:
                return None
            if record.expires_at <= now:
                self._records.pop(token, None)
                return None
            if not record.file_path.exists():
                self._records.pop(token, None)
                return None
            return record

    def _cleanup_expired_locked(self, now: datetime) -> None:
        expired_tokens = [token for token, record in self._records.items() if record.expires_at <= now]
        for token in expired_tokens:
            self._records.pop(token, None)


def save_downloaded_log(
    download_dir: Path,
    server_id: str,
    log_name: str,
    result: TaskResult,
) -> tuple[Path, int]:
    """Save task lines to a safe local file and return path plus size."""

    root = download_dir.expanduser().resolve()
    safe_server_id = sanitize_path_part(server_id)
    safe_log_name = sanitize_path_part(log_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    file_name = f"{safe_log_name}-{timestamp}-{result.task_id}.log"
    server_dir = (root / safe_server_id).resolve()
    file_path = (server_dir / file_name).resolve()

    if not file_path.is_relative_to(root):
        raise ValueError("download path escapes configured download directory")

    server_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(result.lines)
    if text:
        text += "\n"
    with file_path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(text)
    return file_path, file_path.stat().st_size


def sanitize_path_part(value: str) -> str:
    """Return a filesystem-safe path segment."""

    cleaned = SAFE_NAME_PATTERN.sub("_", value.strip()).strip("._-")
    return cleaned or "unnamed"
