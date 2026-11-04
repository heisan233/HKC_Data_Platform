"""Shared test isolation for authentication and MySQL-backed pages.

Existing module tests focus on dataset behavior. They receive an in-memory-style
authenticated request identity without touching the real ``storage`` database.
Tests marked ``real_auth`` exercise the actual MySQL login flow instead.
The robot and semantics routers always receive a per-test data root so general
page rendering cannot initialize tables in the developer's real platform database.
"""

import os
from pathlib import Path
from uuid import uuid4

import mysql.connector
import pytest

from app.core.config import Settings
from app.modules.auth import middleware as auth_middleware
from app.modules.dashboard import router as dashboard_router
from app.modules.robots import router as robot_router
from app.modules.semantics import router as semantics_router
from app.modules.system import router as system_router


@pytest.fixture(autouse=True)
def isolated_mysql_database(monkeypatch: pytest.MonkeyPatch):
    """Give every test a fresh schema; never connect to a deployed database."""
    host = os.getenv("HKC_TEST_MYSQL_HOST")
    password = os.getenv("HKC_TEST_MYSQL_ROOT_PASSWORD")
    if not host or not password:
        pytest.fail("测试需要 HKC_TEST_MYSQL_HOST 和 HKC_TEST_MYSQL_ROOT_PASSWORD")
    port = int(os.getenv("HKC_TEST_MYSQL_PORT", "3306"))
    database = f"hkc_test_{uuid4().hex[:16]}"
    admin = mysql.connector.connect(host=host, port=port, user="root", password=password)
    admin.autocommit = True
    cursor = admin.cursor()
    try:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        monkeypatch.setenv("HKC_MYSQL_HOST", host)
        monkeypatch.setenv("HKC_MYSQL_PORT", str(port))
        monkeypatch.setenv("HKC_MYSQL_DATABASE", database)
        monkeypatch.setenv("HKC_MYSQL_USER", "root")
        monkeypatch.setenv("HKC_MYSQL_PASSWORD", password)
        monkeypatch.delenv("HKC_MYSQL_PASSWORD_FILE", raising=False)
        yield
    finally:
        cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        cursor.close()
        admin.close()


@pytest.fixture(autouse=True)
def isolated_shared_services(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(robot_router, "settings", Settings(data_root=tmp_path))
    monkeypatch.setattr(dashboard_router, "settings", Settings(data_root=tmp_path))
    monkeypatch.setattr(semantics_router, "settings", Settings(data_root=tmp_path))
    monkeypatch.setattr(system_router, "settings", Settings(data_root=tmp_path))
    monkeypatch.setattr(auth_middleware, "settings", Settings(data_root=tmp_path))
    if request.node.get_closest_marker("real_auth"):
        return
    monkeypatch.setattr(
        auth_middleware,
        "_current_user",
        lambda _request: {
            "id": 1,
            "username": "test_user",
            "is_active": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "last_login_at": None,
        },
    )
