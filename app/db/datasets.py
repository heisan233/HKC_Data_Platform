"""Unified MySQL registry for datasets stored by the platform.

The registry owns stable ``DS000001`` identifiers and maps them to one direct
child of ``storage/data`` or a retained legacy directory. Dataset bytes stay on disk;
MySQL only stores management metadata and lineage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.db.mysql import IntegrityError, Row, connection, ensure_schema

STORAGE_AREAS = frozenset({"raw", "derived", "data"})
AVAILABILITY = frozenset({"available", "unavailable"})
STATUS_LABELS = frozenset({"待质检", "待处理"})
DATASET_NAME_CONFLICT = "数据集名称已存在，请使用其他名称"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _area(value: str) -> str:
    value = value.strip().lower()
    if value not in STORAGE_AREAS:
        raise ValueError("存储区域只能是 raw、derived 或 data")
    return value


def _child_name(value: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."} or Path(value).name != value or value.startswith("."):
        raise ValueError("数据集目录名称不安全")
    return value


class DatasetRepository:
    def __init__(self, root: Path):
        self.root = root

    def connection(self):
        return connection()

    def initialize(self) -> None:
        """Ensure the fresh MySQL schema exists without scanning dataset folders."""
        ensure_schema()

    @staticmethod
    def _ensure_name_available(db, name: str, exclude_id: str | None = None) -> None:
        key = name.strip().casefold()
        rows = db.execute("SELECT id,name FROM datasets").fetchall()
        if any(
            row["id"] != exclude_id and str(row["name"]).strip().casefold() == key
            for row in rows
        ):
            raise ValueError(DATASET_NAME_CONFLICT)

    def ensure_name_available(self, name: str, exclude_id: str | None = None) -> None:
        self.initialize()
        with self.connection() as db:
            self._ensure_name_available(db, name, exclude_id)

    def list(self, storage_area: str | None = None, *, availability: str | None = None,
             deleted: bool | None = False) -> list[dict]:
        self.initialize()
        if availability is not None and availability not in AVAILABILITY:
            raise ValueError("数据集可用性不合法")
        with self.connection() as db:
            area = _area(storage_area) if storage_area else None
            clauses = []
            params = []
            if area:
                clauses.append("storage_area=?")
                params.append(area)
            if availability:
                clauses.append("availability=?")
                params.append(availability)
            if deleted is not None:
                clauses.append("deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL")
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute("SELECT * FROM datasets" + where + " ORDER BY seq DESC", params).fetchall()
            labels = db.execute("SELECT dataset_id,label FROM dataset_status_labels").fetchall()
        by_id: dict[str, list[str]] = {}
        for label in labels:
            by_id.setdefault(label["dataset_id"], []).append(label["label"])
        return [self._row(row, by_id.get(row["id"], [])) for row in rows]

    def get(self, dataset_id: str, *, include_deleted: bool = False) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT * FROM datasets WHERE id=?" +
                             ("" if include_deleted else " AND deleted_at IS NULL"),
                             (dataset_id,)).fetchone()
            labels = db.execute("SELECT label FROM dataset_status_labels WHERE dataset_id=?",
                                (dataset_id,)).fetchall() if row else []
        return self._row(row, [label["label"] for label in labels]) if row else None

    def by_location(self, storage_area: str, storage_relpath: str) -> dict | None:
        self.initialize()
        area = _area(storage_area)
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM datasets WHERE storage_area=? AND storage_relpath=?",
                (area, _child_name(storage_relpath)),
            ).fetchone()
        return self.get(row["id"]) if row else None

    def register(
        self,
        storage_area: str,
        storage_relpath: str,
        name: str,
        robot_type: str,
        digest: str,
        source_dataset_ids: list[str] | None = None,
        *,
        reserved_id: str | None = None,
    ) -> dict:
        self.initialize()
        area = _area(storage_area)
        relpath = _child_name(storage_relpath)
        sources = list(dict.fromkeys(source_dataset_ids or []))
        clean_name = name.strip()
        try:
            with self.connection() as db:
                self._ensure_name_available(db, clean_name)
                if db.execute(
                    "SELECT 1 FROM datasets WHERE storage_area=? AND storage_relpath=?",
                    (area, relpath),
                ).fetchone():
                    raise ValueError("此目录已经登记")
                if reserved_id is None:
                    seq = db.execute("INSERT INTO dataset_numbers () VALUES ()").lastrowid
                    dataset_id = f"DS{seq:06d}"
                else:
                    if area != "data" or relpath != reserved_id or not reserved_id.startswith("DS"):
                        raise ValueError("新数据集编号与目录不一致")
                    seq = int(reserved_id[2:])
                    if db.execute("SELECT 1 FROM dataset_numbers WHERE seq=?", (seq,)).fetchone() is None:
                        raise ValueError("数据集编号未预留")
                    dataset_id = reserved_id
                timestamp = utc_now()
                db.execute("""INSERT INTO datasets
                    (id, seq, storage_area, storage_relpath, name, robot_type,
                     description, tags_json, source_dataset_ids_json, info_sha256,
                     registered_at, updated_at, availability)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unavailable')""",
                    (dataset_id, seq, area, relpath, clean_name, robot_type, "", "[]",
                     json.dumps(sources, ensure_ascii=False), digest, timestamp, timestamp))
                db.execute("INSERT INTO dataset_status_labels (dataset_id,label,created_at) VALUES (?,?,?)",
                           (dataset_id, "待质检", timestamp))
        except IntegrityError as exc:
            raise ValueError("目录或编号已被其他登记占用") from exc
        return self.get(dataset_id)

    def reserve_id(self) -> str:
        self.initialize()
        with self.connection() as db:
            seq = db.execute("INSERT INTO dataset_numbers () VALUES ()").lastrowid
        return f"DS{seq:06d}"

    def set_availability(self, dataset_id: str, availability: str) -> dict | None:
        if availability not in AVAILABILITY:
            raise ValueError("数据集可用性不合法")
        self.initialize()
        with self.connection() as db:
            result = db.execute("UPDATE datasets SET availability=?, updated_at=? WHERE id=?",
                                (availability, utc_now(), dataset_id))
        return self.get(dataset_id) if result.rowcount else None

    def mark_for_qc(self, dataset_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            result = db.execute("UPDATE datasets SET availability='unavailable',updated_at=? WHERE id=?",
                                (utc_now(), dataset_id))
            if not result.rowcount:
                return None
            db.execute("DELETE FROM dataset_status_labels WHERE dataset_id=?", (dataset_id,))
            db.execute("INSERT INTO dataset_status_labels (dataset_id,label,created_at) VALUES (?,?,?)",
                       (dataset_id, "待质检", utc_now()))
        return self.get(dataset_id)

    def set_status_labels(self, dataset_id: str, labels: list[str]) -> dict | None:
        labels = list(dict.fromkeys(labels))
        if any(label not in STATUS_LABELS for label in labels):
            raise ValueError("状态标签不合法")
        self.initialize()
        with self.connection() as db:
            if db.execute("SELECT 1 FROM datasets WHERE id=?", (dataset_id,)).fetchone() is None:
                return None
            db.execute("DELETE FROM dataset_status_labels WHERE dataset_id=?", (dataset_id,))
            for label in labels:
                db.execute("INSERT INTO dataset_status_labels (dataset_id,label,created_at) VALUES (?,?,?)",
                           (dataset_id, label, utc_now()))
        return self.get(dataset_id)

    def unregister(self, dataset_id: str) -> bool:
        """Remove a newly-created row when its file transaction rolls back."""

        self.initialize()
        with self.connection() as db:
            result = db.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))
        return bool(result.rowcount)

    def trash(self, dataset_id: str, username: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            result = db.execute("""UPDATE datasets SET deleted_at=?,deleted_by=?,updated_at=?
                WHERE id=? AND deleted_at IS NULL""",
                (utc_now(), username, utc_now(), dataset_id))
        return self.get(dataset_id, include_deleted=True) if result.rowcount else None

    def restore(self, dataset_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            result = db.execute("""UPDATE datasets SET deleted_at=NULL,deleted_by=NULL,
                purge_requested_at=NULL,updated_at=?
                WHERE id=? AND deleted_at IS NOT NULL AND purge_requested_at IS NULL""",
                (utc_now(), dataset_id))
        return self.get(dataset_id) if result.rowcount else None

    def request_purge(self, dataset_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            result = db.execute("""UPDATE datasets SET purge_requested_at=?,updated_at=?
                WHERE id=? AND deleted_at IS NOT NULL AND purge_requested_at IS NULL""",
                (utc_now(), utc_now(), dataset_id))
        return self.get(dataset_id, include_deleted=True) if result.rowcount else None

    def finish_purge(self, dataset_id: str) -> bool:
        """Remove only an archived DS after its files have been cleared."""
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT id FROM datasets WHERE id=? AND purge_requested_at IS NOT NULL FOR UPDATE",
                             (dataset_id,)).fetchone()
            if row is None:
                return False
            db.execute("DELETE r FROM qc_reviews r JOIN qc_tasks t ON r.task_id=t.id WHERE t.dataset_id=?",
                       (dataset_id,))
            db.execute("DELETE FROM qc_tasks WHERE dataset_id=?", (dataset_id,))
            db.execute("DELETE FROM dataset_robot_links WHERE dataset_id=?", (dataset_id,))
            db.execute("DELETE FROM dataset_status_labels WHERE dataset_id=?", (dataset_id,))
            result = db.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))
        return bool(result.rowcount)

    def update_display(self, dataset_id: str, name: str, description: str, tags: list[str]) -> dict | None:
        self.initialize()
        with self.connection() as db:
            self._ensure_name_available(db, name, dataset_id)
            result = db.execute("""UPDATE datasets SET name=?, description=?, tags_json=?, updated_at=?
                WHERE id=?""", (name, description, json.dumps(tags, ensure_ascii=False),
                                utc_now(), dataset_id))
        return self.get(dataset_id) if result.rowcount else None

    def accept_rescan(self, dataset_id: str, digest: str, robot_type: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            result = db.execute("""UPDATE datasets SET info_sha256=?, robot_type=?, availability='unavailable',
                updated_at=?
                WHERE id=?""", (digest, robot_type, utc_now(), dataset_id))
            if result.rowcount:
                db.execute("DELETE FROM dataset_status_labels WHERE dataset_id=?", (dataset_id,))
                db.execute("INSERT INTO dataset_status_labels (dataset_id,label,created_at) VALUES (?,?,?)",
                           (dataset_id, "待质检", utc_now()))
        return self.get(dataset_id) if result.rowcount else None

    @staticmethod
    def _row(row: Row, status_labels: list[str] | None = None) -> dict:
        item = dict(row)
        item["status_labels"] = status_labels or []
        for source, target in (
            ("tags_json", "tags"),
            ("source_dataset_ids_json", "source_dataset_ids"),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (ValueError, TypeError, KeyError):
                item[target] = []
        return item
