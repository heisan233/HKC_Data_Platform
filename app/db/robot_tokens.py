"""MySQL credentials used by registered robots to call collector APIs.

The first internal-network release intentionally keeps the full token so an
operator can display it again from the deployment terminal.  Browser-facing
records expose only a short prefix.  Authentication uses the SHA-256 column so
the raw token is never included in lookup queries or request logs.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from pathlib import Path

from app.db.mysql import IntegrityError, connection, ensure_schema
from app.db.robots import RobotRepository

TOKEN_MARKER = "hkc_rb_"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return TOKEN_MARKER + secrets.token_urlsafe(32)


def _shown_prefix(token: str) -> str:
    return token[:18] + "…"


class RobotTokenRepository:
    """Own the single upload credential attached to each robot record."""

    def __init__(self, root: Path):
        self.root = root

    def initialize(self) -> None:
        ensure_schema()

    @staticmethod
    def _public(row, *, reveal: bool = False) -> dict | None:
        if row is None:
            return None
        item = {
            "robot_id": row["robot_id"],
            "token_configured": True,
            "token_prefix": row["token_prefix"],
            "token_created_at": row["created_at"],
            "token_last_used_at": row["last_used_at"],
        }
        if reveal:
            item["token"] = row["token_value"]
        return item

    def get(self, robot_id: str, *, reveal: bool = False) -> dict | None:
        self.initialize()
        with connection() as db:
            row = db.execute(
                "SELECT * FROM robot_api_tokens WHERE robot_id=?", (robot_id,)
            ).fetchone()
        return self._public(row, reveal=reveal)

    def list_statuses(self) -> dict[str, dict]:
        self.initialize()
        with connection() as db:
            rows = db.execute(
                "SELECT * FROM robot_api_tokens ORDER BY robot_id"
            ).fetchall()
        return {row["robot_id"]: self._public(row) for row in rows}

    def create(self, robot_id: str) -> dict:
        """Create the only active token and return its full value to a CLI caller."""
        self.initialize()
        token = _new_token()
        timestamp = _now()
        try:
            with connection() as db:
                robot = db.execute(
                    "SELECT id FROM robots WHERE id=? FOR UPDATE", (robot_id,)
                ).fetchone()
                if robot is None:
                    raise ValueError("机器人不存在")
                if db.execute(
                    "SELECT 1 FROM robot_api_tokens WHERE robot_id=?", (robot_id,)
                ).fetchone():
                    raise ValueError("该机器人已经配置上传 Token")
                db.execute(
                    """INSERT INTO robot_api_tokens
                       (robot_id,token_value,token_sha256,token_prefix,created_at,last_used_at)
                       VALUES (?,?,?,?,?,NULL)""",
                    (robot_id, token, _digest(token), _shown_prefix(token), timestamp),
                )
        except IntegrityError as exc:
            raise ValueError("机器人 Token 发生冲突，请重新生成") from exc
        return self.get(robot_id, reveal=True)

    def regenerate(self, robot_id: str) -> dict:
        """Replace a robot's token; the old value becomes invalid immediately."""
        self.initialize()
        token = _new_token()
        timestamp = _now()
        with connection() as db:
            robot = db.execute(
                "SELECT id FROM robots WHERE id=? FOR UPDATE", (robot_id,)
            ).fetchone()
            if robot is None:
                raise ValueError("机器人不存在")
            db.execute("DELETE FROM robot_api_tokens WHERE robot_id=?", (robot_id,))
            db.execute(
                """INSERT INTO robot_api_tokens
                   (robot_id,token_value,token_sha256,token_prefix,created_at,last_used_at)
                   VALUES (?,?,?,?,?,NULL)""",
                (robot_id, token, _digest(token), _shown_prefix(token), timestamp),
            )
        return self.get(robot_id, reveal=True)

    def delete(self, robot_id: str) -> bool:
        self.initialize()
        if RobotRepository(self.root).get(robot_id) is None:
            raise ValueError("机器人不存在")
        with connection() as db:
            result = db.execute(
                "DELETE FROM robot_api_tokens WHERE robot_id=?", (robot_id,)
            )
        return bool(result.rowcount)

    def authenticate(self, token: str) -> dict | None:
        """Return the owning robot and update last-used time for a valid token."""
        normalized = token.strip()
        if not normalized.startswith(TOKEN_MARKER) or len(normalized) > 160:
            return None
        self.initialize()
        timestamp = _now()
        with connection() as db:
            row = db.execute(
                "SELECT robot_id FROM robot_api_tokens WHERE token_sha256=?",
                (_digest(normalized),),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE robot_api_tokens SET last_used_at=? WHERE robot_id=?",
                (timestamp, row["robot_id"]),
            )
        robot = RobotRepository(self.root).get(row["robot_id"])
        if robot is None:
            return None
        return {**robot, "token_last_used_at": timestamp}
