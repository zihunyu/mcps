"""Project entrypoint for running the Python 3.7 compatible Log Center."""

import sys
from pathlib import Path


if sys.version_info < (3, 7):
    raise SystemExit("Compatible Log Center requires Python 3.7+.")

ROOT_DIR = Path(__file__).resolve().parent
CENTER_SOURCE_DIR = ROOT_DIR / "center_python3.7"

if str(CENTER_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(CENTER_SOURCE_DIR))

from log_center_compat.server import main


if __name__ == "__main__":
    main()
