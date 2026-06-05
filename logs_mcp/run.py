"""Project entrypoint for running Log MCP with Python."""

import sys
from pathlib import Path


if sys.version_info < (3, 10):
    raise SystemExit("Log MCP requires Python 3.10+. Please run: python3.10 run.py")

ROOT_DIR = Path(__file__).resolve().parent
SOURCE_DIR = ROOT_DIR / "mcp"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from log_mcp.server import main


if __name__ == "__main__":
    main()
