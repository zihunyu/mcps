"""Project entrypoint for running the Python 3.6 compatible Log Agent."""

import sys
from pathlib import Path


if sys.version_info < (3, 6):
    raise SystemExit("Compatible Log Agent requires Python 3.6+.")

ROOT_DIR = Path(__file__).resolve().parent
AGENT_SOURCE_DIR = ROOT_DIR / "agent_python3.6"

if str(AGENT_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_SOURCE_DIR))

from log_agent_compat.worker import main


if __name__ == "__main__":
    main()
