"""MySQL users and server-side login sessions.

Passwords are stored as ``scrypt`` hashes with an independent random salt.
Session cookies contain a random token; MySQL stores only its SHA-256 digest,
so neither plaintext passwords nor usable session tokens are written to disk.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.mysql import IntegrityError, connection, ensure_schema

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
SESSION_TTL_SECONDS = 12 * 60 * 60

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def normalize_username(value: str) -> str:
    username = value.strip()
    if not 3 <= len(username) <= 64:
        raise ValueError("用户名长度必须为 3 到 64 个字符")
    if not all(character.isalnum() or character in "_.-" for character in username):
        raise ValueError("用户名只能包含文字、数字、下划线、点和连字符")
    return username


def _password_bytes(password: str) -> bytes:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"密码长度必须为 {PASSWORD_MIN_LENGTH} 到 {PASSWORD_MAX_LENGTH} 个字符"
        )
    return password.encode("utf-8")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        _password_bytes(password),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$", maxsplit=5)
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            _password_bytes(password),
            salt=_decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_decode(expected)),
            maxmem=_SCRYPT_MAXMEM,
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(digest, _decode(expected))


def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class UserRepository:
    def __init__(self, root: Path):
        self.root = root

    def connection(self):
        return connection()

    def initialize(self) -> None:
        ensure_schema()

    def create_user(self, username: str, password: str) -> dict:
        self.initialize()
        username = normalize_username(username)
        password_hash = hash_password(password)
        timestamp = _timestamp(utc_now())
        try:
            with self.connection() as db:
                cursor = db.execute(
                    """INSERT INTO users
                       (username, password_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (username, password_hash, timestamp, timestamp),
                )
                user_id = cursor.lastrowid
        except IntegrityError as exc:
            raise ValueError("用户名已经存在") from exc
        return self.get_by_id(user_id)

    def get_by_id(self, user_id: int) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute(
                """SELECT id, username, is_active, created_at, updated_at, last_login_at
                   FROM users WHERE id=?""",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def authenticate(self, username: str, password: str) -> dict | None:
        self.initialize()
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username=?",
                (username.strip(),),
            ).fetchone()
        if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
            return None
        return {
            key: row[key]
            for key in ("id", "username", "is_active", "created_at", "updated_at", "last_login_at")
        }

    def create_session(
        self,
        user_id: int,
        ttl_seconds: int = SESSION_TTL_SECONDS,
    ) -> tuple[str, int]:
        self.initialize()
        token = secrets.token_urlsafe(32)
        now = utc_now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self.connection() as db:
            db.execute("DELETE FROM user_sessions WHERE expires_at<=?", (_timestamp(now),))
            db.execute(
                """INSERT INTO user_sessions
                   (token_hash, user_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (session_digest(token), user_id, _timestamp(now), _timestamp(expires_at)),
            )
            db.execute(
                "UPDATE users SET last_login_at=?, updated_at=? WHERE id=?",
                (_timestamp(now), _timestamp(now), user_id),
            )
        return token, ttl_seconds

    def user_for_session(self, token: str | None) -> dict | None:
        if not token:
            return None
        self.initialize()
        now = _timestamp(utc_now())
        token_hash = session_digest(token)
        with self.connection() as db:
            row = db.execute(
                """SELECT u.id, u.username, u.is_active, u.created_at,
                          u.updated_at, u.last_login_at, s.expires_at
                   FROM user_sessions AS s
                   JOIN users AS u ON u.id=s.user_id
                   WHERE s.token_hash=?""",
                (token_hash,),
            ).fetchone()
            if row and (not row["is_active"] or row["expires_at"] <= now):
                db.execute("DELETE FROM user_sessions WHERE token_hash=?", (token_hash,))
                return None
        if not row:
            return None
        return {
            key: row[key]
            for key in ("id", "username", "is_active", "created_at", "updated_at", "last_login_at")
        }

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        self.initialize()
        with self.connection() as db:
            db.execute("DELETE FROM user_sessions WHERE token_hash=?", (session_digest(token),))

    def user_count(self) -> int:
        self.initialize()
        with self.connection() as db:
            return int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def list_users(self) -> list[dict]:
        """Return account metadata without password hashes or session tokens."""

        self.initialize()
        with self.connection() as db:
            rows = db.execute(
                """SELECT id, username, is_active, created_at, updated_at, last_login_at
                   FROM users
                   ORDER BY id"""
            ).fetchall()
        return [dict(row) for row in rows]
