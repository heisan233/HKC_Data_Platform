"""SQLite state for format conversion jobs; outputs are keyed by CV task IDs."""

from __future__ import annotations

import json
from pathlib import Path

from app.db.datasets import DatasetRepository


class ConversionRepository:
    def __init__(self, root: Path):
        self.datasets = DatasetRepository(root)

    def initialize(self) -> None:
        self.datasets.initialize()
        with self.datasets.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS conversion_jobs (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                source_dataset_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                completed_episodes INTEGER NOT NULL,
                total_episodes INTEGER NOT NULL,
                files_json TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS conversion_jobs_created_idx
                ON conversion_jobs(created_at DESC)""")

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

    def import_legacy(self, task: dict) -> bool:
        """Copy a former JSON task once; the JSON file remains as backup."""
        self.initialize()
        with self.datasets.connection() as db:
            result = db.execute("""INSERT OR IGNORE INTO conversion_jobs
                (id, profile_id, source_dataset_id, source_name, source_fingerprint,
                 status, completed_episodes, total_episodes, files_json, error,
                 created_at, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task["id"], "lerobot-v3-hdf5", task["source_dataset_id"],
                 task.get("source_name", task["source_dataset_id"]),
                 task.get("source_fingerprint", ""), task["status"],
                 task.get("completed_episodes", 0), task.get("total_episodes", 0),
                 json.dumps(task.get("files", [])), task.get("error"),
                 task["created_at"], task.get("started_at"), task.get("finished_at")))
        return bool(result.rowcount)
