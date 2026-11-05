"""Authentication service functions shared by HTTP routes and middleware."""

from pathlib import Path

from app.db.users import SESSION_TTL_SECONDS, UserRepository

SESSION_COOKIE_NAME = "hkc_session"


def authenticate(root: Path, username: str, password: str) -> dict | None:
    return UserRepository(root).authenticate(username, password)


def start_session(root: Path, user_id: int) -> tuple[str, int]:
    return UserRepository(root).create_session(user_id, SESSION_TTL_SECONDS)


def current_user(root: Path, token: str | None) -> dict | None:
    return UserRepository(root).user_for_session(token)


def end_session(root: Path, token: str | None) -> None:
    UserRepository(root).revoke_session(token)


def update_display_name(root: Path, user_id: int, display_name: str) -> dict:
    return UserRepository(root).update_display_name(user_id, display_name)


def change_password(
    root: Path,
    user_id: int,
    current_password: str,
    new_password: str,
) -> None:
    UserRepository(root).change_password(user_id, current_password, new_password)
