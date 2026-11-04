"""Unified MySQL registry for datasets stored by the platform.

The registry owns stable ``DS000001`` identifiers and maps them to one direct
child of ``storage/raw`` or ``storage/derived``. Dataset bytes stay on disk;
MySQL only stores management metadata and lineage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.db.mysql import IntegrityError, Row, connection, ensure_schema

STORAGE_AREAS = frozenset({"raw", "derived"})


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _area(value: str) -> str:
    value = value.strip().lower()
    if value not in STORAGE_AREAS:
        raise ValueError("存储区域只能是 raw 或 derived")
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

    def list(self, storage_area: str | None = None) -> list[dict]:
        self.initialize()
        with self.connection() as db:
            area = _area(storage_area) if storage_area else None
            rows = db.execute(
                "SELECT * FROM datasets ORDER BY seq DESC" if area is None else
                "SELECT * FROM datasets WHERE storage_area=? ORDER BY seq DESC",
                () if area is None else (area,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, dataset_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        return self._row(row) if row else None

    def by_location(self, storage_area: str, storage_relpath: str) -> dict | None:
        self.initialize()
        area = _area(storage_area)
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM datasets WHERE storage_area=? AND storage_relpath=?",
                (area, _child_name(storage_relpath)),
            ).fetchone()
        return self._row(row) if row else None

    def register(
        self,
        storage_area: str,
        storage_relpath: str,
        name: str,
        robot_type: str,
        digest: str,
        source_dataset_ids: list[str] | None = None,
    ) -> dict:
        self.initialize()
        area = _area(storage_area)
        relpath = _child_name(storage_relpath)
        sources = list(dict.fromkeys(source_dataset_ids or []))
        try:
            with self.connection() as db:
                if db.execute(
                    "SELECT 1 FROM datasets WHERE storage_area=? AND storage_relpath=?",
                    (area, relpath),
                ).fetchone():
                    raise ValueError("此目录已经登记")
                seq = db.execute("INSERT INTO dataset_numbers () VALUES ()").lastrowid
                dataset_id = f"DS{seq:06d}"
                timestamp = utc_now()
                db.execute("""INSERT INTO datasets
                    (id, seq, storage_area, storage_relpath, name, robot_type,
                     description, tags_json, source_dataset_ids_json, info_sha256,
                     registered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (dataset_id, seq, area, relpath, name, robot_type, "", "[]",
                     json.dumps(sources, ensure_ascii=False), digest, timestamp, timestamp))
        except IntegrityError as exc:
            raise ValueError("目录或编号已被其他登记占用") from exc
        return self.get(dataset_id)

    def unregister(self, dataset_id: str) -> bool:
        """Remove a newly-created row when its file transaction rolls back."""

        self.initialize()
        with self.connection() as db:
            result = db.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))
        return bool(result.rowcount)

    def update_display(self, dataset_id: str, name: str, description: str, tags: list[str]) -> dict | None:
        self.initialize()
        with self.connection() as db:
            result = db.execute("""UPDATE datasets SET name=?, description=?, tags_json=?, updated_at=?
                WHERE id=?""", (name, description, json.dumps(tags, ensure_ascii=False),
                                utc_now(), dataset_id))
        return self.get(dataset_id) if result.rowcount else None

    def accept_rescan(self, dataset_id: str, digest: str, robot_type: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            result = db.execute("""UPDATE datasets SET info_sha256=?, robot_type=?, updated_at=?
                WHERE id=?""", (digest, robot_type, utc_now(), dataset_id))
        return self.get(dataset_id) if result.rowcount else None

    @staticmethod
    def _row(row: Row) -> dict:
        item = dict(row)
        for source, target in (
            ("tags_json", "tags"),
            ("source_dataset_ids_json", "source_dataset_ids"),
        ):
            try:
                item[target] = json.loads(item.pop(source))
            except (ValueError, TypeError, KeyError):
                item[target] = []
        return item
