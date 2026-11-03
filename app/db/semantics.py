"""SQLite registry for platform-controlled semantic vocabulary."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

DEFINITION_CATEGORIES = frozenset({"scene", "task", "action", "object"})
DEFINITION_STATUSES = frozenset({"active", "inactive"})
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _required(value: str, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label}不能为空")
    if len(normalized) > maximum:
        raise ValueError(f"{label}不能超过 {maximum} 个字符")
    return normalized


def _optional(value: str | None, label: str, maximum: int) -> str:
    normalized = (value or "").strip()
    if len(normalized) > maximum:
        raise ValueError(f"{label}不能超过 {maximum} 个字符")
    return normalized


def _category(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in DEFINITION_CATEGORIES:
        raise ValueError("定义类别只能是 scene、task、action 或 object")
    return normalized


def _code(value: str) -> str:
    normalized = value.strip().lower()
    if not CODE_PATTERN.fullmatch(normalized):
        raise ValueError("机器编码必须以小写字母开头，只能包含小写字母、数字和下划线")
    return normalized


def _status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in DEFINITION_STATUSES:
        raise ValueError("定义状态只能是 active 或 inactive")
    return normalized


def _examples(values: list[str] | None) -> list[str]:
    examples: list[str] = []
    for value in values or []:
        normalized = value.strip()
        if not normalized or normalized in examples:
            continue
        if len(normalized) > 200:
            raise ValueError("单条示例不能超过 200 个字符")
        examples.append(normalized)
    if len(examples) > 20:
        raise ValueError("示例不能超过 20 条")
    return examples


def _sort_order(value: int) -> int:
    if value < 0 or value > 9999:
        raise ValueError("排序值必须在 0 到 9999 之间")
    return value


class SemanticDefinitionRepository:
    def __init__(self, root: Path):
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
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS semantic_definition_numbers (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT
                );

                CREATE TABLE IF NOT EXISTS semantic_definitions (
                    id TEXT PRIMARY KEY,
                    seq INTEGER NOT NULL UNIQUE,
                    category TEXT NOT NULL
                        CHECK(category IN ('scene', 'task', 'action', 'object')),
                    code TEXT NOT NULL COLLATE NOCASE,
                    name TEXT NOT NULL COLLATE NOCASE,
                    description TEXT NOT NULL DEFAULT '',
                    examples_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active', 'inactive')),
                    sort_order INTEGER NOT NULL DEFAULT 100,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(category, code),
                    UNIQUE(category, name)
                );

                CREATE INDEX IF NOT EXISTS semantic_definitions_category_idx
                    ON semantic_definitions(category, status, sort_order, seq);
                """
            )
            highest = db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM semantic_definitions"
            ).fetchone()[0]
            current = db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM semantic_definition_numbers"
            ).fetchone()[0]
            if highest > current:
                db.execute("INSERT INTO semantic_definition_numbers(seq) VALUES (?)", (highest,))

    def list(self, category: str | None = None, status: str | None = None) -> list[dict]:
        self.initialize()
        conditions: list[str] = []
        values: list[str] = []
        if category is not None:
            conditions.append("category=?")
            values.append(_category(category))
        if status is not None:
            conditions.append("status=?")
            values.append(_status(status))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.connection() as db:
            rows = db.execute(
                f"SELECT * FROM semantic_definitions{where} ORDER BY category, sort_order, seq",
                values,
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, definition_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM semantic_definitions WHERE id=?", (definition_id,)
            ).fetchone()
        return self._row(row) if row else None

    def create(
        self,
        *,
        category: str,
        code: str,
        name: str,
        description: str = "",
        examples: list[str] | None = None,
        status: str = "active",
        sort_order: int = 100,
    ) -> dict:
        values = self._values(
            name=name,
            description=description,
            examples=examples,
            status=status,
            sort_order=sort_order,
        )
        category_value = _category(category)
        code_value = _code(code)
        self.initialize()
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                seq = db.execute(
                    "INSERT INTO semantic_definition_numbers DEFAULT VALUES"
                ).lastrowid
                definition_id = f"SD{seq:06d}"
                timestamp = utc_now()
                db.execute(
                    """INSERT INTO semantic_definitions
                       (id, seq, category, code, name, description, examples_json,
                        status, sort_order, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        definition_id,
                        seq,
                        category_value,
                        code_value,
                        *values,
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(self._integrity_message(exc)) from exc
        return self.get(definition_id)

    def update(
        self,
        definition_id: str,
        *,
        name: str,
        description: str = "",
        examples: list[str] | None = None,
        status: str = "active",
        sort_order: int = 100,
    ) -> dict | None:
        values = self._values(
            name=name,
            description=description,
            examples=examples,
            status=status,
            sort_order=sort_order,
        )
        self.initialize()
        try:
            with self.connection() as db:
                result = db.execute(
                    """UPDATE semantic_definitions
                       SET name=?, description=?, examples_json=?, status=?, sort_order=?,
                           updated_at=?
                       WHERE id=?""",
                    (*values, utc_now(), definition_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(self._integrity_message(exc)) from exc
        return self.get(definition_id) if result.rowcount else None

    def set_status(self, definition_id: str, status: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            result = db.execute(
                "UPDATE semantic_definitions SET status=?, updated_at=? WHERE id=?",
                (_status(status), utc_now(), definition_id),
            )
        return self.get(definition_id) if result.rowcount else None

    @staticmethod
    def _values(
        *,
        name: str,
        description: str,
        examples: list[str] | None,
        status: str,
        sort_order: int,
    ) -> tuple[str, str, str, str, int]:
        return (
            _required(name, "显示名称", 100),
            _optional(description, "说明", 1000),
            json.dumps(_examples(examples), ensure_ascii=False),
            _status(status),
            _sort_order(sort_order),
        )

    @staticmethod
    def _integrity_message(exc: sqlite3.IntegrityError) -> str:
        message = str(exc).casefold()
        if "semantic_definitions.category, semantic_definitions.code" in message:
            return "该类别下的机器编码已经存在"
        if "semantic_definitions.category, semantic_definitions.name" in message:
            return "该类别下的显示名称已经存在"
        return "定义编号、机器编码或显示名称发生冲突"

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        item = dict(row)
        try:
            item["examples"] = json.loads(item.pop("examples_json"))
        except (ValueError, TypeError, KeyError):
            item["examples"] = []
        return item
