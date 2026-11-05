"""Real MySQL login, cookie session, logout, and access-protection tests."""

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
    _, repository, _ = auth_platform

    user = repository.create_user("hei_san", "test-password")
    with repository.connection() as database:
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
    client, repository, _ = auth_platform
    repository.create_user("hei_san", "test-password", must_change_password=False)

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
    with repository.connection() as database:
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
    with repository.connection() as database:
        assert database.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0

    protected = client.get("/dashboard/", follow_redirects=False)
    assert protected.status_code == 303


def test_login_rejects_external_redirect(auth_platform):
    client, repository, _ = auth_platform
    repository.create_user("hei_san", "test-password", must_change_password=False)

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


def test_initial_password_requires_change_before_platform_access(auth_platform):
    client, repository, _ = auth_platform
    repository.create_user("new_user", "initial-password")

    response = client.post(
        "/login",
        data={"username": "new_user", "password": "initial-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/account/password"
    assert client.get("/account/password").status_code == 200

    page = client.get("/dashboard/", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/account/password"
    api = client.get("/annotation/api/datasets", headers={"Accept": "application/json"})
    assert api.status_code == 403
    assert api.json()["password_change_required"] is True

    changed = client.post(
        "/account/password",
        data={
            "current_password": "initial-password",
            "new_password": "changed-password",
            "confirmation": "changed-password",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert changed.headers["location"] == "/login?password_changed=1"
    assert repository.authenticate("new_user", "initial-password") is None
    user = repository.authenticate("new_user", "changed-password")
    assert user and not user["must_change_password"]
    assert client.get("/dashboard/", follow_redirects=False).status_code == 303

    login = client.post(
        "/login",
        data={"username": "new_user", "password": "changed-password"},
        follow_redirects=False,
    )
    assert login.headers["location"] == "/dashboard/"


def test_profile_update_and_password_change_revoke_all_user_sessions(auth_platform):
    client_a, repository, _ = auth_platform
    repository.create_user(
        "operator_1",
        "current-password",
        display_name="操作员一",
        must_change_password=False,
    )
    client_b = TestClient(app)
    for client in (client_a, client_b):
        response = client.post(
            "/login",
            data={"username": "operator_1", "password": "current-password"},
            follow_redirects=False,
        )
        assert response.status_code == 303

    updated = client_a.post(
        "/account/profile",
        data={"display_name": "一号操作员"},
        follow_redirects=False,
    )
    assert updated.headers["location"] == "/account/?profile_updated=1"
    assert repository.get_by_username("operator_1")["display_name"] == "一号操作员"
    assert client_a.post("/account/profile", data={"display_name": "   "}).status_code == 400

    unchanged = client_a.post(
        "/account/password",
        data={
            "current_password": "current-password",
            "new_password": "current-password",
            "confirmation": "current-password",
        },
    )
    assert unchanged.status_code == 400
    assert "不能与当前密码相同" in unchanged.text

    changed = client_a.post(
        "/account/password",
        data={
            "current_password": "current-password",
            "new_password": "new-password-123",
            "confirmation": "new-password-123",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert client_a.get("/dashboard/", follow_redirects=False).status_code == 303
    assert client_b.get("/dashboard/", follow_redirects=False).status_code == 303
    assert repository.authenticate("operator_1", "current-password") is None
    assert repository.authenticate("operator_1", "new-password-123")


def test_two_users_have_independent_concurrent_sessions(auth_platform):
    client_a, repository, _ = auth_platform
    client_b = TestClient(app)
    repository.create_user("user_a", "password-a", must_change_password=False)
    repository.create_user("user_b", "password-b", must_change_password=False)

    client_a.post("/login", data={"username": "user_a", "password": "password-a"})
    client_b.post("/login", data={"username": "user_b", "password": "password-b"})
    token_a = client_a.cookies.get(SESSION_COOKIE_NAME)
    token_b = client_b.cookies.get(SESSION_COOKIE_NAME)
    assert token_a and token_b and token_a != token_b
    assert "user_a" in client_a.get("/account/").text
    assert "user_b" in client_b.get("/account/").text

    client_a.post("/logout")
    assert client_a.get("/dashboard/", follow_redirects=False).status_code == 303
    assert client_b.get("/dashboard/").status_code == 200


def test_disabling_user_revokes_sessions_and_enable_restores_login(auth_platform):
    client, repository, _ = auth_platform
    repository.create_user("operator_2", "test-password", must_change_password=False)
    client.post("/login", data={"username": "operator_2", "password": "test-password"})
    assert client.get("/dashboard/").status_code == 200

    repository.set_active("operator_2", False)
    assert client.get("/dashboard/", follow_redirects=False).status_code == 303
    assert repository.authenticate("operator_2", "test-password") is None

    repository.set_active("operator_2", True)
    assert repository.authenticate("operator_2", "test-password")
