"""Real LeRobot v3 schema editor tests."""

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.datasets import DatasetRepository
from app.main import app
from app.modules.datasets.service import register_dataset
from app.modules.semantics import router as semantics_router


def make_schema_dataset(root: Path, name: str = "source") -> tuple[str, Path]:
    folder = root / "raw" / name
    (folder / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (folder / "data" / "chunk-000").mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "robot_type": "so100",
        "fps": 10,
        "total_episodes": 2,
        "total_frames": 4,
        "total_tasks": 1,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "features": {
            "value": {"dtype": "float32", "shape": [1], "names": None},
            "index": {"dtype": "int64", "shape": [1], "names": None},
            "episode_index": {"dtype": "int64", "shape": [1], "names": None},
            "frame_index": {"dtype": "int64", "shape": [1], "names": None},
            "timestamp": {"dtype": "float32", "shape": [1], "names": None},
            "task_index": {"dtype": "int64", "shape": [1], "names": None},
        },
    }
    (folder / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    (folder / "meta" / "stats.json").write_text(
        json.dumps({"value": {"mean": [2.5]}}), encoding="utf-8"
    )
    pd.DataFrame(
        {"task_index": [0]}, index=pd.Index(["test task"], name="task")
    ).to_parquet(folder / "meta" / "tasks.parquet")
    pq.write_table(
        pa.table(
            {
                "episode_index": [0, 1],
                "dataset_from_index": [0, 2],
                "dataset_to_index": [2, 4],
                "length": [2, 2],
                "tasks": [["test task"], ["test task"]],
                "data/chunk_index": [0, 0],
                "data/file_index": [0, 0],
            }
        ),
        folder / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "value": pa.array([1, 2, 3, 4], type=pa.float32()),
                "index": pa.array([0, 1, 2, 3], type=pa.int64()),
                "episode_index": pa.array([0, 0, 1, 1], type=pa.int64()),
                "frame_index": pa.array([0, 1, 0, 1], type=pa.int64()),
                "timestamp": pa.array([0.0, 0.1, 0.0, 0.1], type=pa.float32()),
                "task_index": pa.array([0, 0, 0, 0], type=pa.int64()),
            }
        ),
        folder / "data" / "chunk-000" / "file-000.parquet",
    )
    record = register_dataset(root, "raw", name, "测试数据集")
    return record["id"], folder


@pytest.fixture
def schema_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    monkeypatch.setattr(semantics_router, "settings", Settings(data_root=tmp_path))
    return TestClient(app), tmp_path


def test_page_is_dataset_schema_editor(schema_platform: tuple[TestClient, Path]) -> None:
    client, _ = schema_platform
    response = client.get("/semantics/")
    assert response.status_code == 200
    assert "数据集结构定义" in response.text
    assert "平台受控词表" not in response.text


def test_scan_reads_real_info_and_parquet(schema_platform: tuple[TestClient, Path]) -> None:
    client, root = schema_platform
    dataset_id, _ = make_schema_dataset(root)

    response = client.get(f"/semantics/api/datasets/{dataset_id}/schema")

    assert response.status_code == 200
    body = response.json()
    assert body["dataset"]["total_episodes"] == 2
    assert body["status"]["training_ready"] is True
    assert {item["name"] for item in body["episode_fields"]} >= {"episode_index", "length"}
    assert {item["name"] for item in body["frame_features"]} >= {"value", "frame_index"}


def test_external_video_features_do_not_block_training_status(
    schema_platform: tuple[TestClient, Path],
) -> None:
    client, root = schema_platform
    dataset_id, folder = make_schema_dataset(root)
    info_path = folder / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["features"]["observation.images.front"] = {
        "dtype": "video",
        "shape": [480, 640, 3],
        "names": ["height", "width", "channels"],
    }
    info["features"]["observation.images.wrist"] = {
        "dtype": "video",
        "shape": [480, 640, 3],
        "names": ["height", "width", "channels"],
    }
    info_path.write_text(json.dumps(info), encoding="utf-8")

    response = client.get(f"/semantics/api/datasets/{dataset_id}/schema")

    assert response.status_code == 200
    body = response.json()
    camera_rows = {
        item["name"]: item
        for item in body["frame_features"]
        if item["name"].startswith("observation.images.")
    }
    assert camera_rows["observation.images.front"]["state"] == "external_media"
    assert camera_rows["observation.images.wrist"]["state"] == "external_media"
    assert body["status"]["training_ready"] is True
    assert body["status"]["training_blockers"] == []


