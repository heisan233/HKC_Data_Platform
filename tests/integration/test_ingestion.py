import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient

from app.db.datasets import DatasetRepository
from app.main import app
from app.modules.ingestion import router as ingestion_router

client = TestClient(app)


def make_lerobot_v3_zip(
    *,
    version: str = "v3.0",
    unsafe_path: bool = False,
    robot_type: str | None = "test_robot",
) -> bytes:
    """生成不含真实业务数据的最小结构样本。"""

    payload = BytesIO()
    info = {
        "codebase_version": version,
        "robot_type": robot_type,
        "fps": 30,
        "total_episodes": 1,
        "total_frames": 2,
        "features": {
            "observation.state": {
                "dtype": "float32",
                "shape": [2],
                "names": ["joint_1", "joint_2"],
            }
        },
    }
    with ZipFile(payload, "w", ZIP_DEFLATED) as archive:
        archive.writestr("sample/meta/info.json", json.dumps(info))
        archive.writestr("sample/meta/stats.json", "{}")
        archive.writestr("sample/meta/tasks.parquet", b"PAR1-test-tasks")
        archive.writestr(
            "sample/meta/episodes/chunk-000/file-000.parquet",
            b"PAR1-test-episodes",
        )
        archive.writestr("sample/data/chunk-000/file-000.parquet", b"PAR1-test-data")
        if unsafe_path:
            archive.writestr("sample/../escape.txt", "must not escape")
    return payload.getvalue()


def use_temporary_storage(monkeypatch, data_root: Path) -> None:
    monkeypatch.setattr(
        ingestion_router,
        "settings",
        SimpleNamespace(data_root=data_root),
    )


def test_valid_v3_zip_is_extracted_to_raw(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "测试采集数据", "description": "自动化测试"},
        files={
            "archive": (
                "sample.zip",
                make_lerobot_v3_zip(),
                "application/zip",
            )
        },
    )

    assert response.status_code == 201
    result = response.json()
    assert result["ok"] is True
    assert result["dataset_id"] == "DS000001"
    assert result["summary"]["total_episodes"] == 1
    dataset_root = tmp_path / result["raw_relative_path"]
    assert (dataset_root / "meta" / "info.json").is_file()
    assert (dataset_root / "data" / "chunk-000" / "file-000.parquet").is_file()
    assert list((tmp_path / "system" / "upload-records").glob("*.json"))
    assert DatasetRepository(tmp_path).get("DS000001")["storage_relpath"] == result["storage_name"]
    assert not any((tmp_path / "staging" / "uploads").iterdir())


def test_non_v3_archive_is_rejected(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "错误版本数据"},
        files={"archive": ("sample.zip", make_lerobot_v3_zip(version="v2.1"))},
    )

    assert response.status_code == 400
    assert response.json()["status"] == "rejected"
    assert "v3.0" in response.json()["message"]
    assert not (tmp_path / "raw").exists()


def test_v3_without_robot_type_is_registered_as_unspecified(
    monkeypatch, tmp_path: Path
) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "未声明机器人数据"},
        files={"archive": ("sample.zip", make_lerobot_v3_zip(robot_type=None))},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["summary"]["robot_type"] == "未声明"
    assert DatasetRepository(tmp_path).get(result["dataset_id"])["robot_type"] == "未声明"


def test_zip_path_traversal_is_rejected(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "危险路径数据"},
        files={
            "archive": (
                "sample.zip",
                make_lerobot_v3_zip(unsafe_path=True),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert response.json()["status"] == "rejected"
    assert "../" in response.json()["message"]
    assert not (tmp_path / "escape.txt").exists()
