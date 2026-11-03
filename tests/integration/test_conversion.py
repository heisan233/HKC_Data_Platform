"""Synthetic LeRobot v3 conversion through the registered DS API."""

from __future__ import annotations

import json
import tarfile
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import av
import h5py
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from PIL import Image

from app.db.datasets import DatasetRepository
from app.main import app
from app.modules.conversion import converter, service
from app.modules.datasets import router as datasets_router
from app.modules.datasets.service import register_dataset
from app.modules.export import router as export_router


def _source(root: Path, *, image: bool = False, legacy: bool = False) -> tuple[str, Path]:
    folder = root / "derived" / "test-version"
    (folder / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (folder / "data" / "chunk-000").mkdir(parents=True)
    info = {
        "codebase_version": "v3.0", "fps": 5, "total_episodes": 2,
        "total_frames": 4, "robot_type": "test_robot",
        "features": {
            "action": {"dtype": "float32", "shape": [2]},
            "observation.state": {"dtype": "float32", "shape": [2]},
            "observation.images.front": {"dtype": "image" if image else "video", "shape": [3, 16, 16]},
            "timestamp": {"dtype": "float32", "shape": [1]},
        },
    }
    (folder / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    (folder / "meta" / "annotations").mkdir()
    (folder / "meta" / "annotations" / "schema.json").write_text("{}", encoding="utf-8")
    frames = pa.table({
        "episode_index": [0, 0, 1, 1], "timestamp": [0.0, 0.2, 0.0, 0.2],
        "task_index": [0, 0, 1, 1],
        "action": [[1., 2.], [3., 4.], [5., 6.], [7., 8.]],
        "observation.state": [[10., 20.], [30., 40.], [50., 60.], [70., 80.]],
    })
    if image:
        buffer = BytesIO()
        Image.new("RGB", (16, 16), (220, 30, 20)).save(buffer, format="PNG")
        pictures = pa.array(
            [{"bytes": buffer.getvalue(), "path": None}] * 4,
            type=pa.struct([("bytes", pa.binary()), ("path", pa.string())]),
        )
        frames = frames.append_column("observation.images.front", pictures)
    data_dir = folder / "data" / "chunk-000"
    if legacy:
        pq.write_table(frames.slice(0, 3), data_dir / "file-000.parquet")
        pq.write_table(frames.slice(3), data_dir / "file-001.parquet")
    else:
        pq.write_table(frames, data_dir / "file-000.parquet")
    rows = []
    for index in range(2):
        row = {
            "episode_index": index, "dataset_from_index": index * 2,
            "dataset_to_index": index * 2 + (1 if legacy else 2),
            "num_frames" if legacy else "length": 2,
            "task_index": index,
        }
        if not legacy:
            row.update({"data/chunk_index": 0, "data/file_index": 0})
        if not image:
            row.update({
                "videos/observation.images.front/chunk_index": 0,
                "videos/observation.images.front/file_index": 0,
                "videos/observation.images.front/from_timestamp": index * 0.4,
            })
        rows.append(row)
    pq.write_table(
        pa.Table.from_pylist(rows),
        folder / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    pd.DataFrame({"task_index": [0, 1]}, index=pd.Index(["拿起", "放下"], name="task")).to_parquet(
        folder / "meta" / "tasks.parquet"
    )
    if not image:
        video_dir = folder / "videos" / "observation.images.front" / "chunk-000"
        video_dir.mkdir(parents=True)
        with av.open(str(video_dir / "file-000.mp4"), mode="w") as video:
            stream = video.add_stream("mpeg4", rate=5)
            stream.width = stream.height = 16
            stream.pix_fmt = "yuv420p"
            for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)):
                pixels = np.zeros((16, 16, 3), dtype=np.uint8)
                pixels[:] = color
                for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="rgb24")):
                    video.mux(packet)
            for packet in stream.encode():
                video.mux(packet)
    record = register_dataset(root, "derived", folder.name, "测试派生版")
    return record["id"], folder


