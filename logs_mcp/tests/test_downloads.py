from __future__ import annotations

from pathlib import Path

from log_mcp.downloads import DownloadRegistry


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
