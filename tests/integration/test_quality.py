"""Quality workflow contracts across upload, versioned plans, execution, and reports."""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.modules.datasets import router as datasets_router
from app.modules.ingestion import router as ingestion_router
from app.modules.quality import router as quality_router
from app.modules.quality import service, vlm


def _dataset(folder: Path) -> None:
    (folder / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (folder / "data" / "chunk-000").mkdir(parents=True)
    info = {
        "codebase_version": "v3.0", "robot_type": "testbot", "fps": 20,
        "total_episodes": 1, "total_frames": 3,
        "features": {
            "observation.state": {"dtype": "float32", "shape": [2]},
            "action": {"dtype": "float32", "shape": [2]},
        },
    }
    (folder / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    (folder / "meta" / "stats.json").write_text("{}", encoding="utf-8")
    pq.write_table(pa.table({"task_index": [0], "task": ["grasp"]}),
                   folder / "meta" / "tasks.parquet")
    pq.write_table(pa.table({"episode_index": [0], "num_frames": [3], "task": ["grasp"]}),
                   folder / "meta" / "episodes" / "chunk-000" / "file-000.parquet")
    pq.write_table(pa.table({
        "episode_index": [0, 0, 0], "timestamp": [0.0, 0.05, 0.1],
        "observation.state": [[0.0, 0.0], [0.2, 0.0], [0.4, 0.0]],
        "action": [[0.1, 0.0], [0.2, 0.0], [0.3, 0.0]],
    }), folder / "data" / "chunk-000" / "file-000.parquet")


@pytest.fixture
def platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(data_root=tmp_path)
    monkeypatch.setattr(datasets_router, "settings", settings)
    monkeypatch.setattr(quality_router, "settings", settings)
    monkeypatch.setattr(ingestion_router, "settings", settings)
    (tmp_path / "raw").mkdir()
    return TestClient(app), tmp_path


def test_batch_upload_keeps_raw_and_rejects_unsafe_archive(platform):
    client, root = platform
    source = root / "source"
    _dataset(source)
    good = io.BytesIO()
    with zipfile.ZipFile(good, "w") as archive:
        for file in source.rglob("*"):
            if file.is_file():
                archive.write(file, (Path("sample") / file.relative_to(source)).as_posix())
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../escape.txt", "no")
    response = client.post("/ingestion/api/uploads/batch", files=[
        ("files", ("valid.zip", good.getvalue(), "application/zip")),
        ("files", ("unsafe.zip", bad.getvalue(), "application/zip")),
    ])
    assert response.status_code == 200
    body = response.json()
    assert len(body["uploaded"]) == len(body["errors"]) == 1
    assert body["uploaded"][0]["dataset_id"] == "DS000001"
    assert (root / "raw" / body["uploaded"][0]["folder"] / "meta" / "info.json").exists()
    assert not (root / "escape.txt").exists()
    assert client.get("/qc/").status_code == 200


def test_one_plan_batch_tasks_and_plan_deletion_keep_reports(platform, monkeypatch):
    client, root = platform
    _dataset(root / "raw" / "first")
    _dataset(root / "raw" / "second")
    assert client.post("/data/api/register", json={
        "folders": ["first", "second"]
    }).status_code == 200
    plan = client.post("/qc/api/plans/from-dataset/DS000001").json()
    monkeypatch.setattr(service, "_doctor", lambda *_: {
        "status": "PASS", "version": "0.2.0", "results": [], "summary": {}
    })

    class DirectExecutor:
        @staticmethod
        def submit(fn, *args):
            fn(*args)

    monkeypatch.setattr(service, "_executor", DirectExecutor())
    payload = {"dataset_ids": ["DS000001", "DS000002"], "plan_id": plan["id"],
               "plan_version": 1, "selections": {
                   "doctor": ["metadata"], "robot_rules": [], "vlm": []}}
    response = client.post("/qc/api/tasks/batch", json=payload)
    assert response.status_code == 200
    assert len(response.json()["created"]) == 2
    assert response.json()["errors"] == []
    ids = [row["id"] for row in response.json()["created"]]
    assert len(set(ids)) == 2
    for task_id in ids:
        result = client.get(f"/qc/api/tasks/{task_id}").json()
        assert result["task"]["status"] == "COMPLETED"
        assert result["report"]["plan_id"] == plan["id"]
    assert client.post("/qc/api/tasks/batch", json={**payload,
        "dataset_ids": ["DS000001", "DS000001"]}).status_code == 400
    assert client.delete(f"/qc/api/plans/{plan['id']}").status_code == 200
    assert client.get("/qc/api/plans").json() == []
    assert client.get(f"/qc/reports/{ids[0]}").status_code == 200
    assert client.post("/qc/api/tasks/batch", json=payload).json()["errors"] == [
        {"dataset_id": item, "message": "质检方案已删除，请选择其他方案"}
        for item in payload["dataset_ids"]]


def test_hiding_task_preserves_report_reviews_and_raw(platform, monkeypatch):
    client, root = platform
    _dataset(root / "raw" / "sample")
    assert client.post("/data/api/register", json={"folders": ["sample"]}).status_code == 200
    plan = client.post("/qc/api/plans/from-dataset/DS000001").json()
    config = plan["config"]
    config["robot_rules"][0]["name"] = "自定义机器人字段检查"
    updated = client.put(f"/qc/api/plans/{plan['id']}", json={
        "name": plan["name"], "robot_type": "testbot",
        "expected_version": 1, "config": config,
    }).json()
    assert updated["config"]["robot_rules"][0]["name"] == "自定义机器人字段检查"
    monkeypatch.setattr(service, "_doctor", lambda *_: {
        "status": "PASS", "version": "0.2.0", "results": [], "summary": {}
    })

    class DirectExecutor:
        @staticmethod
        def submit(fn, *args):
            fn(*args)

    monkeypatch.setattr(service, "_executor", DirectExecutor())
    task = client.post("/qc/api/tasks", json={
        "dataset_id": "DS000001", "plan_id": plan["id"], "plan_version": 2,
        "selections": {"doctor": ["metadata"], "robot_rules": [config["robot_rules"][0]["id"]],
                       "vlm": []},
    }).json()
    task_id = task["id"]
    report = client.get(f"/qc/reports/{task_id}")
    assert report.status_code == 200
    assert "自定义机器人字段检查" in report.text
    assert "规则快照" not in report.text
    assert "归档任务记录" not in report.text
    assert client.post(f"/qc/api/tasks/{task_id}/review", json={
        "decision": "REVIEW", "reason": "人工检查"
    }).status_code == 200
    report_file = root / "reports" / "qc" / "DS000001" / f"{task_id}.json"
    assert report_file.is_file()
    assert client.post(f"/qc/api/tasks/{task_id}/hide").json() == {
        "id": task_id, "hidden": True
    }
    assert task_id not in [item["id"] for item in client.get("/qc/api/tasks").json()]
    assert task_id in [item["id"] for item in client.get("/qc/api/tasks?hidden=true").json()]
    assert task_id not in client.get("/qc/").text
    assert task_id in client.get("/qc/?hidden=1").text
    assert report_file.is_file()
    assert client.get(f"/qc/api/tasks/{task_id}").status_code == 200
    assert client.get(f"/qc/reports/{task_id}").status_code == 200
    assert service.initialize(root).reviews(task_id)[0]["reason"] == "人工检查"
    assert (root / "raw" / "sample" / "meta" / "info.json").is_file()
    assert client.post(f"/qc/api/tasks/{task_id}/show").json() == {
        "id": task_id, "hidden": False
    }
    assert task_id in [item["id"] for item in client.get("/qc/api/tasks").json()]
    assert task_id in client.get("/qc/").text
    assert service.initialize(root).reviews(task_id)[0]["reason"] == "人工检查"
    assert client.delete(f"/qc/api/tasks/{task_id}").status_code == 405
    assert client.post("/qc/api/tasks/missing/hide").status_code == 404

    class IdleExecutor:
        @staticmethod
        def submit(_fn, *_args):
            pass

    monkeypatch.setattr(service, "_executor", IdleExecutor())
    queued = client.post("/qc/api/tasks", json={
        "dataset_id": "DS000001", "plan_id": plan["id"], "plan_version": 2,
        "selections": {"doctor": ["metadata"], "robot_rules": [], "vlm": []},
    }).json()
    assert client.post(f"/qc/api/tasks/{queued['id']}/hide").status_code == 409
    assert client.get(f"/qc/api/tasks/{queued['id']}").json()["task"]["status"] == "QUEUED"
    assert queued["id"] in [item["id"] for item in client.get("/qc/api/tasks").json()]


def test_task_list_is_paginated_without_losing_older_records(platform):
    client, root = platform
    _dataset(root / "raw" / "sample")
    assert client.post("/data/api/register", json={"folders": ["sample"]}).status_code == 200
    plan = client.post("/qc/api/plans/from-dataset/DS000001").json()
    repo = service.initialize(root)
    for _ in range(21):
        repo.create_task("DS000001", plan, plan["config"], "test-fingerprint")
    first = client.get("/qc/").text
    second = client.get("/qc/?page=2").text
    assert first.count('data-task-id=') == 20
    assert second.count('data-task-id=') == 1
    assert "第 1/2 页" in first
    assert "第 2/2 页" in second
    assert 'href="/qc/?page=2#qc-tasks"' in first


def test_plan_snapshot_report_and_review(platform, monkeypatch: pytest.MonkeyPatch):
    client, root = platform
    _dataset(root / "raw" / "sample")
    assert client.post("/data/api/register", json={"folders": ["sample"]}).status_code == 200
    monkeypatch.setattr(service, "_doctor", lambda *_: {
        "status": "PASS", "version": "0.2.0", "results": [], "summary": {}
    })

    class DirectExecutor:
        @staticmethod
        def submit(fn, *args):
            fn(*args)

    monkeypatch.setattr(service, "_executor", DirectExecutor())
    response = client.post("/qc/api/plans/from-dataset/DS000001")
    assert response.status_code == 200
    plan = response.json()
    assert client.get(f"/qc/plans/{plan['id']}").status_code == 200
    config = plan["config"]
    config["robot_rules"] = [row for row in config["robot_rules"]
                             if row["kind"] == "feature_schema"]
    changed = client.put(f"/qc/api/plans/{plan['id']}", json={
        "name": "机器人标准检查", "robot_type": "testbot",
        "expected_version": 1, "config": config,
    })
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    assert client.put(f"/qc/api/plans/{plan['id']}", json={
        "name": "旧版本写入", "robot_type": "testbot",
        "expected_version": 1, "config": config,
    }).status_code == 409
    task = client.post("/qc/api/tasks", json={
        "dataset_id": "DS000001", "plan_id": plan["id"], "plan_version": 2,
        "selections": {"doctor": ["metadata"],
                       "robot_rules": ["feature_schema"], "vlm": []},
    })
    assert task.status_code == 200
    task_id = task.json()["id"]
    result = client.get(f"/qc/api/tasks/{task_id}").json()
    assert result["task"]["status"] == "COMPLETED"
    assert result["task"]["outcome"] == "PASS"
    assert result["task"]["plan_version"] == 2
    assert set(result["report"]["sections"]) == {"doctor", "robot_rules", "vlm"}
    assert result["report"]["sections"]["robot_rules"]["status"] == "PASS"
    assert result["report"]["sections"]["vlm"]["status"] == "SKIPPED"
    assert client.get(f"/qc/reports/{task_id}").status_code == 200
    assert client.get(f"/qc/reports/{task_id}/download").status_code == 200
    assert client.post(f"/qc/api/tasks/{task_id}/review", json={
        "decision": "RECAPTURE", "reason": ""
    }).status_code == 400
    assert client.post(f"/qc/api/tasks/{task_id}/review", json={
        "decision": "PASS", "reason": "人工核对"
    }).status_code == 200
    assert client.get(f"/qc/api/tasks/{task_id}").json()["report"]["reviews"][0]["reason"] == "人工核对"
    original = client.post("/qc/api/tasks", json={
        "dataset_id": "DS000001", "plan_id": plan["id"], "plan_version": 1,
        "selections": {"doctor": ["metadata"], "robot_rules": [], "vlm": []},
    })
    assert original.status_code == 200
    assert original.json()["plan_version"] == 1
    assert len(original.json()["snapshot"]["robot_rules"]) > len(config["robot_rules"])
    rerun = client.post(f"/qc/api/tasks/{task_id}/rerun")
    assert rerun.status_code == 200
    assert rerun.json()["snapshot"] == result["task"]["snapshot"]


def test_worker_import_failure_is_visible_in_task_and_report(platform, monkeypatch):
    client, root = platform
    _dataset(root / "raw" / "sample")
    assert client.post("/data/api/register", json={"folders": ["sample"]}).status_code == 200
    plan = client.post("/qc/api/plans/from-dataset/DS000001").json()

    class DirectExecutor:
        @staticmethod
        def submit(fn, *args):
            fn(*args)

    def fail_before_stage(*_):
        raise ModuleNotFoundError("No module named 'httpx'")

    monkeypatch.setattr(service, "_executor", DirectExecutor())
    monkeypatch.setattr(service, "run_task", fail_before_stage)
    response = client.post("/qc/api/tasks", json={
        "dataset_id": "DS000001", "plan_id": plan["id"], "plan_version": 1,
        "selections": {"doctor": ["metadata"], "robot_rules": [], "vlm": []},
    })
    assert response.status_code == 200
    result = client.get(f"/qc/api/tasks/{response.json()['id']}").json()
    assert result["task"]["status"] == "FAILED"
    assert "httpx" in result["task"]["error"]
    assert result["report"]["status"] == "FAILED"
    assert result["report"]["error"] == result["task"]["error"]


def test_queued_tasks_resume_on_initialize(platform, monkeypatch):
    client, root = platform
    _dataset(root / "raw" / "sample")
    assert client.post("/data/api/register", json={"folders": ["sample"]}).status_code == 200
    plan = client.post("/qc/api/plans/from-dataset/DS000001").json()

    class IdleExecutor:
        @staticmethod
        def submit(_fn, *_args):
            pass

    monkeypatch.setattr(service, "_executor", IdleExecutor())
    response = client.post("/qc/api/tasks", json={
        "dataset_id": "DS000001", "plan_id": plan["id"], "plan_version": 1,
        "selections": {"doctor": ["metadata"], "robot_rules": [], "vlm": []},
    })
    task_id = response.json()["id"]
    assert response.json()["status"] == "QUEUED"
    running = client.post("/qc/api/tasks", json={
        "dataset_id": "DS000001", "plan_id": plan["id"], "plan_version": 1,
        "selections": {"doctor": ["metadata"], "robot_rules": [], "vlm": []},
    }).json()["id"]
    service.initialize(root).set_task(running, status="RUNNING", phase="doctor")
    submitted = []

    class RecordingExecutor:
        @staticmethod
        def submit(_fn, *args):
            submitted.append(args[-1])

    monkeypatch.setattr(service, "_executor", RecordingExecutor())
    service._initialized_roots.remove(root)
    service.initialize(root)
    assert submitted == [task_id]
    assert service.initialize(root).get_task(task_id)["status"] == "QUEUED"
    assert service.initialize(root).get_task(running)["status"] == "INTERRUPTED"
    assert service.read_report(root, service.initialize(root).get_task(running))["status"] == "INTERRUPTED"


def test_doctor_json_cli_and_vlm_episode_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    folder = tmp_path / "raw" / "sample"
    _dataset(folder)
    from app.modules.quality.catalog import default_config
    config = default_config(json.loads((folder / "meta" / "info.json").read_text()))
    config["doctor"]["checks"] = [{"id": "metadata", "enabled": True}]
    config["doctor"]["max_episodes"] = 1
    payload = {"version": "0.2.0", "total_episodes": 2,
               "overall_severity": "WARN", "checks": [
        {"name": "metadata", "severity": "WARN", "messages": [
            {"severity": "WARN", "message": "test"}]}
    ], "summary": {"PASS": 0, "WARN": 1, "FAIL": 0}}
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    doctor = service._doctor(folder, config, 10)
    assert doctor["status"] == "WARN"
    assert doctor["sampled"] is True
    assert "--max-episodes" in commands[0]

    config["vlm"]["rules"][0]["enabled"] = True
    config["robot"]["video_keys"] = ["camera"]
    config["vlm"]["rules"][0]["camera_key"] = "camera"

    def clip(_folder, _info, _episode, camera, destination, _timeout):
        destination.write_bytes(b"video")
        return destination, {"camera": camera, "source_file": "videos/camera/file.mp4",
                             "from_timestamp": 0, "to_timestamp": 1}

    monkeypatch.setattr(vlm, "_video_segment", clip)
    monkeypatch.setattr(vlm, "_probe_endpoint", lambda *_: None)
    monkeypatch.setattr(vlm, "_ask_model", lambda *_: {
        "result": "uncertain", "reason": "画面遮挡", "evidence": "无法看见目标物"
    })
    result = vlm.run_vlm_rules(tmp_path, "test", folder, config, "http://example/v1", "")
    assert result["status"] == "REVIEW"
    assert result["results"][-1]["episodes"][0]["status"] == "UNCERTAIN"


def test_vlm_connection_failure_returns_immediately(tmp_path: Path, monkeypatch):
    from app.modules.quality.catalog import default_config

    folder = tmp_path / "raw" / "sample"
    _dataset(folder)
    config = default_config(json.loads((folder / "meta" / "info.json").read_text()))
    config["vlm"]["rules"][0]["enabled"] = True

    def unavailable(*_):
        raise TimeoutError("connection timed out")

    monkeypatch.setattr(vlm, "_probe_endpoint", unavailable)
    result = vlm.run_vlm_rules(tmp_path, "test", folder, config,
                               "http://192.168.123.105:8000/v1", "EMPTY")
    assert result["status"] == "ERROR"
    assert "connection timed out" in result["results"][-1]["reason"]
    assert result["results"][-1]["episodes"] == []


def test_installed_doctor_and_real_video_clip(tmp_path: Path):
    from app.modules.quality.catalog import default_config

    folder = tmp_path / "raw" / "sample"
    _dataset(folder)
    info = json.loads((folder / "meta" / "info.json").read_text())
    config = default_config(info)
    config["doctor"]["checks"] = [{"id": "metadata", "enabled": True}]
    doctor = service._doctor(folder, config, 60)
    assert doctor["status"] in {"PASS", "WARN", "FAIL"}, doctor.get("reason")
    assert doctor["version"] == "0.2.0"
    assert doctor["results"]

    source = folder / "videos" / "camera" / "chunk-000" / "file-000.mp4"
    source.parent.mkdir(parents=True)
    video = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=64x64:r=20:d=2", "-an", "-c:v", "libx264", str(source),
    ], capture_output=True, text=True, check=False)
    assert video.returncode == 0, video.stderr
    info["video_path"] = "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
    destination = tmp_path / "clip.mp4"
    clip, evidence = vlm._video_segment(folder, info, {
        "videos/camera/chunk_index": 0, "videos/camera/file_index": 0,
        "videos/camera/from_timestamp": 0.25,
        "videos/camera/to_timestamp": 0.75,
    }, "camera", destination, 30)
    assert clip.stat().st_size > 0
    assert evidence["from_timestamp"] == 0.25
    assert evidence["to_timestamp"] == 0.75