def test_add_uniform_episode_field_creates_derived_dataset(
    schema_platform: tuple[TestClient, Path],
) -> None:
    client, root = schema_platform
    dataset_id, source = make_schema_dataset(root)

    response = client.post(
        f"/semantics/api/datasets/{dataset_id}/fields",
        json={
            "scope": "episode",
            "name": "scene",
            "dtype": "string",
            "shape": [1],
            "nullable": False,
            "value": "lab",
            "description": "采集场景",
            "version_name": "增加场景字段",
        },
    )

    assert response.status_code == 201
    result = response.json()["result"]
    record = next(
        item
        for item in client.get("/semantics/api/datasets").json()["datasets"]
        if item["dataset_id"] == result["dataset_id"]
    )
    derived = root / "derived" / next(
        row["storage_relpath"]
        for row in DatasetRepository(root).list()
        if row["id"] == result["dataset_id"]
    )
    info = json.loads((derived / "meta" / "info.json").read_text(encoding="utf-8"))
    rows = pq.read_table(
        derived / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    ).to_pylist()
    assert record["storage_area"] == "derived"
    assert info["hkc_extensions"]["episode_features"]["scene"]["dtype"] == "string"
    assert [row["scene"] for row in rows] == ["lab", "lab"]
    assert "scene" not in pq.read_schema(
        source / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    ).names


def test_add_empty_frame_vector_marks_not_training_ready(
    schema_platform: tuple[TestClient, Path],
) -> None:
    client, root = schema_platform
    dataset_id, _ = make_schema_dataset(root)

    response = client.post(
        f"/semantics/api/datasets/{dataset_id}/fields",
        json={
            "scope": "frame",
            "name": "observation.extra_joints",
            "dtype": "float32",
            "shape": [2],
            "names": ["joint_a", "joint_b"],
            "description": "待补充关节",
            "version_name": "增加空关节字段",
        },
    )

    assert response.status_code == 201
    derived_id = response.json()["result"]["dataset_id"]
    scanned = client.get(f"/semantics/api/datasets/{derived_id}/schema").json()
    field = next(item for item in scanned["frame_features"]
                 if item["name"] == "observation.extra_joints")
    assert field["state"] == "declared_empty"
    assert field["completeness"] == 0
    assert scanned["status"]["training_ready"] is False
    assert scanned["status"]["training_blockers"] == ["observation.extra_joints"]


def test_import_vector_feature_by_episode_and_frame_index(
    schema_platform: tuple[TestClient, Path],
) -> None:
    client, root = schema_platform
    dataset_id, _ = make_schema_dataset(root)
    csv = (
        "episode_index,frame_index,joint_a,joint_b\n"
        "0,0,1.0,2.0\n0,1,3.0,4.0\n1,0,5.0,6.0\n1,1,7.0,8.0\n"
    )

    response = client.post(
        f"/semantics/api/datasets/{dataset_id}/frame-import",
        data={
            "field_name": "observation.extra_joints",
            "dtype": "float32",
            "shape": "[2]",
            "source_columns": '["joint_a", "joint_b"]',
            "names": '["joint_a", "joint_b"]',
            "description": "导入关节",
            "version_name": "导入关节数据",
        },
        files={"import_file": ("joints.csv", csv, "text/csv")},
    )

    assert response.status_code == 201, response.text
    derived_id = response.json()["result"]["dataset_id"]
    scanned = client.get(f"/semantics/api/datasets/{derived_id}/schema").json()
    field = next(item for item in scanned["frame_features"]
                 if item["name"] == "observation.extra_joints")
    assert field["state"] == "complete"
    assert field["completeness"] == 100
    assert scanned["status"]["training_ready"] is True


def test_delete_protects_core_and_requires_official_confirmation(
    schema_platform: tuple[TestClient, Path],
) -> None:
    client, root = schema_platform
    dataset_id, _ = make_schema_dataset(root)
    common = {"version_name": "删除字段测试", "version_description": ""}

    core = client.post(
        f"/semantics/api/datasets/{dataset_id}/fields/delete",
        json={"scope": "frame", "name": "frame_index", "confirm_official": True, **common},
    )
    warning = client.post(
        f"/semantics/api/datasets/{dataset_id}/fields/delete",
        json={"scope": "frame", "name": "value", "confirm_official": False, **common},
    )
    confirmed = client.post(
        f"/semantics/api/datasets/{dataset_id}/fields/delete",
        json={"scope": "frame", "name": "value", "confirm_official": True, **common},
    )

    assert core.status_code == 400
    assert "禁止删除" in core.json()["message"]
    assert warning.status_code == 400
    assert "明确确认" in warning.json()["message"]
    assert confirmed.status_code == 200
    derived_id = confirmed.json()["result"]["dataset_id"]
    scanned = client.get(f"/semantics/api/datasets/{derived_id}/schema").json()
    assert "value" not in {item["name"] for item in scanned["frame_features"]}
