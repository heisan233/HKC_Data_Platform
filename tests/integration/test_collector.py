"""Robot-token distributed collection API and exact-ZIP deduplication tests."""

import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.dataset_robots import DatasetRobotRepository
from app.db.datasets import DatasetRepository
from app.db.robot_tokens import RobotTokenRepository
from app.db.robots import RobotRepository
from app.main import app
from app.modules.collector import router as collector_router


def make_zip(robot_type: str | None = "so100") -> bytes:
    payload = BytesIO()
    info = {
        "codebase_version": "v3.0",
        "fps": 30,
        "total_episodes": 1,
        "total_frames": 2,
        "features": {"observation.state": {"dtype": "float32", "shape": [2]}},
    }
    if robot_type is not None:
        info["robot_type"] = robot_type
    with ZipFile(payload, "w", ZIP_DEFLATED) as archive:
        archive.writestr("sample/meta/info.json", json.dumps(info))
        archive.writestr("sample/meta/stats.json", "{}")
        archive.writestr("sample/meta/tasks.parquet", b"PAR1-tasks")
        archive.writestr("sample/meta/episodes/chunk-000/file-000.parquet", b"PAR1-episodes")
        archive.writestr("sample/data/chunk-000/file-000.parquet", b"PAR1-data")
    return payload.getvalue()


def create_robot(root: Path, name: str, robot_type: str, serial: str) -> tuple[dict, str]:
    robot = RobotRepository(root).create(
        name=name,
        robot_type=robot_type,
        serial_number=serial,
    )
    token = RobotTokenRepository(root).create(robot["id"])["token"]
    return robot, token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def upload(
    client: TestClient,
    token: str,
    data: bytes,
    *,
    declared_type: str = "so100",
    confirmation: str = "",
    declared_digest: str | None = None,
):
    return client.post(
        "/api/v1/collector/uploads",
        headers=auth(token),
        data={
            "archive_sha256": declared_digest or digest(data),
            "archive_size": str(len(data)),
            "archive_robot_type": declared_type,
            "robot_type_confirmation": confirmation,
        },
        files={"archive": ("sample.zip", data, "application/zip")},
    )


@pytest.fixture
def collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    monkeypatch.setattr(collector_router, "settings", Settings(data_root=tmp_path))
    return TestClient(app), tmp_path


def test_profile_requires_valid_token_and_inactive_robot_is_blocked(collector) -> None:
    client, root = collector
    robot, token = create_robot(root, "采集机器人一号", "so100", "COL-001")

    assert client.get("/api/v1/collector/profile").status_code == 401
    assert client.get(
        "/api/v1/collector/profile", headers=auth("hkc_rb_invalid")
    ).status_code == 401
    profile = client.get("/api/v1/collector/profile", headers=auth(token))
    assert profile.status_code == 200
    assert profile.json()["robot"]["id"] == robot["id"]

    RobotRepository(root).set_status(robot["id"], "inactive")
    blocked = client.get("/api/v1/collector/profile", headers=auth(token))
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "robot_inactive"


def test_missing_robot_type_requires_confirmation_then_uploads_and_links(collector) -> None:
    client, root = collector
    robot, token = create_robot(root, "采集机器人一号", "so100", "COL-001")
    data = make_zip(robot_type=None)

    rejected = upload(client, token, data, declared_type="")
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "robot_type_confirmation_required"
    assert rejected.json()["expected_robot_type"] == "so100"

    accepted = upload(
        client,
        token,
        data,
        declared_type="",
        confirmation="so100",
    )
    assert accepted.status_code == 201
    result = accepted.json()
    dataset = DatasetRepository(root).get(result["dataset_id"])
    assert dataset["availability"] == "unavailable"
    assert dataset["status_labels"] == ["待质检"]
    assert DatasetRobotRepository(root).get(result["dataset_id"])["robot_id"] == robot["id"]
    info = json.loads(
        (root / "data" / result["dataset_id"] / "meta" / "info.json").read_text(
            encoding="utf-8"
        )
    )
    assert info["robot_type"] == "so100"


