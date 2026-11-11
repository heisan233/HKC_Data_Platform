"""Independent robot registry page and API tests."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.dataset_robots import DatasetRobotRepository
from app.db.datasets import DatasetRepository
from app.db.robot_tokens import RobotTokenRepository
from app.main import app
from app.modules.datasets import service as dataset_service
from app.modules.datasets import trash as dataset_trash
from app.modules.robots import router as robot_router


@pytest.fixture
def robot_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(robot_router, "settings", Settings(data_root=tmp_path))
    return TestClient(app)


def robot_payload(**overrides) -> dict:
    payload = {
        "name": "采集机器人一号",
        "robot_type": "so100",
        "serial_number": "SN-0001",
        "network_address": "192.168.123.50",
        "notes": "测试档案",
    }
    payload.update(overrides)
    return payload


def registered_dataset(root: Path, name: str, robot_type: str = "so100") -> str:
    return DatasetRepository(root).register(
        "raw", name, name, robot_type, f"digest-{name}",
    )["id"]


def test_empty_page_has_no_demo_robots(robot_platform: TestClient) -> None:
    response = robot_platform.get("/robots/")

    assert response.status_code == 200
    assert "当前条件下没有机器人档案" in response.text
    assert "人形机器人-A1" not in response.text


def test_create_list_and_render_robot(
    robot_platform: TestClient,
    tmp_path: Path,
) -> None:
    created = robot_platform.post("/robots/api/robots", json=robot_payload())

    assert created.status_code == 201
    assert created.json()["id"] == "RB000001"
    assert created.json()["status"] == "active"
    assert created.json()["token_configured"] is True
    assert created.json()["token_prefix"].startswith("hkc_rb_")
    assert created.json()["token"].startswith("hkc_rb_")
    assert created.headers["cache-control"] == "no-store"
    assert "model" not in created.json()
    assert "sensors" not in created.json()

    listed = robot_platform.get("/robots/api/robots")
    assert listed.status_code == 200
    assert listed.json()[0]["status_label"] == "在用"
    assert listed.json()[0]["matching_dataset_count"] == 0
    assert listed.json()[0]["token_configured"] is True
    assert "token" not in listed.json()[0]
    assert RobotTokenRepository(tmp_path).get("RB000001", reveal=True)["token"].startswith(
        "hkc_rb_"
    )

    page = robot_platform.get("/robots/")
    assert "RB000001" in page.text
    assert "采集机器人一号" in page.text
    assert "SN-0001" in page.text
    assert "192.168.123.50" in page.text
    assert "robot-token-dialog" in page.text
    assert "型号" not in page.text
    assert "传感器" not in page.text


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


def test_regenerate_token_replaces_old_value_and_does_not_leak_in_list(
    robot_platform: TestClient,
    tmp_path: Path,
) -> None:
    created = robot_platform.post("/robots/api/robots", json=robot_payload()).json()
    old_token = created["token"]

    regenerated = robot_platform.post(
        f"/robots/api/robots/{created['id']}/token/regenerate"
    )

    assert regenerated.status_code == 200
    assert regenerated.headers["cache-control"] == "no-store"
    assert regenerated.json()["token"].startswith("hkc_rb_")
    assert regenerated.json()["token"] != old_token
    assert RobotTokenRepository(tmp_path).authenticate(old_token) is None
    assert RobotTokenRepository(tmp_path).authenticate(regenerated.json()["token"])["id"] == created["id"]
    assert "token" not in robot_platform.get("/robots/api/robots").json()[0]
    page = robot_platform.get("/robots/")
    assert 'data-robot-token-regenerate="RB000001"' in page.text


def test_regenerate_token_requires_existing_robot(robot_platform: TestClient) -> None:
    response = robot_platform.post(
        "/robots/api/robots/RB999999/token/regenerate"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "机器人不存在"


def test_update_deactivate_restore_and_no_delete(robot_platform: TestClient) -> None:
    robot_id = robot_platform.post("/robots/api/robots", json=robot_payload()).json()["id"]
    changed_payload = robot_payload(
        name="采集机器人一号-改",
    )

    updated = robot_platform.put(f"/robots/api/robots/{robot_id}", json=changed_payload)
    assert updated.status_code == 200
    assert updated.json()["name"] == "采集机器人一号-改"
    assert updated.json()["status"] == "active"

    deactivated = robot_platform.post(f"/robots/api/robots/{robot_id}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"
    assert robot_platform.get("/robots/api/robots?status=inactive").json()[0]["id"] == robot_id

    edited_while_inactive = robot_platform.put(
        f"/robots/api/robots/{robot_id}",
        json=robot_payload(name="采集机器人一号-停用中编辑"),
    )
    assert edited_while_inactive.status_code == 200
    assert edited_while_inactive.json()["status"] == "inactive"

    restored = robot_platform.post(f"/robots/api/robots/{robot_id}/activate")
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert robot_platform.delete(f"/robots/api/robots/{robot_id}").status_code == 405


def test_validation_and_missing_robot_errors(robot_platform: TestClient) -> None:
    invalid = robot_platform.post(
        "/robots/api/robots",
        json=robot_payload(name="   "),
    )

    assert invalid.status_code == 422
    unsupported = robot_platform.post(
        "/robots/api/robots",
        json={**robot_payload(), "model": "不再接收的字段"},
    )
    assert unsupported.status_code == 422
    assert robot_platform.get("/robots/api/robots?status=online").status_code == 422
    assert robot_platform.post("/robots/api/robots/RB999999/deactivate").status_code == 404


def test_datasets_start_unlinked_and_can_bind_rebind_and_unlink(
    robot_platform: TestClient,
    tmp_path: Path,
) -> None:
    first = robot_platform.post("/robots/api/robots", json=robot_payload()).json()["id"]
    second = robot_platform.post(
        "/robots/api/robots",
        json=robot_payload(
            name="采集机器人二号", serial_number="SN-0002", robot_type="aloha",
        ),
    ).json()["id"]
    ds1 = registered_dataset(tmp_path, "first")
    ds2 = registered_dataset(tmp_path, "second", "different-type")

    before = robot_platform.get(f"/robots/api/robots/{first}/datasets").json()
    assert before["linked_count"] == 0
    assert all(row["collector_robot_id"] is None for row in before["datasets"])
    assert next(row for row in before["datasets"] if row["id"] == ds2)["type_mismatch"] is True

    bound = robot_platform.post(
        f"/robots/api/robots/{first}/datasets",
        json={"dataset_ids": [ds1, ds2]},
    )
    assert bound.status_code == 200
    assert bound.json()["linked_count"] == 2
    assert DatasetRobotRepository(tmp_path).get(ds1)["updated_by"] == "test_user"
    listed = robot_platform.get("/robots/api/robots").json()
    assert next(row for row in listed if row["id"] == first)["matching_dataset_count"] == 2

    rebound = robot_platform.post(
        f"/robots/api/robots/{second}/datasets", json={"dataset_ids": [ds1]},
    )
    assert rebound.status_code == 200
    assert rebound.json()["linked_count"] == 1
    assert DatasetRobotRepository(tmp_path).get(ds1)["robot_id"] == second
    assert robot_platform.get(f"/robots/api/robots/{first}/datasets").json()["linked_count"] == 1

    removed = robot_platform.delete(f"/robots/api/robots/{second}/datasets/{ds1}")
    assert removed.json() == {"robot_id": second, "dataset_id": ds1, "unlinked": True}
    assert DatasetRobotRepository(tmp_path).get(ds1) is None


def test_inactive_robot_keeps_history_but_cannot_receive_new_links(
    robot_platform: TestClient,
    tmp_path: Path,
) -> None:
    robot_id = robot_platform.post("/robots/api/robots", json=robot_payload()).json()["id"]
    ds1 = registered_dataset(tmp_path, "first")
    ds2 = registered_dataset(tmp_path, "second")
    assert robot_platform.post(
        f"/robots/api/robots/{robot_id}/datasets", json={"dataset_ids": [ds1]},
    ).status_code == 200
    robot_platform.post(f"/robots/api/robots/{robot_id}/deactivate")

    blocked = robot_platform.post(
        f"/robots/api/robots/{robot_id}/datasets", json={"dataset_ids": [ds2]},
    )
    assert blocked.status_code == 409
    assert "已停用" in blocked.json()["detail"]
    assert DatasetRobotRepository(tmp_path).get(ds1)["robot_id"] == robot_id
    assert robot_platform.delete(
        f"/robots/api/robots/{robot_id}/datasets/{ds1}"
    ).status_code == 200


def test_trashed_dataset_stays_visible_until_permanent_purge(
    robot_platform: TestClient,
    tmp_path: Path,
) -> None:
    robot_id = robot_platform.post("/robots/api/robots", json=robot_payload()).json()["id"]
    dataset_id = registered_dataset(tmp_path, "trash-source")
    (tmp_path / "raw" / "trash-source").mkdir(parents=True)
    DatasetRobotRepository(tmp_path).bind(robot_id, [dataset_id], "test_user")

    trashed = dataset_trash.trash_dataset(tmp_path, dataset_id, "test_user")
    assert trashed["deleted_at"] is not None
    bindings = robot_platform.get(f"/robots/api/robots/{robot_id}/datasets").json()
    row = next(item for item in bindings["datasets"] if item["id"] == dataset_id)
    assert row["linked_to_selected"] is True
    assert row["in_trash"] is True
    assert bindings["linked_count"] == 1
    assert robot_platform.get("/robots/api/robots").json()[0][
        "matching_dataset_count"
    ] == 1

    dataset_trash.purge_dataset(tmp_path, dataset_id)
    bindings = robot_platform.get(f"/robots/api/robots/{robot_id}/datasets").json()
    assert all(item["id"] != dataset_id for item in bindings["datasets"])
    assert bindings["linked_count"] == 0
    assert robot_platform.get("/robots/api/robots").json()[0][
        "matching_dataset_count"
    ] == 0


def test_derived_dataset_inherits_only_one_shared_robot(
    robot_platform: TestClient,
    tmp_path: Path,
) -> None:
    robot_id = robot_platform.post("/robots/api/robots", json=robot_payload()).json()["id"]
    source_a = registered_dataset(tmp_path, "source-a")
    source_b = registered_dataset(tmp_path, "source-b")
    source_unlinked = registered_dataset(tmp_path, "source-unlinked")
    DatasetRobotRepository(tmp_path).bind(robot_id, [source_a, source_b], "test_user")

    def derived(name: str, sources: list[str]) -> str:
        folder = tmp_path / "derived" / name / "meta"
        folder.mkdir(parents=True)
        (folder / "info.json").write_text(json.dumps({
            "codebase_version": "v3.0", "robot_type": "so100",
            "total_episodes": 1, "total_frames": 1, "features": {},
        }), encoding="utf-8")
        return dataset_service.register_dataset(
            tmp_path, "derived", name, name, sources,
        )["id"]

    single = derived("single", [source_a])
    merged_same = derived("merged-same", [source_a, source_b])
    merged_partial = derived("merged-partial", [source_a, source_unlinked])

    assert DatasetRobotRepository(tmp_path).get(single)["robot_id"] == robot_id
    assert DatasetRobotRepository(tmp_path).get(merged_same)["robot_id"] == robot_id
    assert DatasetRobotRepository(tmp_path).get(merged_partial) is None
