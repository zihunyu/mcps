"""Safe log reading utilities for the Python 3.6 compatible Log Agent."""


def read_tail_lines(path, lines, keyword=None):
    if lines < 1:
        raise ValueError("lines must be >= 1")
    if not path.exists():
        raise FileNotFoundError("log file not found: {0}".format(path))
    if not path.is_file():
        raise ValueError("log path is not a file: {0}".format(path))

    content = _tail(path, lines)
    if keyword:
        content = [line for line in content if keyword in line]
    return content


def _tail(path, lines):
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
