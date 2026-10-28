"""Shared test isolation for authentication and SQLite-backed pages.

Existing module tests focus on dataset behavior. They receive an in-memory-style
authenticated request identity without touching the real ``storage`` database.
Tests marked ``real_auth`` exercise the actual SQLite login flow instead.
The robot and semantics routers always receive a per-test data root so general
page rendering cannot initialize tables in the developer's real platform database.
"""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.modules.auth import middleware as auth_middleware
from app.modules.robots import router as robot_router
from app.modules.semantics import router as semantics_router
from app.modules.system import router as system_router


@pytest.fixture(autouse=True)
def isolated_shared_services(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(robot_router, "settings", Settings(data_root=tmp_path))
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