def _wait(client: TestClient, task_id: str) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        task = client.get(f"/export/api/conversions/{task_id}").json()
        if task["status"] in {"COMPLETED", "FAILED"}:
            return task
        time.sleep(0.05)
    raise AssertionError("conversion task timed out")


def test_registered_video_source_converts_and_downloads(tmp_path: Path, monkeypatch) -> None:
    dataset_id, source = _source(tmp_path)
    monkeypatch.setattr(export_router, "settings", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(datasets_router, "settings", SimpleNamespace(data_root=tmp_path))
    before = service._fingerprint(source)
    with TestClient(app) as client:
        assert dataset_id in client.get("/export/").text
        created = client.post("/export/api/conversions", json={"source_dataset_id": dataset_id})
        assert created.status_code == 202
        task = _wait(client, created.json()["id"])
        assert task["status"] == "COMPLETED", task["error"]
        assert task["files"] == ["chunk_001.hdf5"]
        assert "result_dataset_id" not in task
        assert DatasetRepository(tmp_path).list() == [DatasetRepository(tmp_path).get(dataset_id)]
        assert task["id"] not in client.get("/data/manage/?view=registered").text
        data_page = client.get("/data/")
        assert data_page.status_code == 200
        assert "格式转换结果" in data_page.text and task["id"] in data_page.text
        assert client.get(f"/data/converted/{task['id']}").status_code == 404
        assert "导出数据集" in client.get("/export/").text
        exportables = client.get("/export/api/exportable-datasets").json()
        assert {item["dataset_id"] for item in exportables} == {dataset_id, task["id"]}
        derived_archive = client.get(f"/export/archive/{dataset_id}")
        assert derived_archive.status_code == 200
        with tarfile.open(fileobj=BytesIO(derived_archive.content), mode="r:gz") as archive:
            assert f"{dataset_id}/meta/info.json" in archive.getnames()
        converted_archive = client.get(f"/export/archive/{task['id']}")
        assert converted_archive.status_code == 200
        with tarfile.open(fileobj=BytesIO(converted_archive.content), mode="r:gz") as archive:
            assert sorted(archive.getnames()) == [
                f"{task['id']}/chunk_001.hdf5",
                f"{task['id']}/manifest.json",
            ]
        assert len(list((tmp_path / "exports").glob("*.tar.gz"))) == 2
        assert client.patch(f"/data/api/datasets/{task['id']}", json={
            "name": "HDF5 展示名", "description": "转换结果", "tags": ["export"]
        }).status_code == 404
        output = tmp_path / "converted" / task["id"]
        with h5py.File(output / "chunk_001.hdf5") as result:
            assert result["data/episode_002/action"][:].tolist() == [[5, 6], [7, 8]]
            assert result["data/episode_002"].attrs["task"] == "放下"
            assert bytes(result["data/episode_001/observation.images.front"][0]).startswith(b"\xff\xd8")
            assert bytes(result["meta/source_meta_files/annotations/schema.json"][:]) == b"{}"
        assert client.get(f"/export/conversions/{task['id']}/files/chunk_001.hdf5").status_code == 200
        assert client.get(f"/export/conversions/{task['id']}/files/manifest.json").json()[
            "source_dataset_id"
        ] == dataset_id
        assert client.get(f"/export/conversions/{task['id']}/files/unknown.hdf5").status_code == 404
    assert service._fingerprint(source) == before


def test_legacy_index_and_embedded_images(tmp_path: Path, monkeypatch) -> None:
    dataset_id, _ = _source(tmp_path, image=True, legacy=True)
    monkeypatch.setattr(export_router, "settings", SimpleNamespace(data_root=tmp_path))
    with TestClient(app) as client:
        created = client.post("/export/api/conversions", json={"source_dataset_id": dataset_id})
        task = _wait(client, created.json()["id"])
        assert task["status"] == "COMPLETED", task["error"]
        with h5py.File(tmp_path / "converted" / task["id"] / "chunk_001.hdf5") as result:
            assert result["data/episode_002/action"][:].tolist() == [[5, 6], [7, 8]]
            assert bytes(result["data/episode_002/observation.images.front"][0]).startswith(b"\xff\xd8")


def test_multiple_chunks_are_listed_and_downloadable(tmp_path: Path, monkeypatch) -> None:
    dataset_id, _ = _source(tmp_path, image=True)
    monkeypatch.setattr(export_router, "settings", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(converter, "EPISODES_PER_FILE", 1)
    with TestClient(app) as client:
        created = client.post("/export/api/conversions", json={"source_dataset_id": dataset_id})
        task = _wait(client, created.json()["id"])
        assert task["status"] == "COMPLETED", task["error"]
        assert task["files"] == ["chunk_001.hdf5", "chunk_002.hdf5"]
        assert task["id"] in client.get("/export/").text
        assert client.get(f"/export/conversions/{task['id']}/files/chunk_002.hdf5").status_code == 200


def test_rejects_unregistered_and_symlink_sources(tmp_path: Path, monkeypatch) -> None:
    dataset_id, source = _source(tmp_path, image=True)
    monkeypatch.setattr(export_router, "settings", SimpleNamespace(data_root=tmp_path))
    with TestClient(app) as client:
        assert client.post("/export/api/conversions", json={"source_dataset_id": "../raw"}).status_code == 400
        assert client.post("/export/api/conversions", json={"source_dataset_id": "DS999999"}).status_code == 400
        assert client.post("/export/api/conversions", json={
            "source_dataset_id": dataset_id, "profile_id": "unsupported"
        }).status_code == 400
        (source / "leak").symlink_to(tmp_path)
        assert client.post("/export/api/conversions", json={"source_dataset_id": dataset_id}).status_code == 400
        assert client.get(f"/export/archive/{dataset_id}").status_code == 400


def test_broken_video_fails_without_publishing_result(tmp_path: Path, monkeypatch) -> None:
    dataset_id, source = _source(tmp_path)
    (source / "videos" / "observation.images.front" / "chunk-000" / "file-000.mp4").unlink()
    monkeypatch.setattr(export_router, "settings", SimpleNamespace(data_root=tmp_path))
    with TestClient(app) as client:
        created = client.post("/export/api/conversions", json={"source_dataset_id": dataset_id})
        assert created.status_code == 202
        task = _wait(client, created.json()["id"])
        assert task["status"] == "FAILED"
        assert "result_dataset_id" not in task
        assert service.list_results(tmp_path) == []
        assert not (tmp_path / "converted" / task["id"]).exists()
        assert not (tmp_path / "staging" / "conversion" / task["id"]).exists()


def test_completed_legacy_json_job_is_shown_without_registration(tmp_path: Path) -> None:
    folder = tmp_path / "system" / "conversion-jobs"
    folder.mkdir(parents=True)
    legacy = folder / "CV-ABCDEF123456.json"
    result_folder = tmp_path / "converted" / "CV-ABCDEF123456"
    result_folder.mkdir(parents=True)
    with h5py.File(result_folder / "chunk_001.hdf5", "w") as output:
        output.create_group("data")
        output.create_group("meta")
    (result_folder / "manifest.json").write_text(json.dumps({
        "format": "HDF5", "total_episodes": 2, "total_frames": 4,
        "files": ["chunk_001.hdf5"],
    }), encoding="utf-8")
    legacy.write_text(json.dumps({
        "id": "CV-ABCDEF123456", "source_dataset_id": "DS000001",
        "source_name": "旧派生版", "source_fingerprint": "legacy", "status": "COMPLETED",
        "completed_episodes": 2, "total_episodes": 2, "files": ["chunk_001.hdf5"],
        "error": None, "created_at": "2026-10-01T00:00:00+00:00",
        "started_at": None, "finished_at": "2026-10-01T00:01:00+00:00",
    }, ensure_ascii=False), encoding="utf-8")
    assert len(service.list_tasks(tmp_path)) == 1
    task = service.get_task(tmp_path, "CV-ABCDEF123456")
    assert task["id"] == "CV-ABCDEF123456"
    assert len(service.list_tasks(tmp_path)) == 1
    assert len(service.list_results(tmp_path)) == 1
    assert DatasetRepository(tmp_path).list() == []
    assert legacy.is_file()
