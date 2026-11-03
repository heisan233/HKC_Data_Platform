"""Persistent training task and model-version metadata.

Training files remain in storage/models and storage/training-runs; SQLite only
tracks immutable request snapshots, progress, and validated artifact references.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.db.datasets import DatasetRepository


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TrainingRepository(DatasetRepository):
    def __init__(self, root: Path):
        super().__init__(root)

    def initialize(self) -> None:
        super().initialize()
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS training_jobs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    preset TEXT NOT NULL,
                    executor_id TEXT NOT NULL,
                    runtime_id TEXT NOT NULL DEFAULT 'lerobot',
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    snapshot_json TEXT NOT NULL,
                    split_json TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    latest_metric_json TEXT,
                    evaluation_json TEXT,
                    artifact_json TEXT,
                    error TEXT,
                    retry_of TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    deleted_by TEXT,
                    delete_requested_at TEXT,
                    delete_requested_by TEXT,
                    FOREIGN KEY (dataset_id) REFERENCES datasets(id)
                );
                CREATE INDEX IF NOT EXISTS training_jobs_dataset_idx
                    ON training_jobs(dataset_id, created_at);
            """)
            # Existing deployments have training_jobs without the recycle-bin fields.
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(training_jobs)")}
            for column in ("deleted_at", "deleted_by", "delete_requested_at",
                           "delete_requested_by"):
                if column not in columns:
                    db.execute(f"ALTER TABLE training_jobs ADD COLUMN {column} TEXT")
            if "runtime_id" not in columns:
                db.execute("ALTER TABLE training_jobs ADD COLUMN runtime_id "
                           "TEXT NOT NULL DEFAULT 'lerobot'")
            db.execute("CREATE INDEX IF NOT EXISTS training_jobs_deleted_idx "
                       "ON training_jobs(deleted_at, seq)")

    @staticmethod
    def _row(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        for field in ("snapshot", "split", "parameters", "latest_metric", "evaluation", "artifact"):
            raw = result.pop(f"{field}_json")
            result[field] = json.loads(raw) if raw else None
        return result

    def create(
        self, *, name: str, dataset_id: str, model_id: str,
        model_version: str, preset: str, executor_id: str,
        snapshot: dict, split: dict, parameters: dict,
        runtime_id: str = "lerobot",
        retry_of: str | None = None,
    ) -> dict:
        self.initialize()
        timestamp = _now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""INSERT INTO training_jobs
                (id,name,dataset_id,model_id,model_version,preset,executor_id,runtime_id,
                 status,phase,snapshot_json,split_json,parameters_json,retry_of,
                 created_at,updated_at)
                VALUES ('PENDING',?,?,?,?,?,?,?,'QUEUED','queue',?,?,?,?,?,?)""", (
                    name, dataset_id, model_id, model_version, preset, executor_id, runtime_id,
                    json.dumps(snapshot, ensure_ascii=False),
                    json.dumps(split, ensure_ascii=False),
                    json.dumps(parameters, ensure_ascii=False),
                    retry_of, timestamp, timestamp,
                ))
            task_id = f"TR{cursor.lastrowid:06d}"
            db.execute("UPDATE training_jobs SET id=? WHERE seq=?", (task_id, cursor.lastrowid))
        return self.get(task_id)

    def get(self, task_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT * FROM training_jobs WHERE id=?", (task_id,)).fetchone()
        return self._row(row)

    def list(self, *, status: str | None = None, model_id: str | None = None,
             dataset_id: str | None = None, deleted: bool | None = False) -> list[dict]:
        self.initialize()
        clauses = []
        values = []
        for field, value in (("status", status), ("model_id", model_id),
                             ("dataset_id", dataset_id)):
            if value:
                clauses.append(f"{field}=?")
                values.append(value)
        if deleted is not None:
            clauses.append("deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM training_jobs" + where + " ORDER BY seq DESC", values
            ).fetchall()
        return [self._row(row) for row in rows]

    def archive(self, task_id: str, username: str) -> dict | None:
        self.initialize()
        timestamp = _now()
        with self.connection() as db:
            cursor = db.execute("""UPDATE training_jobs
                SET deleted_at=?, deleted_by=?, delete_requested_at=NULL,
                    delete_requested_by=NULL, updated_at=?
                WHERE id=? AND deleted_at IS NULL
                  AND status IN ('COMPLETED','FAILED','CANCELED','INTERRUPTED')""",
                (timestamp, username, timestamp, task_id))
        return self.get(task_id) if cursor.rowcount else None

    def request_delete(self, task_id: str, username: str) -> dict | None:
        """Remember an active task's delete intent until cancellation is confirmed."""
        self.initialize()
        timestamp = _now()
        with self.connection() as db:
            cursor = db.execute("""UPDATE training_jobs
                SET delete_requested_at=?, delete_requested_by=?, updated_at=?
                WHERE id=? AND deleted_at IS NULL AND delete_requested_at IS NULL
                  AND status IN ('QUEUED','PREPARING','RUNNING','EVALUATING','PACKAGING')""",
                (timestamp, username, timestamp, task_id))
        return self.get(task_id) if cursor.rowcount else None

    def restore(self, task_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            cursor = db.execute("""UPDATE training_jobs
                SET deleted_at=NULL, deleted_by=NULL, updated_at=?
                WHERE id=? AND deleted_at IS NOT NULL""", (_now(), task_id))
        return self.get(task_id) if cursor.rowcount else None

    def purge(self, task_id: str) -> bool:
        """Remove only an archived, terminal task; AUTOINCREMENT keeps its ID retired."""
        self.initialize()
        with self.connection() as db:
            cursor = db.execute("""DELETE FROM training_jobs
                WHERE id=? AND deleted_at IS NOT NULL
                  AND status IN ('COMPLETED','FAILED','CANCELED','INTERRUPTED')""",
                (task_id,))
        return cursor.rowcount == 1

    def update(self, task_id: str, **fields) -> dict | None:
        allowed = {"status", "phase", "progress", "error", "started_at", "completed_at"}
        json_fields = {"latest_metric", "evaluation", "artifact"}
        changes = {key: value for key, value in fields.items() if key in allowed}
        changes.update({f"{key}_json": json.dumps(value, ensure_ascii=False)
                        for key, value in fields.items() if key in json_fields})
        if not changes:
            return self.get(task_id)
        changes["updated_at"] = _now()
        assignments = ",".join(f"{key}=?" for key in changes)
        with self.connection() as db:
            db.execute(f"UPDATE training_jobs SET {assignments} WHERE id=?",
                       [*changes.values(), task_id])
        return self.get(task_id)
