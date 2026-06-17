from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import BackgroundReleaseTask


logger = logging.getLogger(__name__)


class ReleaseTaskStore:
    def __init__(self, path: Path):
        self.path = path

    def load_latest(self) -> dict[str, BackgroundReleaseTask]:
        if not self.path.exists():
            return {}

        tasks: dict[str, BackgroundReleaseTask] = {}
        with self.path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    task = BackgroundReleaseTask.model_validate(json.loads(line))
                except Exception as exc:
                    logger.warning(
                        "Ignoring invalid release task JSONL line %s in %s: %s",
                        line_number,
                        self.path,
                        exc,
                    )
                    continue
                tasks[task.task_id] = task
        return tasks

    def append(self, task: BackgroundReleaseTask) -> None:
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = task.model_dump(mode="json")
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
