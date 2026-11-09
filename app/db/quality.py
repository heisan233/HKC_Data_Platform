"""Versioned quality plans, durable task state, and append-only review decisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.db.datasets import DatasetRepository
from app.db.mysql import ensure_schema


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class QualityRepository(DatasetRepository):
    def __init__(self, root: Path):
        super().__init__(root)

    def initialize(self) -> None:
        ensure_schema()

    def create_plan(self, name: str, robot_type: str, config: dict) -> dict:
        self.initialize()
        timestamp = _now()
        with self.connection() as db:
            cursor = db.execute(
                "INSERT INTO qc_plans(name,robot_type,created_at,updated_at) VALUES (?,?,?,?)",
                (name, robot_type, timestamp, timestamp),
            )
            plan_id = f"QP{cursor.lastrowid:06d}"
            db.execute("UPDATE qc_plans SET id=? WHERE seq=?", (plan_id, cursor.lastrowid))
            db.execute(
                "INSERT INTO qc_plan_versions VALUES (?,?,?,?)",
                (plan_id, 1, json.dumps(config, ensure_ascii=False), timestamp),
            )
        return self.get_plan(plan_id)

    def update_plan(self, plan_id: str, name: str, config: dict,
                    expected_version: int) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute(
                "SELECT current_version,archived_at FROM qc_plans WHERE id=? FOR UPDATE", (plan_id,)
            ).fetchone()
            if row is None:
                return None
            if row["archived_at"]:
                raise ValueError("已删除的方案不能修改")
            if row["current_version"] != expected_version:
                raise ValueError("方案已被其他操作更新，请刷新后重试")
            version = row["current_version"] + 1
            timestamp = _now()
            db.execute(
                "UPDATE qc_plans SET name=?,current_version=?,updated_at=? WHERE id=?",
                (name, version, timestamp, plan_id),
            )
            db.execute(
                "INSERT INTO qc_plan_versions VALUES (?,?,?,?)",
                (plan_id, version, json.dumps(config, ensure_ascii=False), timestamp),
            )
        return self.get_plan(plan_id)

    def get_plan(self, plan_id: str, version: int | None = None) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT * FROM qc_plans WHERE id=?", (plan_id,)).fetchone()
            if row is None:
                return None
            selected = row["current_version"] if version is None else version
            item = db.execute(
                "SELECT config_json FROM qc_plan_versions WHERE plan_id=? AND version=?",
                (plan_id, selected),
            ).fetchone()
        if item is None:
            return None
        return {**dict(row), "version": selected, "config": json.loads(item["config_json"])}

    def list_plans(self, robot_type: str | None = None) -> list[dict]:
        self.initialize()
        with self.connection() as db:
            if robot_type:
                rows = db.execute(
                    "SELECT * FROM qc_plans WHERE robot_type=? AND archived_at IS NULL "
                    "ORDER BY updated_at DESC,seq DESC",
                    (robot_type,),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM qc_plans WHERE archived_at IS NULL "
                                  "ORDER BY updated_at DESC,seq DESC").fetchall()
        return [self.get_plan(row["id"]) for row in rows]

    def archive_plan(self, plan_id: str) -> bool:
        self.initialize()
        with self.connection() as db:
            result = db.execute("UPDATE qc_plans SET archived_at=?,updated_at=? "
                                "WHERE id=? AND archived_at IS NULL",
                                (_now(), _now(), plan_id))
        return result.rowcount == 1

    def create_task(self, dataset_id: str, plan: dict, snapshot: dict, fingerprint: str) -> dict:
        self.initialize()
        task_id = "QC-" + uuid4().hex[:12].upper()
        timestamp = _now()
        with self.connection() as db:
            db.execute("""INSERT INTO qc_tasks
                (id,dataset_id,plan_id,plan_version,snapshot_json,raw_fingerprint,
                 status,phase,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (task_id, dataset_id, plan["id"], plan["version"],
                 json.dumps(snapshot, ensure_ascii=False), fingerprint,
                 "QUEUED", "pending", timestamp, timestamp),
            )
            db.execute("UPDATE datasets SET availability='unavailable',updated_at=? WHERE id=?",
                       (timestamp, dataset_id))
            db.execute("DELETE FROM dataset_status_labels WHERE dataset_id=?", (dataset_id,))
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT * FROM qc_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["snapshot"] = json.loads(item.pop("snapshot_json"))
        return item

    def list_tasks(self, dataset_id: str | None = None,
                   visibility: str = "visible") -> list[dict]:
        self.initialize()
        if visibility not in {"visible", "hidden", "all"}:
            raise ValueError("无效的质检任务列表范围")
        clauses = []
        values: list[str] = []
        if dataset_id:
            clauses.append("dataset_id=?")
            values.append(dataset_id)
        if visibility == "visible":
            clauses.append("hidden_at IS NULL AND archived_at IS NULL")
        elif visibility == "hidden":
            clauses.append("(hidden_at IS NOT NULL OR archived_at IS NOT NULL)")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as db:
            rows = db.execute(
                "SELECT id FROM qc_tasks" + where + " ORDER BY created_at DESC,seq DESC", values
            ).fetchall()
        return [self.get_task(row["id"]) for row in rows]

    def hidden_task_count(self) -> int:
        self.initialize()
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) FROM qc_tasks WHERE "
                              "hidden_at IS NOT NULL OR archived_at IS NOT NULL").fetchone()[0]

    def set_task(self, task_id: str, **fields) -> dict | None:
        allowed = {"status", "phase", "outcome", "error", "started_at", "completed_at", "archived_at"}
        changes = {key: value for key, value in fields.items() if key in allowed}
        if not changes:
            return self.get_task(task_id)
        changes["updated_at"] = _now()
        assignment = ",".join(f"{key}=?" for key in changes)
        with self.connection() as db:
            db.execute(f"UPDATE qc_tasks SET {assignment} WHERE id=?", [*changes.values(), task_id])
        return self.get_task(task_id)

    def set_task_visibility(self, task_id: str, hidden: bool) -> dict | None:
        """Change only task-list visibility; preserve task, report, and reviews."""
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT id FROM qc_tasks WHERE id=? FOR UPDATE", (task_id,)).fetchone()
            if row is None:
                return None
            timestamp = _now()
            db.execute("UPDATE qc_tasks SET hidden_at=?,archived_at=NULL,updated_at=? "
                       "WHERE id=?", (timestamp if hidden else None, timestamp, task_id))
        return self.get_task(task_id)

    def claim_task(self, task_id: str) -> bool:
        """Only one worker may move a queued task into execution."""
        self.initialize()
        timestamp = _now()
        with self.connection() as db:
            paused = db.execute(
                "SELECT vlm_paused FROM qc_runtime_control WHERE id=1 FOR UPDATE"
            ).fetchone()[0]
            row = db.execute("SELECT status,snapshot_json FROM qc_tasks WHERE id=? FOR UPDATE",
                             (task_id,)).fetchone()
            if row is None or row["status"] != "QUEUED":
                return False
            if paused and self.uses_vlm(json.loads(row["snapshot_json"])):
                return False
            cursor = db.execute("""UPDATE qc_tasks
                SET status='RUNNING',phase='doctor',started_at=?,updated_at=?
                WHERE id=? AND status='QUEUED'""", (timestamp, timestamp, task_id))
        return cursor.rowcount == 1

    @staticmethod
    def uses_vlm(snapshot: dict) -> bool:
        return any(row.get("enabled") is True for row in snapshot.get("vlm", {}).get("rules", []))

    def vlm_paused(self) -> bool:
        self.initialize()
        with self.connection() as db:
            return bool(db.execute(
                "SELECT vlm_paused FROM qc_runtime_control WHERE id=1"
            ).fetchone()[0])

    def pause_vlm(self) -> list[str]:
        """Block future VLM task claims, then report already running VLM tasks."""
        self.initialize()
        with self.connection() as db:
            db.execute("UPDATE qc_runtime_control SET vlm_paused=1 WHERE id=1")
            rows = db.execute(
                "SELECT id,snapshot_json FROM qc_tasks WHERE status='RUNNING'"
            ).fetchall()
        return [row["id"] for row in rows
                if self.uses_vlm(json.loads(row["snapshot_json"]))]

    def resume_vlm(self) -> None:
        self.initialize()
        with self.connection() as db:
            db.execute("UPDATE qc_runtime_control SET vlm_paused=0 WHERE id=1")

    def review(self, task_id: str, decision: str, reason: str) -> dict | None:
        timestamp = _now()
        with self.connection() as db:
            row = db.execute("SELECT status,dataset_id FROM qc_tasks WHERE id=? FOR UPDATE",
                             (task_id,)).fetchone()
            if row is None or row["status"] != "COMPLETED":
                return None
            latest = db.execute("SELECT id FROM qc_tasks WHERE dataset_id=? ORDER BY seq DESC LIMIT 1",
                                (row["dataset_id"],)).fetchone()
            if latest is None or latest["id"] != task_id:
                raise ValueError("只能复核该数据集最新的质检任务")
            db.execute(
                "INSERT INTO qc_reviews(task_id,decision,reason,created_at) VALUES (?,?,?,?)",
                (task_id, decision, reason, timestamp),
            )
            db.execute(
                "UPDATE qc_tasks SET decision=?,updated_at=? WHERE id=?",
                (decision, timestamp, task_id),
            )
            db.execute("UPDATE datasets SET availability=?,updated_at=? WHERE id=?",
                       ("available" if decision == "PASS" else "unavailable",
                        timestamp, row["dataset_id"]))
            db.execute("DELETE FROM dataset_status_labels WHERE dataset_id=?",
                       (row["dataset_id"],))
            if decision == "RECAPTURE":
                db.execute("INSERT INTO dataset_status_labels (dataset_id,label,created_at) VALUES (?,?,?)",
                           (row["dataset_id"], "待处理", timestamp))
        return self.get_task(task_id)

    def reviews(self, task_id: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT decision,reason,created_at FROM qc_reviews WHERE task_id=? ORDER BY id DESC",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def queued_task_ids(self) -> list[str]:
        self.initialize()
        with self.connection() as db:
            rows = db.execute("""SELECT id FROM qc_tasks WHERE status='QUEUED'
                ORDER BY created_at,id""").fetchall()
        return [row["id"] for row in rows]

    def interrupt_running(self) -> list[str]:
        self.initialize()
        timestamp = _now()
        with self.connection() as db:
            rows = db.execute("SELECT id FROM qc_tasks WHERE status='RUNNING'").fetchall()
            db.execute("""UPDATE qc_tasks SET status='INTERRUPTED',phase='interrupted',
                error='应用重启，任务已中断，请重新质检',completed_at=?,updated_at=?
                WHERE status='RUNNING'""", (timestamp, timestamp))
        return [row["id"] for row in rows]
