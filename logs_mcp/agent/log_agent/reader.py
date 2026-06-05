"""Safe log reading utilities for Agent."""

from __future__ import annotations

from pathlib import Path


def read_tail_lines(path: Path, lines: int, keyword: str | None = None) -> list[str]:
    """Read the last N lines from an allow-listed log file."""

    if lines < 1:
        raise ValueError("lines must be >= 1")
    if not path.exists():
        raise FileNotFoundError(f"log file not found: {path}")
    if not path.is_file():
        raise ValueError(f"log path is not a file: {path}")

    content = _tail(path, lines)
    if keyword:
        content = [line for line in content if keyword in line]
    return content


def _tail(path: Path, lines: int) -> list[str]:
    # Read from the end in blocks so large log files do not need to be loaded fully.
    block_size = 8192
    data = bytearray()
    newline_count = 0

    with path.open("rb") as file:
        file.seek(0, 2)
        position = file.tell()
        while position > 0 and newline_count <= lines:
            read_size = min(block_size, position)
            position -= read_size
            file.seek(position)
            chunk = file.read(read_size)
            data[:0] = chunk
            newline_count += chunk.count(b"\n")

    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]
