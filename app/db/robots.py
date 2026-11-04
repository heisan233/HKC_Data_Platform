"""MySQL registry for independently managed robot records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.db.mysql import IntegrityError, Row, connection, ensure_schema

ROBOT_STATUSES = frozenset({"active", "maintenance", "inactive"})


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


def _status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in ROBOT_STATUSES:
        raise ValueError("机器人状态只能是 active、maintenance 或 inactive")
    return normalized


def _sensors(values: list[str] | None) -> list[str]:
    sensors: list[str] = []
    for value in values or []:
        normalized = value.strip()
        if not normalized or normalized in sensors:
            continue
        if len(normalized) > 80:
            raise ValueError("单个传感器名称不能超过 80 个字符")
        sensors.append(normalized)
    if len(sensors) > 50:
        raise ValueError("传感器不能超过 50 项")
    return sensors


class RobotRepository:
    def __init__(self, root: Path):
        self.root = root

    def connection(self):
        return connection()

    def initialize(self) -> None:
        ensure_schema()

    def list(self, status: str | None = None) -> list[dict]:
        self.initialize()
        with self.connection() as db:
            if status is None:
                rows = db.execute("SELECT * FROM robots ORDER BY seq DESC").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM robots WHERE status=? ORDER BY seq DESC",
                    (_status(status),),
                ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, robot_id: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute("SELECT * FROM robots WHERE id=?", (robot_id,)).fetchone()
        return self._row(row) if row else None

    def create(
        self,
        *,
        name: str,
        robot_type: str,
        model: str = "",
        serial_number: str = "",
        status: str = "active",
        network_address: str = "",
        sensors: list[str] | None = None,
        notes: str = "",
    ) -> dict:
        self.initialize()
        values = self._values(
            name=name,
            robot_type=robot_type,
            model=model,
            serial_number=serial_number,
            status=status,
            network_address=network_address,
            sensors=sensors,
            notes=notes,
        )
        try:
            with self.connection() as db:
                seq = db.execute("INSERT INTO robot_numbers () VALUES ()").lastrowid
                robot_id = f"RB{seq:06d}"
                timestamp = utc_now()
                db.execute(
                    """INSERT INTO robots
                       (id, seq, name, robot_type, model, serial_number, status,
                        network_address, sensors_json, notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (robot_id, seq, *values, timestamp, timestamp),
                )
        except IntegrityError as exc:
            raise ValueError(self._integrity_message(exc)) from exc
        return self.get(robot_id)

    def update(
        self,
        robot_id: str,
        *,
        name: str,
        robot_type: str,
        model: str = "",
        serial_number: str = "",
        status: str = "active",
        network_address: str = "",
        sensors: list[str] | None = None,
        notes: str = "",
    ) -> dict | None:
        self.initialize()
        values = self._values(
            name=name,
            robot_type=robot_type,
            model=model,
            serial_number=serial_number,
            status=status,
            network_address=network_address,
            sensors=sensors,
            notes=notes,
        )
        try:
            with self.connection() as db:
                result = db.execute(
                    """UPDATE robots
                       SET name=?, robot_type=?, model=?, serial_number=?, status=?,
                           network_address=?, sensors_json=?, notes=?, updated_at=?
                       WHERE id=?""",
                    (*values, utc_now(), robot_id),
                )
        except IntegrityError as exc:
            raise ValueError(self._integrity_message(exc)) from exc
        return self.get(robot_id) if result.rowcount else None

    def set_status(self, robot_id: str, status: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            result = db.execute(
                "UPDATE robots SET status=?, updated_at=? WHERE id=?",
                (_status(status), utc_now(), robot_id),
            )
        return self.get(robot_id) if result.rowcount else None

    @staticmethod
    def _values(
        *,
        name: str,
        robot_type: str,
        model: str,
        serial_number: str,
        status: str,
        network_address: str,
        sensors: list[str] | None,
        notes: str,
    ) -> tuple[str, str, str, str | None, str, str, str, str]:
        serial = _optional(serial_number, "序列号", 100)
        return (
            _required(name, "机器人名称", 100),
            _required(robot_type, "机器人类型", 100),
            _optional(model, "型号", 100),
            serial or None,
            _status(status),
            _optional(network_address, "网络地址", 255),
            json.dumps(_sensors(sensors), ensure_ascii=False),
            _optional(notes, "备注", 2000),
        )

    @staticmethod
    def _integrity_message(exc: IntegrityError) -> str:
        message = str(exc).casefold()
        if "serial_number" in message:
            return "序列号已经被其他机器人使用"
        if "name" in message:
            return "机器人名称已经存在"
        return "机器人编号、名称或序列号发生冲突"

    @staticmethod
    def _row(row: Row) -> dict:
        item = dict(row)
        try:
            item["sensors"] = json.loads(item.pop("sensors_json"))
        except (ValueError, TypeError, KeyError):
            item["sensors"] = []
        return item
