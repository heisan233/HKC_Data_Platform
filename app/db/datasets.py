"""SQLite registration for datasets that another process has placed in raw/.

The database contains only management metadata. Dataset bytes always remain on disk.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3


CODE = re.compile(r"DS([0-9]{6})\Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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

    def initialize(self) -> None:
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS dataset_numbers (
                seq INTEGER PRIMARY KEY AUTOINCREMENT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY,
                seq INTEGER NOT NULL UNIQUE,
                raw_relpath TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                robot_type TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                info_sha256 TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS datasets_registered_idx ON datasets(registered_at)")
            # Folder names like DS000026 already exist in old data. Never issue a
            # lower number that could make an ID look like the wrong directory.
            highest = db.execute("SELECT COALESCE(MAX(seq), 0) FROM datasets").fetchone()[0]
            raw = self.root / "raw"
            if raw.is_dir():
                for entry in raw.iterdir():
                    match = CODE.fullmatch(entry.name)
                    if match:
                        highest = max(highest, int(match.group(1)))
            current = db.execute("SELECT COALESCE(MAX(seq), 0) FROM dataset_numbers").fetchone()[0]
            if highest > current:
                db.execute("INSERT INTO dataset_numbers(seq) VALUES (?)", (highest,))

    def list(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM datasets ORDER BY seq DESC").fetchall()
        return [self._row(row) for row in rows]

    def get(self, dataset_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        return self._row(row) if row else None

    def by_path(self, relative_path: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM datasets WHERE raw_relpath=?", (relative_path,)).fetchone()
        return self._row(row) if row else None

    def register(self, relative_path: str, name: str, robot_type: str, digest: str) -> dict:
        self.initialize()
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM datasets WHERE raw_relpath=?", (relative_path,)).fetchone():
                    raise ValueError("此目录已经登记")
                match = CODE.fullmatch(relative_path)
                requested = int(match.group(1)) if match else None
                occupied = (db.execute("SELECT 1 FROM datasets WHERE seq=?", (requested,)).fetchone()
                            if requested else None)
                if requested and not occupied:
                    db.execute("INSERT OR IGNORE INTO dataset_numbers(seq) VALUES (?)", (requested,))
                    seq = requested
                else:
                    seq = db.execute("INSERT INTO dataset_numbers DEFAULT VALUES").lastrowid
                dataset_id = f"DS{seq:06d}"
                timestamp = utc_now()
                db.execute("""INSERT INTO datasets
                    (id, seq, raw_relpath, name, robot_type, info_sha256, registered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (dataset_id, seq, relative_path, name, robot_type, digest, timestamp, timestamp))
        except sqlite3.IntegrityError as exc:
            raise ValueError("目录或编号已被其他登记占用") from exc
        return self.get(dataset_id)

    def update_display(self, dataset_id: str, name: str, description: str, tags: list[str]) -> dict | None:
        with self.connection() as db:
            result = db.execute("""UPDATE datasets SET name=?, description=?, tags_json=?, updated_at=?
                WHERE id=?""", (name, description, json.dumps(tags, ensure_ascii=False),
                                utc_now(), dataset_id))
        return self.get(dataset_id) if result.rowcount else None

    def accept_rescan(self, dataset_id: str, digest: str, robot_type: str) -> dict | None:
        with self.connection() as db:
            result = db.execute("""UPDATE datasets SET info_sha256=?, robot_type=?, updated_at=?
                WHERE id=?""", (digest, robot_type, utc_now(), dataset_id))
        return self.get(dataset_id) if result.rowcount else None

    def _row(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        try:
            item["tags"] = json.loads(item.pop("tags_json"))
        except (ValueError, TypeError):
            item["tags"] = []
        return item
