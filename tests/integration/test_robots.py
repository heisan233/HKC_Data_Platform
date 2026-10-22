"""Independent robot registry page and API tests."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.modules.robots import router as robot_router


@pytest.fixture
def robot_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(robot_router, "settings", Settings(data_root=tmp_path))
    return TestClient(app)


def robot_payload(**overrides) -> dict:
    payload = {
        "name": "采集机器人一号",
        "robot_type": "so100",
        "model": "SO-100",
        "serial_number": "SN-0001",
        "status": "active",
        "network_address": "192.168.123.50",
        "sensors": ["front_camera", "wrist_camera"],
        "notes": "测试档案",
    }
    payload.update(overrides)
    return payload


def test_empty_page_has_no_demo_robots(robot_platform: TestClient) -> None:
    response = robot_platform.get("/robots/")

    assert response.status_code == 200
    assert "当前条件下没有机器人档案" in response.text
    assert "人形机器人-A1" not in response.text


def test_create_list_and_render_robot(robot_platform: TestClient) -> None:
    created = robot_platform.post("/robots/api/robots", json=robot_payload())

    assert created.status_code == 201
    assert created.json()["id"] == "RB000001"
    assert created.json()["sensors"] == ["front_camera", "wrist_camera"]

    listed = robot_platform.get("/robots/api/robots")
    assert listed.status_code == 200
    assert listed.json()[0]["status_label"] == "在用"
    assert listed.json()[0]["matching_dataset_count"] == 0

    page = robot_platform.get("/robots/")
    assert "RB000001 · 采集机器人一号" in page.text
    assert "SN-0001" in page.text
    assert "front_camera" in page.text


def test_duplicate_name_and_serial_are_rejected(robot_platform: TestClient) -> None:
    assert robot_platform.post("/robots/api/robots", json=robot_payload()).status_code == 201

    duplicate_name = robot_platform.post(
        "/robots/api/robots",
        json=robot_payload(name="采集机器人一号", serial_number="SN-0002"),
    )
    duplicate_serial = robot_platform.post(
        "/robots/api/robots",
        json=robot_payload(name="采集机器人二号", serial_number="sn-0001"),
    )

    assert duplicate_name.status_code == 409
    assert duplicate_name.json()["detail"] == "机器人名称已经存在"
    assert duplicate_serial.status_code == 409
    assert duplicate_serial.json()["detail"] == "序列号已经被其他机器人使用"


def test_update_deactivate_restore_and_no_delete(robot_platform: TestClient) -> None:
    robot_id = robot_platform.post("/robots/api/robots", json=robot_payload()).json()["id"]
    changed_payload = robot_payload(
        name="采集机器人一号-改",
        status="maintenance",
        sensors=["front_camera"],
    )

    updated = robot_platform.put(f"/robots/api/robots/{robot_id}", json=changed_payload)
    assert updated.status_code == 200
    assert updated.json()["name"] == "采集机器人一号-改"
    assert updated.json()["status"] == "maintenance"

    deactivated = robot_platform.post(f"/robots/api/robots/{robot_id}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"
    assert robot_platform.get("/robots/api/robots?status=inactive").json()[0]["id"] == robot_id

    restored = robot_platform.post(f"/robots/api/robots/{robot_id}/activate")
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert robot_platform.delete(f"/robots/api/robots/{robot_id}").status_code == 405


def test_validation_and_missing_robot_errors(robot_platform: TestClient) -> None:
    invalid = robot_platform.post(
        "/robots/api/robots",
        json=robot_payload(name="   ", status="online"),
    )

    assert invalid.status_code == 422
    assert robot_platform.get("/robots/api/robots?status=online").status_code == 422
    assert robot_platform.post("/robots/api/robots/RB999999/deactivate").status_code == 404
