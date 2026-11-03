"""Real SQLite login, cookie session, logout, and access-protection tests."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.users import UserRepository, session_digest, verify_password
from app.main import app
from app.modules.auth import middleware as auth_middleware
from app.modules.auth import router as auth_router
from app.modules.auth.service import SESSION_COOKIE_NAME

pytestmark = pytest.mark.real_auth


@pytest.fixture
def auth_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    isolated_settings = Settings(data_root=tmp_path)
    monkeypatch.setattr(auth_middleware, "settings", isolated_settings)
    monkeypatch.setattr(auth_router, "settings", isolated_settings)
    return TestClient(app), UserRepository(tmp_path), tmp_path


def test_no_default_user_and_public_healthcheck(auth_platform):
    client, repository, _ = auth_platform

    assert repository.user_count() == 0
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/login").status_code == 200


def test_protected_page_and_api_require_login(auth_platform):
    client, _, _ = auth_platform

    page = client.get("/dashboard/", follow_redirects=False)
    api = client.get(
        "/annotation/api/datasets",
        headers={"Accept": "application/json"},
    )

    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=%2Fdashboard%2F"
    assert api.status_code == 401
    assert api.json() == {"detail": "请先登录"}


def test_password_is_hashed_and_duplicate_username_is_rejected(auth_platform):
    _, repository, root = auth_platform

    user = repository.create_user("hei_san", "test-password")
    with sqlite3.connect(root / "system" / "platform.sqlite") as database:
        stored = database.execute(
            "SELECT password_hash FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()[0]

    assert stored != "test-password"
    assert stored.startswith("scrypt$")
    assert verify_password("test-password", stored)
    assert not verify_password("wrong-password", stored)
    with pytest.raises(ValueError, match="用户名已经存在"):
        repository.create_user("HEI_SAN", "another-password")


def test_login_session_and_logout(auth_platform):
    client, repository, root = auth_platform
    repository.create_user("hei_san", "test-password")

    invalid = client.post(
        "/login",
        data={"username": "hei_san", "password": "wrong-password"},
    )
    assert invalid.status_code == 401
    assert "用户名或密码错误" in invalid.text

    response = client.post(
        "/login",
        data={
            "username": "hei_san",
            "password": "test-password",
            "next": "/data/",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/data/"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]

    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token
    with sqlite3.connect(root / "system" / "platform.sqlite") as database:
        stored_token = database.execute(
            "SELECT token_hash FROM user_sessions"
        ).fetchone()[0]
    assert stored_token == session_digest(token)
    assert stored_token != token

    dashboard = client.get("/dashboard/")
    assert dashboard.status_code == 200
    assert "hei_san" in dashboard.text
    navigation = [
        "工作台",
        "机器人管理",
        "数据定义",
        "数据管理",
        "原始数据集上传",
        "数据质检",
        "数据标注",
        "数据处理",
        "格式转换与导出",
        "模型训练",
        "系统管理",
    ]
    positions = [dashboard.text.index(label) for label in navigation]
    assert positions == sorted(positions)

    logout = client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"
    with sqlite3.connect(root / "system" / "platform.sqlite") as database:
        assert database.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0

    protected = client.get("/dashboard/", follow_redirects=False)
    assert protected.status_code == 303


def test_login_rejects_external_redirect(auth_platform):
    client, repository, _ = auth_platform
    repository.create_user("hei_san", "test-password")

    response = client.post(
        "/login",
        data={
            "username": "hei_san",
            "password": "test-password",
            "next": "https://example.invalid/",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/"
