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


def cleanup_downloads(
    download_dir: Path,
    retention_seconds: int,
    max_total_size_mb: int,
) -> dict[str, int]:
    """Remove old or excessive downloaded logs under the configured directory."""

    root = download_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    retention = timedelta(seconds=retention_seconds)
    removed_files = 0
    removed_bytes = 0
    files: list[tuple[Path, float, int]] = []

    for file_path in root.rglob("*.log"):
        if not file_path.is_file():
            continue
        resolved_path = file_path.resolve()
        if not resolved_path.is_relative_to(root):
            continue
        stat = resolved_path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if now - modified_at > retention:
            size = stat.st_size
            resolved_path.unlink(missing_ok=True)
            removed_files += 1
            removed_bytes += size
            continue
        files.append((resolved_path, stat.st_mtime, stat.st_size))

    max_total_size = max_total_size_mb * 1024 * 1024
    total_size = sum(size for _, _, size in files)
    for file_path, _, size in sorted(files, key=lambda item: item[1]):
        if total_size <= max_total_size:
            break
        file_path.unlink(missing_ok=True)
        total_size -= size
        removed_files += 1
        removed_bytes += size

    _remove_empty_dirs(root)
    return {"removed_files": removed_files, "removed_bytes": removed_bytes}


def sanitize_path_part(value: str) -> str:
    """Return a filesystem-safe path segment."""

    cleaned = SAFE_NAME_PATTERN.sub("_", value.strip()).strip("._-")
    return cleaned or "unnamed"


def _remove_empty_dirs(root: Path) -> None:
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            continue
