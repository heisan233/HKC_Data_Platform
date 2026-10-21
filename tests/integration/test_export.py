"""End-to-end conversion of a small, shared-file LeRobot v3 dataset."""

from __future__ import annotations

import json
import shutil
import tarfile
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import av
import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.modules.export import router as export_router


def make_source(root: Path, name: str = "edited-v3") -> Path:
    folder = root / "derived" / name
    (folder / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (folder / "data" / "chunk-000").mkdir(parents=True)
    video_dir = folder / "videos" / "observation.images.front" / "chunk-000"
    video_dir.mkdir(parents=True)
    info = {
        "codebase_version": "v3.0", "fps": 5, "total_episodes": 2, "total_frames": 4,
        "robot_type": "test_robot", "features": {
            "action": {"dtype": "float32", "shape": [2]},
            "observation.state": {"dtype": "float32", "shape": [2]},
            "observation.images.front": {"dtype": "video", "shape": [3, 32, 32]},
            "timestamp": {"dtype": "float32", "shape": [1]},
        },
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
    }
    (folder / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    pq.write_table(pa.table({
        "episode_index": [0, 0, 1, 1], "index": [0, 1, 2, 3],
        "task_index": [0, 0, 1, 1], "timestamp": [0.0, 0.2, 0.0, 0.2],
        "action": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]],
        "observation.state": [[10.0, 20.0], [30.0, 40.0], [50.0, 60.0], [70.0, 80.0]],
    }), folder / "data" / "chunk-000" / "file-000.parquet")
    pq.write_table(pa.table({
        "episode_index": [0, 1], "length": [2, 2], "tasks": [["拿起"], ["放下"]],
        "dataset_from_index": [0, 2], "dataset_to_index": [2, 4],
        "data/chunk_index": [0, 0], "data/file_index": [0, 0],
        "videos/observation.images.front/chunk_index": [0, 0],
        "videos/observation.images.front/file_index": [0, 0],
        "videos/observation.images.front/from_timestamp": [0.0, 0.4],
        "videos/observation.images.front/to_timestamp": [0.4, 0.8],
    }), folder / "meta" / "episodes" / "chunk-000" / "file-000.parquet")
    pq.write_table(pa.table({"task": ["拿起", "放下"], "task_index": [0, 1]}),
                   folder / "meta" / "tasks.parquet")
    with av.open(str(video_dir / "file-000.mp4"), mode="w") as video:
        stream = video.add_stream("mpeg4", rate=5)
        stream.width = 32
        stream.height = 32
        stream.pix_fmt = "yuv420p"
        for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)):
            pixels = np.zeros((32, 32, 3), dtype=np.uint8)
            pixels[:] = color
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                video.mux(packet)
        for packet in stream.encode():
            video.mux(packet)
    return folder


