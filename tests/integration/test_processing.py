import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.db.datasets import DatasetRepository
from app.modules.datasets.service import register_dataset
from app.modules.processing.schemas import ProcessingJobRequest
from app.modules.processing.service import create_job, get_job, run_job, scan_duplicates


def make_processing_dataset(
    data_root: Path,
    dataset_id: str,
    episode_values: list[list[float]],
    *,
    legacy_episode_metadata: bool = False,
) -> Path:
    dataset = data_root / "raw" / dataset_id
    (dataset / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (dataset / "data" / "chunk-000").mkdir(parents=True)

    total_frames = sum(len(values) for values in episode_values)
    info = {
        "codebase_version": "v3.0",
        "robot_type": "test_robot",
        "fps": 10,
        "total_episodes": len(episode_values),
        "total_frames": total_frames,
        "total_tasks": 1,
        "splits": {"train": f"0:{len(episode_values)}"},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {
            "value": {"dtype": "float32", "shape": [1], "names": None},
            "timestamp": {"dtype": "float32", "shape": [1], "names": None},
            "frame_index": {"dtype": "int64", "shape": [1], "names": None},
            "episode_index": {"dtype": "int64", "shape": [1], "names": None},
            "index": {"dtype": "int64", "shape": [1], "names": None},
            "task_index": {"dtype": "int64", "shape": [1], "names": None},
        },
    }
    (dataset / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    (dataset / "meta" / "stats.json").write_text("{}", encoding="utf-8")
    pd.DataFrame(
        {"task_index": [0]},
        index=pd.Index(["test task"], name="task"),
    ).to_parquet(dataset / "meta" / "tasks.parquet")

    episode_rows = []
    frame_rows = []
    global_index = 0
    for episode_index, values in enumerate(episode_values):
        start = global_index
        for frame_index, value in enumerate(values):
            frame_rows.append(
                {
                    "value": value,
                    "timestamp": frame_index / 10,
                    "frame_index": frame_index,
                    "episode_index": episode_index,
                    "index": global_index,
                    "task_index": 0,
                }
            )
            global_index += 1
        episode_row = {
            "episode_index": episode_index,
            "dataset_from_index": start,
            "dataset_to_index": global_index,
        }
        if legacy_episode_metadata:
            episode_row.update({"task": "test task", "num_frames": len(values)})
        else:
            episode_row.update(
                {
                    "data/chunk_index": 0,
                    "data/file_index": 0,
                    "tasks": ["test task"],
                    "length": len(values),
                    "meta/episodes/chunk_index": 0,
                    "meta/episodes/file_index": 0,
                }
            )
        episode_rows.append(episode_row)

    pq.write_table(
        pa.Table.from_pylist(episode_rows),
        dataset / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(frame_rows),
        dataset / "data" / "chunk-000" / "file-000.parquet",
    )
    register_dataset(data_root, "raw", dataset_id, dataset_id)
    return dataset


def run_processing(data_root: Path, request: ProcessingJobRequest) -> dict:
    created = create_job(data_root, request)
    run_job(data_root, created["job_id"])
    job = get_job(data_root, created["job_id"])
    assert job["status"] == "completed", job["message"]
    return job


def output_path(data_root: Path, job: dict, position: int = 0) -> Path:
    return data_root / job["outputs"][position]["relative_path"]


def test_filter_creates_derived_dataset_without_changing_source(tmp_path: Path) -> None:
    source = make_processing_dataset(tmp_path, "source", [[1, 2], [3, 4], [5, 6]])
    job = run_processing(
        tmp_path,
        ProcessingJobRequest(
            operation="filter",
            source_dataset_ids=["DS000001"],
            version_name="筛除中间 Episode",
            episode_indices=[1],
        ),
    )

    derived = output_path(tmp_path, job)
    output = job["outputs"][0]
    registered = DatasetRepository(tmp_path).get(output["dataset_id"])
    info = json.loads((derived / "meta" / "info.json").read_text())
    frames = pq.read_table(derived / "data" / "chunk-000" / "file-000.parquet").to_pylist()
    source_info = json.loads((source / "meta" / "info.json").read_text())

    assert info["total_episodes"] == 2
    assert info["total_frames"] == 4
    assert output["dataset_id"] == "DS000002"
    assert registered["storage_area"] == "derived"
    assert registered["storage_relpath"] == output["storage_name"]
    assert registered["source_dataset_ids"] == ["DS000001"]
    assert [row["episode_index"] for row in frames] == [0, 0, 1, 1]
    assert [row["value"] for row in frames] == [1, 2, 5, 6]
    assert source_info["total_episodes"] == 3


def test_split_by_ratio_creates_multiple_derived_datasets(tmp_path: Path) -> None:
    make_processing_dataset(tmp_path, "source", [[1], [2], [3], [4]])
    job = run_processing(
        tmp_path,
        ProcessingJobRequest(
            operation="split",
            source_dataset_ids=["DS000001"],
            version_name="训练验证拆分",
            splits={"train": 0.75, "val": 0.25},
        ),
    )

    assert len(job["outputs"]) == 2
    assert [item["label"] for item in job["outputs"]] == ["train", "val"]
    assert [item["total_episodes"] for item in job["outputs"]] == [3, 1]


def test_merge_and_reorder_compatible_datasets(tmp_path: Path) -> None:
    make_processing_dataset(tmp_path, "first", [[1], [2]])
    make_processing_dataset(tmp_path, "second", [[3]])
    merged_job = run_processing(
        tmp_path,
        ProcessingJobRequest(
            operation="merge",
            source_dataset_ids=["DS000001", "DS000002"],
            version_name="合并版本",
        ),
    )
    assert merged_job["outputs"][0]["total_episodes"] == 3

    reordered_job = run_processing(
        tmp_path,
        ProcessingJobRequest(
            operation="reorder",
            source_dataset_ids=["DS000001"],
            version_name="反向重排",
            episode_indices=[1, 0],
        ),
    )
    frames = pq.read_table(
        output_path(tmp_path, reordered_job) / "data" / "chunk-000" / "file-000.parquet"
    ).to_pylist()
    assert [row["value"] for row in frames] == [2, 1]
    assert [row["episode_index"] for row in frames] == [0, 1]


def test_duplicate_scan_and_deduplicate_keep_first_episode(tmp_path: Path) -> None:
    make_processing_dataset(tmp_path, "source", [[1, 2], [3, 4], [1, 2]])
    report = scan_duplicates(tmp_path, "DS000001")
    assert report["duplicate_episode_count"] == 1
    assert report["duplicate_groups"][0]["episodes"] == [0, 2]

    job = run_processing(
        tmp_path,
        ProcessingJobRequest(
            operation="deduplicate",
            source_dataset_ids=["DS000001"],
            version_name="精确去重版本",
        ),
    )
    frames = pq.read_table(
        output_path(tmp_path, job) / "data" / "chunk-000" / "file-000.parquet"
    ).to_pylist()
    assert job["outputs"][0]["total_episodes"] == 2
    assert [row["value"] for row in frames] == [1, 2, 3, 4]


def test_legacy_episode_metadata_is_discovered_and_normalized(tmp_path: Path) -> None:
    source = make_processing_dataset(
        tmp_path,
        "legacy-source",
        [[1, 2], [3, 4], [5, 6]],
        legacy_episode_metadata=True,
    )
    source_episode_path = source / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    source_columns = pq.read_schema(source_episode_path).names
    assert "data/chunk_index" not in source_columns
    assert "data/file_index" not in source_columns

    job = run_processing(
        tmp_path,
        ProcessingJobRequest(
            operation="filter",
            source_dataset_ids=["DS000001"],
            version_name="旧格式兼容版本",
            episode_indices=[1],
        ),
    )

    derived_episode_path = (
        output_path(tmp_path, job) / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    )
    derived = pq.read_table(derived_episode_path).to_pylist()
    assert [row["episode_index"] for row in derived] == [0, 1]
    assert [row["data/chunk_index"] for row in derived] == [0, 0]
    assert [row["data/file_index"] for row in derived] == [0, 0]
    assert [row["length"] for row in derived] == [2, 2]
    assert [row["tasks"] for row in derived] == [["test task"], ["test task"]]
    assert "data/chunk_index" not in pq.read_schema(source_episode_path).names
