"""Focused coverage for EGO LeRobot v3 episode metadata and vectors."""

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.modules.datasets import ego_detail


def test_episode_browser_and_vector_equality(tmp_path):
    folder = tmp_path / "data" / "DS000001"
    (folder / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (folder / "data" / "chunk-000").mkdir(parents=True)
    video = folder / "videos" / "observation.images.cam_high" / "chunk-000" / "file-000.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    info = {
        "codebase_version": "v3.0", "fps": 30,
        "total_episodes": 1, "total_frames": 3,
        "features": {
            "observation.state": {"dtype": "float32", "shape": [16]},
            "action": {"dtype": "float32", "shape": [16]},
            "observation.images.cam_high": {"dtype": "video", "shape": [3, 1080, 1920]},
        },
    }
    (folder / "meta" / "info.json").write_text(json.dumps(info))
    pq.write_table(pa.table({"task_index": [0], "task": ["Pick up a cup"]}),
                   folder / "meta" / "tasks.parquet")
    pq.write_table(pa.table({
        "episode_index": [0], "length": [3], "tasks": [["Pick up a cup"]],
        "data/chunk_index": [0], "data/file_index": [0],
        "videos/observation.images.cam_high/chunk_index": [0],
        "videos/observation.images.cam_high/file_index": [0],
        "videos/observation.images.cam_high/from_timestamp": [12.0],
        "videos/observation.images.cam_high/to_timestamp": [12.1],
    }), folder / "meta" / "episodes" / "chunk-000" / "file-000.parquet")
    vectors = [[float(index)] * 16 for index in range(3)]
    pq.write_table(pa.table({
        "episode_index": [0, 0, 0], "frame_index": [0, 1, 2],
        "timestamp": [0.0, 1 / 30, 2 / 30],
        "observation.state": vectors, "action": vectors,
    }), folder / "data" / "chunk-000" / "file-000.parquet")
    record = {"id": "DS000001", "dataset_family": "ego", "manifest_sha256": None,
              "storage_area": "data", "storage_relpath": "DS000001"}

    assert ego_detail.overview(tmp_path, record, info, 1) == {
        "task_count": 1, "duration_seconds": 0.1, "camera_count": 1,
    }
    page = ego_detail.episodes(tmp_path, record, 1)
    assert page["items"][0]["task"] == "Pick up a cup"
    assert page["items"][0]["video"]["url"].endswith("/episodes/0/preview")
    assert ego_detail.episode_video(tmp_path, record, 0) == (video, 12.0, pytest.approx(0.1))
    series = ego_detail.vector_series(tmp_path, record, 0, "observation.state")
    assert series["width"] == 16
    assert series["total_points"] == 3
    assert series["state_action_equal"] is True
    assert series["points"][2]["values"] == [2.0] * 16
    with pytest.raises(ValueError, match="不存在"):
        ego_detail.vector_series(tmp_path, record, 9, "action")
