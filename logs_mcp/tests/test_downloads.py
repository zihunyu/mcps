from __future__ import annotations

from pathlib import Path
import os
import time

from log_mcp.downloads import DownloadRegistry, cleanup_downloads


def test_download_registry_returns_none_for_expired_token(tmp_path: Path) -> None:
    file_path = tmp_path / "app.log"
    file_path.write_text("ERROR xxx\n", encoding="utf-8")
    registry = DownloadRegistry(token_ttl_seconds=1)
    record = registry.register(file_path=file_path, line_count=1, size_bytes=file_path.stat().st_size)

    registry._records[record.token] = record.__class__(
        token=record.token,
        file_path=record.file_path,
        file_name=record.file_name,
        expires_at=record.expires_at.replace(year=2000),
        line_count=record.line_count,
        size_bytes=record.size_bytes,
    )

    assert registry.get(record.token) is None


def test_cleanup_downloads_removes_expired_files(tmp_path: Path) -> None:
    old_file = tmp_path / "server" / "old.log"
    new_file = tmp_path / "server" / "new.log"
    old_file.parent.mkdir()
    old_file.write_text("old\n", encoding="utf-8")
    new_file.write_text("new\n", encoding="utf-8")
    old_time = time.time() - 7200
    os.utime(old_file, (old_time, old_time))

    result = cleanup_downloads(tmp_path, retention_seconds=3600, max_total_size_mb=1024)

    assert result["removed_files"] == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_cleanup_downloads_enforces_total_size(tmp_path: Path) -> None:
    first = tmp_path / "server" / "first.log"
    second = tmp_path / "server" / "second.log"
    first.parent.mkdir()
    first.write_bytes(b"a" * 800_000)
    second.write_bytes(b"b" * 800_000)
    first_time = time.time() - 60
    second_time = time.time()
    os.utime(first, (first_time, first_time))
    os.utime(second, (second_time, second_time))

    result = cleanup_downloads(tmp_path, retention_seconds=3600, max_total_size_mb=1)

    assert result["removed_files"] == 1
    assert not first.exists()
    assert second.exists()