def wait_for_task(client: TestClient, task_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        task = client.get(f"/export/api/conversions/{task_id}").json()
        if task["status"] in ("COMPLETED", "FAILED"):
            return task
        time.sleep(0.05)
    raise AssertionError("conversion task did not finish")


def test_convert_and_export_both_areas(tmp_path: Path, monkeypatch) -> None:
    source = make_source(tmp_path)
    monkeypatch.setattr(export_router, "settings", SimpleNamespace(data_root=tmp_path))
    with TestClient(app) as client:
        page = client.get("/export/")
        assert page.status_code == 200
        assert "edited-v3" in page.text
        created = client.post("/export/api/conversions", json={"source_name": source.name})
        assert created.status_code == 202
        task = wait_for_task(client, created.json()["id"])
        assert task["status"] == "COMPLETED", task["error"]
        converted = tmp_path / "converted" / task["id"]
        with h5py.File(converted / "chunk_001.hdf5") as output:
            first = output["data/episode_001"]
            second = output["data/episode_002"]
            assert first["action"].shape == (2, 2)
            assert second["action"][:].tolist() == [[5, 6], [7, 8]]
            assert first.attrs["task"] == "拿起"
            assert second.attrs["task"] == "放下"
            assert first.attrs["source_episode_index"] == 0
            assert bytes(first["observation.images.front"][0]).startswith(b"\xff\xd8")
            assert bytes(second["observation.images.front"][1]).startswith(b"\xff\xd8")
            first_rgb = np.asarray(Image.open(BytesIO(bytes(first["observation.images.front"][0]))))
            last_rgb = np.asarray(Image.open(BytesIO(bytes(second["observation.images.front"][1]))))
            assert first_rgb[0, 0, 0] > first_rgb[0, 0, 1] + 100
            assert last_rgb[0, 0, 0] > 150 and last_rgb[0, 0, 1] > 150
        listed = client.get("/export/api/datasets").json()
        assert [item["name"] for item in listed["derived"]] == [source.name]
        assert [item["name"] for item in listed["converted"]] == [task["id"]]
        for area, name, expected in (
            ("derived", source.name, f"{source.name}/meta/info.json"),
            ("converted", task["id"], f"{task['id']}/chunk_001.hdf5"),
        ):
            response = client.get(f"/export/archive/{area}/{name}")
            assert response.status_code == 200
            with tarfile.open(fileobj=BytesIO(response.content), mode="r:gz") as archive:
                assert expected in archive.getnames()
        assert not (tmp_path / "exports").exists()


def test_invalid_source_and_symlink_are_rejected(tmp_path: Path, monkeypatch) -> None:
    source = make_source(tmp_path)
    monkeypatch.setattr(export_router, "settings", SimpleNamespace(data_root=tmp_path))
    with TestClient(app) as client:
        assert client.post("/export/api/conversions", json={"source_name": "../raw"}).status_code == 400
        (source / "leak").symlink_to(tmp_path)
        assert client.post("/export/api/conversions", json={"source_name": source.name}).status_code == 400
        assert client.get(f"/export/archive/derived/{source.name}").status_code == 409


def test_global_parquet_indices_and_inclusive_episode_end(tmp_path: Path, monkeypatch) -> None:
    source = make_source(tmp_path)
    metadata_path = source / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    rows = pq.read_table(metadata_path).to_pylist()
    for row in rows:
        row["num_frames"] = row.pop("length")
        row["dataset_to_index"] -= 1
        row["task"] = row.pop("tasks")[0]
        del row["data/chunk_index"]
        del row["data/file_index"]
    pq.write_table(pa.Table.from_pylist(rows), metadata_path)
    data_path = source / "data" / "chunk-000" / "file-000.parquet"
    frames = pq.read_table(data_path)
    pq.write_table(frames.slice(0, 3), data_path)
    pq.write_table(frames.slice(3), data_path.with_name("file-001.parquet"))
    monkeypatch.setattr(export_router, "settings", SimpleNamespace(data_root=tmp_path))
    with TestClient(app) as client:
        created = client.post("/export/api/conversions", json={"source_name": source.name})
        assert created.status_code == 202
        task = wait_for_task(client, created.json()["id"])
        assert task["status"] == "COMPLETED", task["error"]
        with h5py.File(tmp_path / "converted" / task["id"] / "chunk_001.hdf5") as output:
            assert output["data/episode_001/action"].shape == (2, 2)
            assert output["data/episode_002"].attrs["task"] == "放下"


def test_image_backed_v3_and_failed_video_leave_no_partial_output(
    tmp_path: Path, monkeypatch
) -> None:
    source = make_source(tmp_path)
    info_path = source / "meta" / "info.json"
    info = json.loads(info_path.read_text())
    info["features"]["observation.images.front"]["dtype"] = "image"
    info_path.write_text(json.dumps(info), encoding="utf-8")
    data_path = source / "data" / "chunk-000" / "file-000.parquet"
    table = pq.read_table(data_path)
    pixels = Image.new("RGB", (32, 32), (40, 80, 120))
    buffer = BytesIO()
    pixels.save(buffer, format="PNG")
    pictures = pa.array([{"bytes": buffer.getvalue(), "path": None}] * 4,
                        type=pa.struct([("bytes", pa.binary()), ("path", pa.string())]))
    table = table.append_column("observation.images.front", pictures)
    pq.write_table(table, data_path)
    shutil.rmtree(source / "videos")
    monkeypatch.setattr(export_router, "settings", SimpleNamespace(data_root=tmp_path))
    with TestClient(app) as client:
        created = client.post("/export/api/conversions", json={"source_name": source.name})
        task = wait_for_task(client, created.json()["id"])
        assert task["status"] == "COMPLETED", task["error"]
        with h5py.File(tmp_path / "converted" / task["id"] / "chunk_001.hdf5") as output:
            assert bytes(output["data/episode_002/observation.images.front"][0]).startswith(b"\xff\xd8")

    broken = make_source(tmp_path, "broken-v3")
    (broken / "videos" / "observation.images.front" / "chunk-000" / "file-000.mp4").unlink()
    with TestClient(app) as client:
        created = client.post("/export/api/conversions", json={"source_name": broken.name})
        task = wait_for_task(client, created.json()["id"])
        assert task["status"] == "FAILED"
        assert not (tmp_path / "converted" / task["id"]).exists()
