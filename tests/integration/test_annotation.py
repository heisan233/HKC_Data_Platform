import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from app.main import app
from app.modules.annotation import router as annotation_router
from app.modules.datasets.service import register_dataset

client = TestClient(app)
CAMERA = "observation.images.head"


def make_v3_dataset(
    data_root: Path,
    dataset_id: str = "ds-test",
    *,
    task_as_unnamed_index: bool = False,
) -> Path:
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
    if task_as_unnamed_index:
        pd.DataFrame(
            {"task_index": [0]},
            index=pd.Index(["old task"]),
        ).to_parquet(dataset / "meta" / "tasks.parquet")
    else:
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
                # LeRobot v3 官方 Episode 元数据使用 length，而不是 num_frames。
                "length": pa.array([2, 2], type=pa.int64()),
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
    register_dataset(data_root, "raw", dataset_id, dataset_id)
    return dataset


def use_temporary_storage(monkeypatch, data_root: Path) -> None:
    monkeypatch.setattr(annotation_router, "settings", SimpleNamespace(data_root=data_root))


def test_workspace_reads_real_episode_tasks_and_video(monkeypatch, tmp_path: Path) -> None:
    make_v3_dataset(tmp_path)
    use_temporary_storage(monkeypatch, tmp_path)

    listing = client.get("/annotation/api/datasets")
    workspace = client.get("/annotation/api/datasets/DS000001")
    video = client.get(
        "/annotation/api/datasets/DS000001/episodes/0/video",
        params={"camera": CAMERA},
        headers={"Range": "bytes=0-4"},
    )

    assert listing.status_code == 200
    assert listing.json()["datasets"][0]["total_episodes"] == 2
    assert workspace.status_code == 200
    assert workspace.json()["episodes"][0]["task"] == "old task"
    assert workspace.json()["episodes"][0]["num_frames"] == 2
    assert workspace.json()["episodes"][0]["duration_seconds"] == 0.1
    assert workspace.json()["episodes"][0]["annotation"] == {
        "outcome": None,
        "failure_reason": "",
        "subtasks": [],
    }
    assert workspace.json()["episodes"][0]["videos"][0]["camera"] == CAMERA
    assert video.status_code == 206
    assert video.content == b"01234"


def test_workspace_and_publish_support_official_task_index_layout(
    monkeypatch, tmp_path: Path
) -> None:
    raw = make_v3_dataset(tmp_path, task_as_unnamed_index=True)
    use_temporary_storage(monkeypatch, tmp_path)

    raw_schema = pq.read_schema(raw / "meta" / "tasks.parquet")
    assert raw_schema.names == ["task_index", "__index_level_0__"]

    workspace = client.get("/annotation/api/datasets/DS000001")
    assert workspace.status_code == 200
    assert workspace.json()["tasks"] == [{"task_index": 0, "task": "old task"}]
    assert workspace.json()["episodes"][0]["task"] == "old task"

    response = client.post(
        "/annotation/api/publish",
        json={
            "dataset_id": "DS000001",
            "version_name": "官方 task 索引 v1",
            "changes": [{"episode_index": 0, "task": "new official task"}],
        },
    )

    assert response.status_code == 201
    derived = tmp_path / response.json()["derived_relative_path"]
    derived_tasks = pd.read_parquet(derived / "meta" / "tasks.parquet")
    assert derived_tasks.index.name == "task"
    assert list(derived_tasks.index) == ["old task", "new official task"]
    assert derived_tasks["task_index"].tolist() == [0, 1]


def test_publish_creates_full_derived_copy_and_keeps_raw_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    raw = make_v3_dataset(tmp_path)
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/annotation/api/publish",
        json={
            "dataset_id": "DS000001",
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
    assert not (tmp_path / "staging" / "annotation" / result["storage_name"]).exists()


def test_publish_episode_outcome_and_subtasks_as_sidecar_annotations(
    monkeypatch, tmp_path: Path
) -> None:
    raw = make_v3_dataset(tmp_path)
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/annotation/api/publish",
        json={
            "dataset_id": "DS000001",
            "version_name": "动作段标注 v1",
            "description": "结果与子任务标注",
            "annotations": [
                {
                    "episode_index": 0,
                    "outcome": "partial",
                    "failure_reason": "放置位置略有偏差",
                    "subtasks": [
                        {
                            "start_time": 0.0,
                            "end_time": 0.05,
                            "description": "接近目标物",
                            "action_type": "reach",
                            "object_name": "red block",
                            "outcome": "success",
                        },
                        {
                            "start_time": 0.05,
                            "end_time": 0.1,
                            "description": "抓取目标物",
                            "action_type": "grasp",
                            "object_name": "red block",
                            "outcome": "partial",
                        },
                    ],
                }
            ],
        },
    )

    assert response.status_code == 201
    result = response.json()
    derived = tmp_path / result["derived_relative_path"]
    assert not (raw / "meta" / "annotations").exists()
    assert result["changed_task_count"] == 0
    assert result["annotated_episode_count"] == 1
    assert result["subtask_segment_count"] == 2

    episode_rows = pq.read_table(
        derived / "meta" / "annotations" / "episodes.parquet"
    ).to_pylist()
    subtask_rows = pq.read_table(
        derived / "meta" / "annotations" / "subtasks.parquet"
    ).to_pylist()
    assert episode_rows == [
        {
            "episode_index": 0,
            "outcome": "partial",
            "failure_reason": "放置位置略有偏差",
        }
    ]
    assert [row["segment_index"] for row in subtask_rows] == [0, 1]
    assert [row["start_frame"] for row in subtask_rows] == [0, 1]
    assert [row["end_frame_exclusive"] for row in subtask_rows] == [1, 2]

    workspace = client.get(f"/annotation/api/datasets/{result['dataset_id']}")
    assert workspace.status_code == 200
    annotation = workspace.json()["episodes"][0]["annotation"]
    assert annotation["outcome"] == "partial"
    assert annotation["failure_reason"] == "放置位置略有偏差"
    assert [item["description"] for item in annotation["subtasks"]] == [
        "接近目标物",
        "抓取目标物",
    ]


def test_annotation_rejects_missing_reason_and_overlapping_subtasks(
    monkeypatch, tmp_path: Path
) -> None:
    make_v3_dataset(tmp_path)
    use_temporary_storage(monkeypatch, tmp_path)
    base = {
        "dataset_id": "DS000001",
        "version_name": "无效标注 v1",
        "annotations": [
            {
                "episode_index": 0,
                "outcome": "failure",
                "failure_reason": "",
                "subtasks": [],
            }
        ],
    }

    missing_reason = client.post("/annotation/api/publish", json=base)
    assert missing_reason.status_code == 400
    assert "必须填写原因" in missing_reason.json()["message"]

    base["annotations"][0]["failure_reason"] = "抓取失败"
    base["annotations"][0]["subtasks"] = [
        {
            "start_time": 0.0,
            "end_time": 0.08,
            "description": "接近",
            "action_type": "reach",
            "object_name": "block",
            "outcome": "success",
        },
        {
            "start_time": 0.05,
            "end_time": 0.1,
            "description": "抓取",
            "action_type": "grasp",
            "object_name": "block",
            "outcome": "failure",
        },
    ]
    overlap = client.post("/annotation/api/publish", json=base)
    assert overlap.status_code == 400
    assert "不能互相重叠" in overlap.json()["message"]