def test_archive_robot_type_mismatch_is_rejected_even_if_client_lies(collector) -> None:
    client, root = collector
    _robot, token = create_robot(root, "采集机器人一号", "so100", "COL-001")
    data = make_zip(robot_type="aloha")

    preflight = client.post(
        "/api/v1/collector/uploads/preflight",
        headers=auth(token),
        json={
            "archive_sha256": digest(data),
            "archive_size": len(data),
            "filename": "sample.zip",
            "archive_robot_type": "aloha",
        },
    )
    assert preflight.status_code == 409
    assert preflight.json()["archive_robot_type"] == "aloha"
    assert preflight.json()["expected_robot_type"] == "so100"

    lied = upload(client, token, data, declared_type="so100")
    assert lied.status_code == 409
    assert lied.json()["code"] == "robot_type_mismatch"
    assert DatasetRepository(root).list() == []

def test_same_robot_duplicate_returns_existing_dataset(collector) -> None:
    client, root = collector
    robot, token = create_robot(root, "采集机器人一号", "so100", "COL-001")
    data = make_zip()

    created = upload(client, token, data)
    assert created.status_code == 201
    dataset_id = created.json()["dataset_id"]
    repeated = upload(client, token, data)
    assert repeated.status_code == 200
    assert repeated.json()["action"] == "existing"
    assert repeated.json()["dataset_id"] == dataset_id
    assert repeated.json()["robot_id"] == robot["id"]
    assert len(DatasetRepository(root).list()) == 1


def test_same_type_other_robot_requires_confirmation_and_rebinds(collector) -> None:
    client, root = collector
    first, first_token = create_robot(root, "采集机器人一号", "so100", "COL-001")
    second, second_token = create_robot(root, "采集机器人二号", "so100", "COL-002")
    data = make_zip()
    dataset_id = upload(client, first_token, data).json()["dataset_id"]

    preflight = client.post(
        "/api/v1/collector/uploads/preflight",
        headers=auth(second_token),
        json={
            "archive_sha256": digest(data),
            "archive_size": len(data),
            "filename": "sample.zip",
            "archive_robot_type": "so100",
        },
    )
    assert preflight.status_code == 409
    assert preflight.json()["code"] == "duplicate_rebind_confirmation_required"
    assert preflight.json()["existing_robot_id"] == first["id"]

    rebound = client.post(
        "/api/v1/collector/uploads/rebind",
        headers=auth(second_token),
        json={
            "archive_sha256": digest(data),
            "dataset_id": dataset_id,
            "confirm": True,
        },
    )
    assert rebound.status_code == 200
    assert rebound.json()["action"] == "rebound"
    assert DatasetRobotRepository(root).get(dataset_id)["robot_id"] == second["id"]
    assert len(DatasetRepository(root).list()) == 1


def test_different_type_other_robot_cannot_claim_same_zip(collector) -> None:
    client, root = collector
    first, first_token = create_robot(root, "采集机器人一号", "so100", "COL-001")
    second, second_token = create_robot(root, "采集机器人二号", "aloha", "COL-002")
    data = make_zip()
    dataset_id = upload(client, first_token, data).json()["dataset_id"]

    conflict = client.post(
        "/api/v1/collector/uploads/preflight",
        headers=auth(second_token),
        json={
            "archive_sha256": digest(data),
            "archive_size": len(data),
            "filename": "sample.zip",
            "archive_robot_type": "aloha",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "duplicate_robot_type_mismatch"
    assert conflict.json()["existing_robot_id"] == first["id"]
    assert conflict.json()["current_robot_id"] == second["id"]
    assert DatasetRobotRepository(root).get(dataset_id)["robot_id"] == first["id"]


def test_uploaded_bytes_must_match_declared_sha256(collector) -> None:
    client, root = collector
    _robot, token = create_robot(root, "采集机器人一号", "so100", "COL-001")
    data = make_zip()
    wrong_digest = "0" * 64

    result = upload(client, token, data, declared_digest=wrong_digest)
    assert result.status_code == 400
    assert result.json()["code"] == "sha256_mismatch"
    assert DatasetRepository(root).list() == []
