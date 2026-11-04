"""MySQL state for format conversion jobs; outputs are keyed by CV task IDs."""

from __future__ import annotations

import json
from pathlib import Path

from app.db.datasets import DatasetRepository
from app.db.mysql import ensure_schema


class ConversionRepository:
    def __init__(self, root: Path):
        self.datasets = DatasetRepository(root)

    def initialize(self) -> None:
        ensure_schema()

    @staticmethod
    def _row(row) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["files"] = json.loads(item.pop("files_json"))
        item.pop("result_dataset_id", None)  # Retired column in older databases.
        return item

    def get(self, task_id: str) -> dict | None:
        self.initialize()
        with self.datasets.connection() as db:
            row = db.execute("SELECT * FROM conversion_jobs WHERE id=?", (task_id,)).fetchone()
        return self._row(row)

    def list(self, limit: int = 50) -> list[dict]:
        self.initialize()
        with self.datasets.connection() as db:
            rows = db.execute("""SELECT * FROM conversion_jobs
                ORDER BY created_at DESC, id DESC LIMIT ?""", (limit,)).fetchall()
        return [self._row(row) for row in rows]

    def list_completed(self) -> list[dict]:
        self.initialize()
        with self.datasets.connection() as db:
            rows = db.execute("""SELECT * FROM conversion_jobs WHERE status='COMPLETED'
                ORDER BY finished_at DESC, id DESC""").fetchall()
        return [self._row(row) for row in rows]

    def create(self, task: dict) -> None:
        self.initialize()
        with self.datasets.connection() as db:
            db.execute("""INSERT INTO conversion_jobs
                (id, profile_id, source_dataset_id, source_name, source_fingerprint,
                 status, completed_episodes, total_episodes, files_json, error,
                 created_at, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task["id"], task["profile_id"], task["source_dataset_id"],
                 task["source_name"], task["source_fingerprint"], task["status"],
                 task["completed_episodes"], task["total_episodes"],
                 json.dumps(task["files"]), task["error"], task["created_at"],
                 task["started_at"], task["finished_at"]))

    def update(self, task_id: str, **changes: object) -> None:
        allowed = {"status", "completed_episodes", "error", "started_at", "finished_at"}
        if not changes or set(changes) - allowed:
            raise ValueError("不支持更新该转换任务字段")
        self.initialize()
        with self.datasets.connection() as db:
            db.execute(
                f"UPDATE conversion_jobs SET {', '.join(f'{key}=?' for key in changes)} WHERE id=?",
                (*changes.values(), task_id),
            )

    def interrupted(self, timestamp: str) -> None:
        self.initialize()
        with self.datasets.connection() as db:
            db.execute("""UPDATE conversion_jobs SET status='FAILED',
                error='服务重启中断了转换，请重新创建任务', finished_at=?
                WHERE status='RUNNING'""", (timestamp,))

    def complete(self, task: dict, manifest: dict, timestamp: str) -> None:
        """Mark a published output complete without entering the DS registry."""
        self.initialize()
        with self.datasets.connection() as db:
            db.execute("""UPDATE conversion_jobs SET status='COMPLETED',
                completed_episodes=?, files_json=?,
                error=NULL, finished_at=? WHERE id=?""",
                (manifest["total_episodes"], json.dumps(manifest["files"]),
                 timestamp, task["id"]))
