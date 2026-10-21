"""Persistent state for LeRobot to HDF5 conversion tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.db.datasets import DatasetRepository


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ConversionRepository(DatasetRepository):
    def __init__(self, root: Path):
        super().__init__(root)

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversion_tasks (
                    id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    completed_episodes INTEGER NOT NULL DEFAULT 0,
                    total_episodes INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS conversion_tasks_created_idx
                    ON conversion_tasks(created_at DESC);
            """)

    def create(self, source_name: str, fingerprint: str, total_episodes: int) -> dict:
        self.initialize()
        task_id = "CV-" + uuid4().hex[:12].upper()
        with self.connection() as db:
            db.execute(
                "INSERT INTO conversion_tasks "
                "(id,source_name,source_fingerprint,status,total_episodes,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (task_id, source_name, fingerprint, "QUEUED", total_episodes, now()),
            )
        return self.get(task_id)

    def get(self, task_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT * FROM conversion_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list(self, limit: int = 50) -> list[dict]:
        self.initialize()
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM conversion_tasks ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def queued_ids(self) -> list[str]:
        self.initialize()
        with self.connection() as db:
            return [row[0] for row in db.execute(
                "SELECT id FROM conversion_tasks WHERE status='QUEUED' ORDER BY created_at,rowid"
            )]

    def interrupt_running(self) -> None:
        self.initialize()
        with self.connection() as db:
            db.execute(
                "UPDATE conversion_tasks SET status='FAILED',error=?,finished_at=? "
                "WHERE status='RUNNING'",
                ("服务重启中断了转换，请重新创建任务", now()),
            )

    def start(self, task_id: str) -> bool:
        with self.connection() as db:
            result = db.execute(
                "UPDATE conversion_tasks SET status='RUNNING',started_at=? "
                "WHERE id=? AND status='QUEUED'", (now(), task_id)
            )
        return result.rowcount == 1

    def progress(self, task_id: str, completed: int) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE conversion_tasks SET completed_episodes=? WHERE id=? AND status='RUNNING'",
                (completed, task_id),
            )

    def finish(self, task_id: str, error: str | None = None) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE conversion_tasks SET status=?,error=?,finished_at=? "
                "WHERE id=? AND status='RUNNING'",
                ("FAILED" if error else "COMPLETED", error, now(), task_id),
            )
