"""SQLite links between registered datasets and their collecting robot."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.db.datasets import DatasetRepository
from app.db.robots import RobotRepository


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _dataset_ids(values: list[str]) -> list[str]:
    result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if not result:
        raise ValueError("至少选择一个数据集")
    if len(result) > 100:
        raise ValueError("一次最多关联 100 个数据集")
    if any(not value.startswith("DS") or not value[2:].isdigit() for value in result):
        raise ValueError("数据集编号格式无效")
    return result


class DatasetRobotRepository:
    """Own the optional one-dataset-to-one-robot management relationship.

    The relation is platform metadata only. It never changes LeRobot files or
    treats the free-text ``robot_type`` field as an instance identifier.
    """

    def __init__(self, root: Path):
        self.root = root
        self.datasets = DatasetRepository(root)
        self.robots = RobotRepository(root)

    def initialize(self) -> None:
        self.datasets.initialize()
        self.robots.initialize()
        with self.datasets.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS dataset_robot_links (
                    dataset_id TEXT PRIMARY KEY,
                    robot_id TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (dataset_id) REFERENCES datasets(id) ON DELETE CASCADE,
                    FOREIGN KEY (robot_id) REFERENCES robots(id) ON DELETE RESTRICT
                );
                CREATE INDEX IF NOT EXISTS dataset_robot_links_robot_idx
                    ON dataset_robot_links(robot_id, dataset_id);
                """
            )

    @staticmethod
    def _row(row) -> dict | None:
        return dict(row) if row is not None else None

    def get(self, dataset_id: str) -> dict | None:
        self.initialize()
        with self.datasets.connection() as db:
            row = db.execute(
                """SELECT links.*, robots.name AS robot_name,
                          robots.robot_type AS robot_type,
                          robots.status AS robot_status
                   FROM dataset_robot_links AS links
                   JOIN robots ON robots.id=links.robot_id
                   WHERE links.dataset_id=?""",
                (dataset_id,),
            ).fetchone()
        return self._row(row)

    def list(self) -> list[dict]:
        self.initialize()
        with self.datasets.connection() as db:
            rows = db.execute(
                """SELECT links.*, robots.name AS robot_name,
                          robots.robot_type AS robot_type,
                          robots.status AS robot_status
                   FROM dataset_robot_links AS links
                   JOIN robots ON robots.id=links.robot_id
                   ORDER BY links.dataset_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def bind(self, robot_id: str, dataset_ids: list[str], username: str) -> list[dict]:
        """Bind or rebind datasets atomically to one non-inactive robot."""
        ids = _dataset_ids(dataset_ids)
        actor = username.strip()[:100] or "unknown"
        self.initialize()
        timestamp = _now()
        with self.datasets.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            robot = db.execute(
                "SELECT id,status FROM robots WHERE id=?", (robot_id,)
            ).fetchone()
            if robot is None:
                raise ValueError("机器人不存在")
            if robot["status"] == "inactive":
                raise ValueError("已停用机器人不能关联新的数据集")
            placeholders = ",".join("?" for _ in ids)
            existing = {
                row["id"] for row in db.execute(
                    f"SELECT id FROM datasets WHERE id IN ({placeholders})", ids
                ).fetchall()
            }
            missing = [dataset_id for dataset_id in ids if dataset_id not in existing]
            if missing:
                raise ValueError("数据集不存在：" + "、".join(missing))
            for dataset_id in ids:
                db.execute(
                    """INSERT INTO dataset_robot_links
                       (dataset_id,robot_id,linked_at,updated_at,updated_by)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(dataset_id) DO UPDATE SET
                         robot_id=excluded.robot_id,
                         updated_at=excluded.updated_at,
                         updated_by=excluded.updated_by""",
                    (dataset_id, robot_id, timestamp, timestamp, actor),
                )
        return [self.get(dataset_id) for dataset_id in ids]

    def unlink(self, robot_id: str, dataset_id: str) -> bool:
        """Remove only a link currently owned by the selected robot."""
        self.initialize()
        with self.datasets.connection() as db:
            result = db.execute(
                "DELETE FROM dataset_robot_links WHERE dataset_id=? AND robot_id=?",
                (dataset_id, robot_id),
            )
        return result.rowcount == 1

    def inherit(self, dataset_id: str, source_dataset_ids: list[str]) -> dict | None:
        """Copy provenance only when every source has the same robot.

        Automatic inheritance is allowed for inactive robots because it
        preserves an existing collection lineage rather than recording a new
        manual collection assignment.
        """
        sources = list(dict.fromkeys(source_dataset_ids))
        if not sources:
            return None
        self.initialize()
        placeholders = ",".join("?" for _ in sources)
        with self.datasets.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                f"""SELECT dataset_id,robot_id FROM dataset_robot_links
                    WHERE dataset_id IN ({placeholders})""",
                sources,
            ).fetchall()
            robot_ids = {row["robot_id"] for row in rows}
            if len(rows) != len(sources) or len(robot_ids) != 1:
                return None
            timestamp = _now()
            db.execute(
                """INSERT INTO dataset_robot_links
                   (dataset_id,robot_id,linked_at,updated_at,updated_by)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(dataset_id) DO NOTHING""",
                (dataset_id, robot_ids.pop(), timestamp, timestamp, "system:derived"),
            )
        return self.get(dataset_id)
