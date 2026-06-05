"""Project entrypoint for running Log Center with Python."""

import sys
from pathlib import Path


if sys.version_info < (3, 10):
    raise SystemExit("Log Center requires Python 3.10+. Please run: python3.10 center_run.py")

ROOT_DIR = Path(__file__).resolve().parent
CENTER_SOURCE_DIR = ROOT_DIR / "center"

if str(CENTER_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(CENTER_SOURCE_DIR))

from log_center.server import main


if __name__ == "__main__":
    main()
