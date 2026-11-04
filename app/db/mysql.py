"""MySQL connection and one-time schema initialization for platform metadata."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import mysql.connector
from mysql.connector.constants import ClientFlag

IntegrityError = mysql.connector.IntegrityError

_schema_lock = Lock()
_ready_databases: set[tuple[str, int, str]] = set()
_schema_file = Path(__file__).resolve().parents[2] / "migrations/mysql/001_initial.sql"


def _config() -> dict:
    password_file = os.getenv("HKC_MYSQL_PASSWORD_FILE", "").strip()
    if password_file:
        password = Path(password_file).read_text(encoding="utf-8").rstrip("\r\n")
    else:
        password = os.getenv("HKC_MYSQL_PASSWORD", "")
    if not password:
        raise RuntimeError("尚未配置 HKC_MYSQL_PASSWORD 或 HKC_MYSQL_PASSWORD_FILE")
    return {
        "host": os.getenv("HKC_MYSQL_HOST", "mysql"),
        "port": int(os.getenv("HKC_MYSQL_PORT", "3306")),
        "database": os.getenv("HKC_MYSQL_DATABASE", "hkc_platform"),
        "user": os.getenv("HKC_MYSQL_USER", "hkc_app"),
        "password": password,
        "charset": "utf8mb4",
        "connection_timeout": 10,
        "autocommit": False,
        "client_flags": [ClientFlag.FOUND_ROWS],
    }


class Row(dict):
    """Keep the existing repository's named and positional row access."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class Result:
    def __init__(self, rows: list[dict], rowcount: int, lastrowid: int | None):
        self._rows = [Row(row) for row in rows]
        self._offset = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self) -> Row | None:
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self) -> list[Row]:
        rows = self._rows[self._offset:]
        self._offset = len(self._rows)
        return rows


class Connection:
    def __init__(self, native):
        self.native = native

    def execute(self, sql: str, params=()) -> Result:
        # The repository's SQL contains only parameter-marker question marks.
        cursor = self.native.cursor(dictionary=True, buffered=True)
        try:
            cursor.execute(sql.replace("?", "%s"), tuple(params))
            rows = cursor.fetchall() if cursor.with_rows else []
            return Result(rows, cursor.rowcount, cursor.lastrowid)
        finally:
            cursor.close()


@contextmanager
def connection():
    native = mysql.connector.connect(**_config())
    try:
        yield Connection(native)
        native.commit()
    except Exception:
        native.rollback()
        raise
    finally:
        native.close()


def ensure_schema() -> None:
    """Create the fresh platform schema once per process and database.

    The MySQL server owns the database itself. This creates only platform tables,
    and never reads or imports an existing SQLite database.
    """
    config = _config()
    key = (config["host"], config["port"], config["database"])
    if key in _ready_databases:
        return
    with _schema_lock:
        if key in _ready_databases:
            return
        native = mysql.connector.connect(**config)
        cursor = native.cursor()
        lock_name = f"hkc_platform_schema:{config['database']}"
        try:
            cursor.execute("SELECT GET_LOCK(%s, 30)", (lock_name,))
            if cursor.fetchone()[0] != 1:
                raise RuntimeError("无法取得 MySQL 建表锁")
            cursor.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                           "version INTEGER PRIMARY KEY, applied_at VARCHAR(40) NOT NULL) "
                           "ENGINE=InnoDB")
            cursor.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
            version = cursor.fetchone()[0]
            if version > 1:
                raise RuntimeError(f"MySQL schema version {version} is newer than this application")
            if version == 0:
                for statement in _schema_file.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        cursor.execute(statement)
                cursor.execute("INSERT INTO schema_migrations(version, applied_at) "
                               "VALUES (1, DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%s+00:00'))")
                native.commit()
            _ready_databases.add(key)
        finally:
            try:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                cursor.fetchall()
            finally:
                cursor.close()
                native.close()
