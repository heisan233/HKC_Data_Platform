"""Unified SQLite registry for datasets stored by the platform.

The registry owns stable ``DS000001`` identifiers and maps them to one direct
child of ``storage/raw`` or ``storage/derived``. Dataset bytes stay on disk;
SQLite only stores management metadata and lineage.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

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
        self.path = root / "system" / "platform.sqlite"

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _create_datasets_table(db: sqlite3.Connection) -> None:
        db.execute("""CREATE TABLE IF NOT EXISTS datasets (
            id TEXT PRIMARY KEY,
            seq INTEGER NOT NULL UNIQUE,
            storage_area TEXT NOT NULL CHECK(storage_area IN ('raw', 'derived')),
            storage_relpath TEXT NOT NULL,
            name TEXT NOT NULL,
            robot_type TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            source_dataset_ids_json TEXT NOT NULL DEFAULT '[]',
            info_sha256 TEXT NOT NULL,
            registered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(storage_area, storage_relpath)
        )""")

    def initialize(self) -> None:
        """Create the schema without scanning or importing dataset folders."""

        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS dataset_numbers (
                seq INTEGER PRIMARY KEY AUTOINCREMENT
            )""")
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='datasets'"
            ).fetchone()
            if not exists:
                self._create_datasets_table(db)
            else:
                columns = {row[1] for row in db.execute("PRAGMA table_info(datasets)")}
                if "storage_area" not in columns and "raw_relpath" in columns:
                    # This only updates SQLite metadata compatibility. Dataset
                    # folders are never scanned, moved, renamed, or imported.
                    db.execute("ALTER TABLE datasets ADD COLUMN storage_area TEXT NOT NULL DEFAULT 'raw'")
                    db.execute("ALTER TABLE datasets ADD COLUMN storage_relpath TEXT")
                    db.execute("ALTER TABLE datasets ADD COLUMN source_dataset_ids_json TEXT NOT NULL DEFAULT '[]'")
                    db.execute("UPDATE datasets SET storage_relpath=raw_relpath WHERE storage_relpath IS NULL")
            db.execute("CREATE INDEX IF NOT EXISTS datasets_registered_idx ON datasets(registered_at)")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS datasets_location_idx
                ON datasets(storage_area, storage_relpath)""")
            highest = db.execute("SELECT COALESCE(MAX(seq), 0) FROM datasets").fetchone()[0]
            current = db.execute("SELECT COALESCE(MAX(seq), 0) FROM dataset_numbers").fetchone()[0]
            if highest > current:
                db.execute("INSERT INTO dataset_numbers(seq) VALUES (?)", (highest,))

    def list(self, storage_area: str | None = None) -> list[dict]:
        self.initialize()
        with self.connection() as db:
            if storage_area is None:
                rows = db.execute("SELECT * FROM datasets ORDER BY seq DESC").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM datasets WHERE storage_area=? ORDER BY seq DESC",
                    (_area(storage_area),),
                ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, dataset_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        return self._row(row) if row else None

    def by_location(self, storage_area: str, storage_relpath: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM datasets WHERE storage_area=? AND storage_relpath=?",
                (_area(storage_area), _child_name(storage_relpath)),
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
                db.execute("BEGIN IMMEDIATE")
                if db.execute(
                    "SELECT 1 FROM datasets WHERE storage_area=? AND storage_relpath=?",
                    (area, relpath),
                ).fetchone():
                    raise ValueError("此目录已经登记")
                seq = db.execute("INSERT INTO dataset_numbers DEFAULT VALUES").lastrowid
                dataset_id = f"DS{seq:06d}"
                timestamp = utc_now()
                columns = {row[1] for row in db.execute("PRAGMA table_info(datasets)")}
                if "raw_relpath" in columns:
                    db.execute("""INSERT INTO datasets
                        (id, seq, raw_relpath, storage_area, storage_relpath, name,
                         robot_type, source_dataset_ids_json, info_sha256,
                         registered_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (dataset_id, seq, relpath, area, relpath, name, robot_type,
                         json.dumps(sources, ensure_ascii=False), digest, timestamp, timestamp))
                else:
                    db.execute("""INSERT INTO datasets
                        (id, seq, storage_area, storage_relpath, name, robot_type,
                         source_dataset_ids_json, info_sha256, registered_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (dataset_id, seq, area, relpath, name, robot_type,
                         json.dumps(sources, ensure_ascii=False), digest, timestamp, timestamp))
        except sqlite3.IntegrityError as exc:
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
    def _row(row: sqlite3.Row) -> dict:
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
