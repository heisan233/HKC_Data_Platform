"""Data definition reference page and controlled vocabulary tests."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.modules.semantics import router as semantics_router


@pytest.fixture
def semantic_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(semantics_router, "settings", Settings(data_root=tmp_path))
    return TestClient(app)


def definition_payload(**overrides) -> dict:
    payload = {
        "category": "action",
        "code": "pick_and_place",
        "name": "拾取并放置",
        "description": "抓取目标并移动到指定位置。",
        "examples": ["抓取红色方块并放入料盒"],
        "status": "active",
        "sort_order": 20,
    }
    payload.update(overrides)
    return payload


def update_payload(**overrides) -> dict:
    payload = {
        "name": "拾取与放置",
        "description": "更新后的动作说明。",
        "examples": ["拾取连接器", "放入治具"],
        "status": "active",
        "sort_order": 10,
    }
    payload.update(overrides)
    return payload


def test_page_describes_real_lerobot_v3_structure(semantic_platform: TestClient) -> None:
    response = semantic_platform.get("/semantics/")

    assert response.status_code == 200
    assert "meta/info.json" in response.text
    assert "meta/tasks.parquet" in response.text
    assert "data/chunk-*/file-*.parquet" in response.text
    assert "episode_0001/" not in response.text
    assert "trajectory.parquet" not in response.text


def test_create_list_and_render_definition(semantic_platform: TestClient) -> None:
    created = semantic_platform.post(
        "/semantics/api/definitions", json=definition_payload()
    )

    assert created.status_code == 201
    assert created.json()["id"] == "SD000001"
    assert created.json()["code"] == "pick_and_place"

    listed = semantic_platform.get("/semantics/api/definitions?category=action")
    assert listed.status_code == 200
    assert listed.json()[0]["category_label"] == "动作类型"
    assert listed.json()[0]["status_label"] == "启用"

    page = semantic_platform.get("/semantics/?q=pick_and_place")
    assert "SD000001 · 拾取并放置" in page.text
    assert "抓取红色方块并放入料盒" in page.text


def test_duplicate_code_and_name_are_rejected(semantic_platform: TestClient) -> None:
    assert semantic_platform.post(
        "/semantics/api/definitions", json=definition_payload()
    ).status_code == 201

    duplicate_code = semantic_platform.post(
        "/semantics/api/definitions",
        json=definition_payload(name="另一名称"),
    )
    duplicate_name = semantic_platform.post(
        "/semantics/api/definitions",
        json=definition_payload(code="another_action"),
    )

    assert duplicate_code.status_code == 409
    assert duplicate_code.json()["detail"] == "该类别下的机器编码已经存在"
    assert duplicate_name.status_code == 409
    assert duplicate_name.json()["detail"] == "该类别下的显示名称已经存在"


def test_update_status_restore_and_no_delete(semantic_platform: TestClient) -> None:
    definition_id = semantic_platform.post(
        "/semantics/api/definitions", json=definition_payload()
    ).json()["id"]

    updated = semantic_platform.put(
        f"/semantics/api/definitions/{definition_id}", json=update_payload()
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "拾取与放置"
    assert updated.json()["code"] == "pick_and_place"
    assert updated.json()["sort_order"] == 10

    deactivated = semantic_platform.post(
        f"/semantics/api/definitions/{definition_id}/deactivate"
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"
    assert semantic_platform.get(
        "/semantics/api/definitions?status=inactive"
    ).json()[0]["id"] == definition_id

    restored = semantic_platform.post(
        f"/semantics/api/definitions/{definition_id}/activate"
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert semantic_platform.delete(
        f"/semantics/api/definitions/{definition_id}"
    ).status_code == 405


def test_validation_and_missing_definition_errors(semantic_platform: TestClient) -> None:
    invalid_code = semantic_platform.post(
        "/semantics/api/definitions",
        json=definition_payload(code="中文 编码"),
    )
    invalid_category = semantic_platform.post(
        "/semantics/api/definitions",
        json=definition_payload(category="sensor"),
    )

    assert invalid_code.status_code == 422
    assert invalid_category.status_code == 422
    assert semantic_platform.get(
        "/semantics/api/definitions?category=sensor"
    ).status_code == 422
    assert semantic_platform.post(
        "/semantics/api/definitions/SD999999/deactivate"
    ).status_code == 404
