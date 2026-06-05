"""Project entrypoint for running Log Agent with Python."""

import sys
from pathlib import Path


if sys.version_info < (3, 10):
    raise SystemExit("Log Agent requires Python 3.10+. Please run: python3.10 agent_run.py")

ROOT_DIR = Path(__file__).resolve().parent
AGENT_SOURCE_DIR = ROOT_DIR / "agent"

if str(AGENT_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_SOURCE_DIR))

from log_agent.worker import main


if __name__ == "__main__":
    main()
