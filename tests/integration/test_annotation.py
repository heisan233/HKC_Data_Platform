import json
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from app.main import app
from app.modules.annotation import router as annotation_router

client = TestClient(app)
CAMERA = "observation.images.head"


def make_v3_dataset(data_root: Path, dataset_id: str = "ds-test") -> Path:
    dataset = data_root / "raw" / dataset_id
    (dataset / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (dataset / "data" / "chunk-000").mkdir(parents=True)
    video_root = dataset / "videos" / CAMERA / "chunk-000"
    video_root.mkdir(parents=True)

    info = {
        "codebase_version": "v3.0",
        "robot_type": "test_robot",
        "fps": 20,
        "total_episodes": 2,
        "total_frames": 4,
        "total_tasks": 1,
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {CAMERA: {"dtype": "video", "shape": [480, 640, 3]}},
    }
    (dataset / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    pq.write_table(
        pa.table({"task_index": pa.array([0], type=pa.int64()), "task": ["old task"]}),
        dataset / "meta" / "tasks.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "episode_index": pa.array([0, 1], type=pa.int64()),
                "dataset_from_index": pa.array([0, 2], type=pa.int64()),
                "dataset_to_index": pa.array([1, 3], type=pa.int64()),
                "task": ["old task", "old task"],
                "num_frames": pa.array([2, 2], type=pa.int64()),
                f"videos/{CAMERA}/chunk_index": pa.array([0, 0], type=pa.int64()),
                f"videos/{CAMERA}/file_index": pa.array([0, 1], type=pa.int64()),
                f"videos/{CAMERA}/from_timestamp": [0.0, 0.0],
                f"videos/{CAMERA}/to_timestamp": [0.1, 0.1],
            }
        ),
        dataset / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "episode_index": pa.array([0, 0, 1, 1], type=pa.int64()),
                "frame_index": pa.array([0, 1, 0, 1], type=pa.int64()),
                "task_index": pa.array([0, 0, 0, 0], type=pa.int64()),
            }
        ),
        dataset / "data" / "chunk-000" / "file-000.parquet",
    )
    (video_root / "file-000.mp4").write_bytes(b"0123456789-episode-zero")
    (video_root / "file-001.mp4").write_bytes(b"0123456789-episode-one")
    return dataset


def use_temporary_storage(monkeypatch, data_root: Path) -> None:
    monkeypatch.setattr(annotation_router, "settings", SimpleNamespace(data_root=data_root))


def test_workspace_reads_real_episode_tasks_and_video(monkeypatch, tmp_path: Path) -> None:
    make_v3_dataset(tmp_path)
    use_temporary_storage(monkeypatch, tmp_path)

    listing = client.get("/annotation/api/datasets")
    workspace = client.get("/annotation/api/datasets/ds-test")
    video = client.get(
        "/annotation/api/datasets/ds-test/episodes/0/video",
        params={"camera": CAMERA},
        headers={"Range": "bytes=0-4"},
    )

    assert listing.status_code == 200
    assert listing.json()["datasets"][0]["total_episodes"] == 2
    assert workspace.status_code == 200
    assert workspace.json()["episodes"][0]["task"] == "old task"
    assert workspace.json()["episodes"][0]["videos"][0]["camera"] == CAMERA
    assert video.status_code == 206
    assert video.content == b"01234"


def test_publish_creates_full_derived_copy_and_keeps_raw_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    raw = make_v3_dataset(tmp_path)
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/annotation/api/publish",
        json={
            "dataset_id": "ds-test",
            "version_name": "task 标注 v1",
            "description": "测试完整派生发布",
            "changes": [{"episode_index": 0, "task": "new task"}],
        },
    )

    assert response.status_code == 201
    result = response.json()
    derived = tmp_path / result["derived_relative_path"]
    assert derived.is_dir()
    assert (derived / "videos" / CAMERA / "chunk-000" / "file-001.mp4").is_file()

    raw_tasks = pq.read_table(raw / "meta" / "tasks.parquet").to_pylist()
    raw_episodes = pq.read_table(
        raw / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    ).to_pylist()
    assert raw_tasks == [{"task_index": 0, "task": "old task"}]
    assert [row["task"] for row in raw_episodes] == ["old task", "old task"]

    derived_tasks = pq.read_table(derived / "meta" / "tasks.parquet").to_pylist()
    derived_episodes = pq.read_table(
        derived / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    ).to_pylist()
    derived_frames = pq.read_table(
        derived / "data" / "chunk-000" / "file-000.parquet"
    ).to_pylist()
    derived_info = json.loads((derived / "meta" / "info.json").read_text())

    assert derived_tasks == [
        {"task_index": 0, "task": "old task"},
        {"task_index": 1, "task": "new task"},
    ]
    assert [row["task"] for row in derived_episodes] == ["new task", "old task"]
    assert [row["task_index"] for row in derived_frames] == [1, 1, 0, 0]
    assert derived_info["total_tasks"] == 2
    assert list((tmp_path / "system" / "dataset-versions").glob("*.json"))
    assert not (tmp_path / "staging" / "annotation" / result["dataset_id"]).exists()
