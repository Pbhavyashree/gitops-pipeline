"""
In-memory task store.

Kept deliberately simple: this repository is about the delivery pipeline, not
persistence. Swapping this for PostgreSQL would not change any of the Helm,
ArgoCD or CI configuration, which is rather the point of the separation.
"""

import uuid
from datetime import UTC, datetime


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, dict] = {}

    def create(self, title: str, priority: str) -> dict:
        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "title": title,
            "priority": priority,
            "done": False,
            "created_at": datetime.now(UTC),
        }
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> dict | None:
        return self._tasks.get(task_id)

    def list(self, done: bool | None = None) -> list[dict]:
        tasks = list(self._tasks.values())
        if done is not None:
            tasks = [t for t in tasks if t["done"] == done]
        return sorted(tasks, key=lambda t: t["created_at"])

    def update(self, task_id: str, **fields) -> dict | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        for key, value in fields.items():
            if value is not None and key in task:
                task[key] = value
        return task

    def delete(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None

    def count(self) -> int:
        return len(self._tasks)
