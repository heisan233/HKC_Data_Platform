"""Training metadata, preflight, and executor-bound submission tests."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.quality import QualityRepository
from app.db.training import TrainingRepository
from app.main import app
from app.modules.datasets import service as datasets
from app.modules.training import router as training_router
from app.modules.training import service as training


def _dataset(tmp_path: Path, monkeypatch) -> str:
    folder = tmp_path / "raw" / "sample"
    (folder / "meta" / "episodes").mkdir(parents=True)
    (folder / "data").mkdir()
    (folder / "meta" / "info.json").write_text(json.dumps({
        "codebase_version": "v3.0", "total_episodes": 3, "total_frames": 3,
    }), encoding="utf-8")
    pq.write_table(pa.table({"episode_index": [0, 1, 2]}),
                   folder / "meta" / "episodes" / "chunk-000.parquet")
    pq.write_table(pa.table({"action": [[0.1], [0.2], [0.3]]}),
                   folder / "data" / "chunk-000.parquet")
    row = datasets.register_dataset(tmp_path, "raw", "sample", "训练样本")

    def passed(_root, rows):
        for item in rows:
            item.update(qc_state="passed", qc_label="已审核通过", qc_task_id="QC000001")
        return rows

    monkeypatch.setattr(training, "attach_status", passed)
    return row["id"]


def test_preflight_uses_final_dataset_and_episode_split(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    result = training.preflight(tmp_path, dataset_id)
    assert result["passed"] is True
    assert result["dataset"]["id"] == dataset_id
    assert len(result["split"]["train_episode_ids"]) == 2
    assert len(result["split"]["validation_episode_ids"]) == 1
    assert result["snapshot"]["qc_task_id"] == "QC000001"


def test_preflight_rejects_unapproved_final_dataset(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    monkeypatch.setattr(training, "attach_status", lambda _root, rows: [
        row.update(qc_state="uninspected", qc_label="未质检", qc_task_id=None)
        for row in rows
    ])
    result = training.preflight(tmp_path, dataset_id)
    assert result["passed"] is False
    assert any("最终 DS" in issue for issue in result["issues"])


def test_task_is_persistent_and_only_fine_tuning_is_submitted(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    preset = {"id": "standard", "parameters": {"steps": 500, "batch_size": 1}}
    monkeypatch.setattr(training, "environment", lambda _root: {
        "online": True, "id": "primary", "reason": "", "models": [],
        "runtimes": [{"id": "lerobot", "name": "LeRobot（Conda）",
                      "available": True, "model_ids": ["xvla"]}],
    })
    monkeypatch.setattr(training, "models", lambda _root: [{
        "id": "xvla", "version": "xvla-base@abc", "available": True,
        "presets": [preset],
    }])
    submitted = []
    monkeypatch.setattr(training, "_runner_request", lambda _root, method, path, payload=None:
                        submitted.append((method, path, payload)) or {"status": "QUEUED"})
    task = training.create_task(tmp_path, name="抓取微调", dataset_id=dataset_id,
                                model_id="xvla", preset="standard", executor_id="primary")
    assert task["id"] == "TR000001"
    assert task["status"] == "QUEUED"
    assert task["model_version"] == "xvla-base@abc"
    assert task["runtime_id"] == "lerobot"
    assert submitted[0][2]["model_id"] == "xvla"
    assert submitted[0][2]["runtime_id"] == "lerobot"
    assert TrainingRepository(tmp_path).get(task["id"])["snapshot"]["dataset_id"] == dataset_id


def test_custom_training_parameters_are_validated_and_snapshotted(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    monkeypatch.setattr(training, "environment", lambda _root: {
        "online": True, "id": "primary", "reason": "", "models": [],
        "runtimes": [{"id": "lerobot", "name": "LeRobot（Conda）",
                      "available": True, "model_ids": ["xvla"]}],
    })
    monkeypatch.setattr(training, "models", lambda _root: [{
        "id": "xvla", "version": "xvla-base@test", "available": True,
        "presets": [{"id": "standard", "parameters": {
            "steps": 2000, "batch_size": 1, "log_freq": 20, "num_workers": 0,
        }}],
        "parameter_schema": {
            "steps": {"min": 100, "max": 10000},
            "batch_size": {"choices": [1, 2]},
            "log_freq": {"choices": [10, 20, 50, 100]},
        },
    }])
    submitted = []
    monkeypatch.setattr(training, "_runner_request", lambda _root, method, path,
                        payload=None: submitted.append(payload) or {"status": "QUEUED"})
    for invalid in ({"steps": 1}, {"batch_size": 3}, {"shell": 1},
                    {"steps": True}, {"steps": 100, "log_freq": 200}):
        with pytest.raises(training.TrainingError):
            training.create_task(tmp_path, name="invalid", dataset_id=dataset_id,
                                 model_id="xvla", preset="standard", executor_id="primary",
                                 parameters=invalid)
    assert submitted == []
    task = training.create_task(tmp_path, name="custom", dataset_id=dataset_id,
                                model_id="xvla", preset="standard", executor_id="primary",
                                parameters={"steps": 300, "batch_size": 2,
                                            "log_freq": 10})
    assert task["parameters"] == {
        "steps": 300, "batch_size": 2, "log_freq": 10, "num_workers": 0,
    }
    assert submitted[-1]["parameters"] == task["parameters"]


def test_training_runtime_must_be_registered_and_support_model(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    monkeypatch.setattr(training, "environment", lambda _root: {
        "online": True, "id": "primary", "reason": "", "models": [],
        "runtimes": [{"id": "lerobot", "available": True, "model_ids": ["xvla"]}],
    })
    monkeypatch.setattr(training, "models", lambda _root: [{
        "id": "xvla", "version": "xvla-base@test", "available": True,
        "presets": [{"id": "standard", "parameters": {"steps": 2000}}],
    }])
    with pytest.raises(training.TrainingError, match="未登记"):
        training.create_task(tmp_path, name="bad runtime", dataset_id=dataset_id,
                             model_id="xvla", preset="standard", executor_id="primary",
                             runtime_id="unknown")
    assert TrainingRepository(tmp_path).list() == []


def test_retry_preserves_editable_values_without_rejecting_fixed_workers(tmp_path, monkeypatch):
    old = {"id": "TR000001", "status": "FAILED", "name": "旧任务",
           "dataset_id": "DS000001", "model_id": "xvla", "preset": "standard",
           "executor_id": "primary", "runtime_id": "lerobot", "parameters": {
               "steps": 300, "batch_size": 2, "log_freq": 10, "num_workers": 0,
           }}
    monkeypatch.setattr(training, "get_task", lambda _root, _task_id: old)
    captured = {}

    def create(_root, **kwargs):
        captured.update(kwargs)
        return {"id": "TR000002"}

    monkeypatch.setattr(training, "create_task", create)
    assert training.retry_task(tmp_path, old["id"])["id"] == "TR000002"
    assert captured["parameters"] == {"steps": 300, "batch_size": 2, "log_freq": 10}
    assert captured["runtime_id"] == "lerobot"
    assert captured["retry_of"] == old["id"]


def test_evaluation_trace_only_exposes_registered_heldout_episode(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    repo = TrainingRepository(tmp_path)
    task = repo.create(
        name="动作评估", dataset_id=dataset_id, model_id="xvla",
        model_version="xvla-base@test", preset="standard", executor_id="primary",
        snapshot={}, split={"validation_episode_ids": [5]}, parameters={},
    )
    repo.update(task["id"], status="COMPLETED", evaluation={
        "checkpoint_smoke_test": True,
        "action_episodes": [{"episode_id": 5, "frames": 1}],
    })
    trace_path = tmp_path / "training-runs" / task["id"] / "action-traces" / "episode-5.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(json.dumps({"episode_id": 5, "points": [
        {"frame": 0, "truth": [1.0], "prediction": [1.2]},
    ]}), encoding="utf-8")
    assert training.evaluation_episode_trace(tmp_path, task["id"], 5)["points"][0]["truth"] == [1.0]
    monkeypatch.setattr(training_router, "settings", Settings(data_root=tmp_path))
    client = TestClient(app)
    url = f"/training/api/tasks/{task['id']}/evaluation/episodes/5"
    assert client.get(url).json()["points"][0]["prediction"] == [1.2]
    with pytest.raises(training.TrainingError, match="没有动作评估"):
        training.evaluation_episode_trace(tmp_path, task["id"], 6)
    repo.archive(task["id"], "tester")
    with pytest.raises(training.TrainingError, match="不存在"):
        training.evaluation_episode_trace(tmp_path, task["id"], 5)
    assert client.get(url).status_code == 404


def test_vlm_button_pauses_and_resumes_vlm_quality_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("HKC_TRAINING_OPERATORS", "operator")
    monkeypatch.setattr(training, "environment", lambda _root: {
        "online": True, "vlm": {"controllable": True},
    })
    calls = []
    monkeypatch.setattr(training, "_runner_request", lambda _root, method, path, payload=None:
                        calls.append((method, path)) or {"state": "stopped" if path.endswith("stop")
                                                        else "running"})
    stopped = training.vlm_control(tmp_path, "operator", "stop")
    assert stopped["state"] == "stopped"
    assert QualityRepository(tmp_path).vlm_paused() is True
    started = training.vlm_control(tmp_path, "operator", "start")
    assert started["state"] == "running"
    assert QualityRepository(tmp_path).vlm_paused() is False
    assert calls == [("POST", "/vlm/stop"), ("POST", "/vlm/start")]


def test_vlm_stop_timeout_keeps_quality_gate_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HKC_TRAINING_OPERATORS", "operator")
    monkeypatch.setattr(training, "environment", lambda _root: {
        "online": True, "vlm": {"controllable": True},
    })

    def timeout(*_args, **_kwargs):
        raise training.TrainingError("执行器请求超时", 503)

    monkeypatch.setattr(training, "_runner_request", timeout)
    with pytest.raises(training.TrainingError, match="超时"):
        training.vlm_control(tmp_path, "operator", "stop")
    assert QualityRepository(tmp_path).vlm_paused() is True


def test_training_task_delete_is_recoverable_and_preserves_model_version(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    repo = TrainingRepository(tmp_path)
    task = repo.create(
        name="抓取微调", dataset_id=dataset_id, model_id="xvla",
        model_version="xvla-base@abc", preset="standard", executor_id="primary",
        snapshot={"dataset_id": dataset_id}, split={}, parameters={},
    )
    repo.update(task["id"], status="COMPLETED", phase="done", progress=100,
                evaluation={"checkpoint_smoke_test": True},
                artifact={"package_relpath": "models/example.tar.gz"})
    archived = training.delete_task(tmp_path, task["id"], "test_user")
    assert archived["deleted_by"] == "test_user"
    assert archived["deleted_at"] is not None
    assert training.list_tasks(tmp_path) == []
    assert [row["id"] for row in training.list_tasks(tmp_path, deleted=True)] == [task["id"]]
    with pytest.raises(training.TrainingError, match="不存在"):
        training.get_task(tmp_path, task["id"])
    assert training.model_versions(tmp_path)[0]["id"] == task["id"]

    restored = training.restore_task(tmp_path, task["id"])
    assert restored["deleted_at"] is None
    assert restored["deleted_by"] is None
    assert [row["id"] for row in training.list_tasks(tmp_path)] == [task["id"]]


@pytest.mark.parametrize("initial_status", ["QUEUED", "RUNNING"])
def test_active_training_delete_cancels_then_archives(tmp_path, monkeypatch, initial_status):
    dataset_id = _dataset(tmp_path, monkeypatch)
    repo = TrainingRepository(tmp_path)
    task = repo.create(
        name="待删除训练", dataset_id=dataset_id, model_id="xvla",
        model_version="xvla-base@abc", preset="standard", executor_id="primary",
        snapshot={}, split={}, parameters={},
    )
    repo.update(task["id"], status=initial_status)
    state = {"status": initial_status}
    calls = []

    def runner(_root, method, path, payload=None):
        calls.append((method, path))
        if method == "POST":
            if initial_status == "QUEUED":
                state["status"] = "CANCELED"
            return {"id": task["id"], "status": state["status"]}
        return {"status": state["status"], "phase": "canceled"}

    monkeypatch.setattr(training, "_runner_request", runner)
    first = training.delete_task(tmp_path, task["id"], "test_user")
    assert calls.count(("POST", f"/jobs/{task['id']}/cancel")) == 1
    if initial_status == "RUNNING":
        assert first["deleted_at"] is None
        assert first["delete_requested_by"] == "test_user"
        assert training.delete_task(tmp_path, task["id"], "test_user")["deleted_at"] is None
        assert calls.count(("POST", f"/jobs/{task['id']}/cancel")) == 1
        state["status"] = "CANCELED"
        assert training.list_tasks(tmp_path) == []
        first = repo.get(task["id"])
    assert first["deleted_by"] == "test_user"
    assert first["deleted_at"] is not None
    assert first["delete_requested_at"] is None
    assert [row["id"] for row in training.list_tasks(tmp_path, deleted=True)] == [task["id"]]


def test_active_training_delete_does_not_mark_pending_when_executor_offline(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    repo = TrainingRepository(tmp_path)
    task = repo.create(
        name="待删除训练", dataset_id=dataset_id, model_id="xvla",
        model_version="xvla-base@abc", preset="standard", executor_id="primary",
        snapshot={}, split={}, parameters={},
    )

    def runner(_root, method, path, payload=None):
        if method == "POST":
            raise training.TrainingError("执行器离线", 503)
        return {"status": "QUEUED"}

    monkeypatch.setattr(training, "_runner_request", runner)
    with pytest.raises(training.TrainingError, match="执行器离线"):
        training.delete_task(tmp_path, task["id"], "test_user")
    assert repo.get(task["id"])["delete_requested_at"] is None


def test_training_repository_migrates_existing_tasks(tmp_path):
    repo = TrainingRepository(tmp_path)
    repo.initialize()
    with repo.connection() as db:
        db.execute("DROP INDEX training_jobs_deleted_idx")
        db.execute("ALTER TABLE training_jobs DROP COLUMN deleted_at")
        db.execute("ALTER TABLE training_jobs DROP COLUMN deleted_by")
        db.execute("ALTER TABLE training_jobs DROP COLUMN delete_requested_at")
        db.execute("ALTER TABLE training_jobs DROP COLUMN delete_requested_by")
        db.execute("ALTER TABLE training_jobs DROP COLUMN runtime_id")
    repo.initialize()
    with repo.connection() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(training_jobs)")}
    assert {"deleted_at", "deleted_by", "delete_requested_at",
            "delete_requested_by", "runtime_id"} <= columns


def test_training_delete_restore_api_uses_recycle_bin(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    monkeypatch.setattr(training_router, "settings", Settings(data_root=tmp_path))
    task = TrainingRepository(tmp_path).create(
        name="测试任务", dataset_id=dataset_id, model_id="xvla",
        model_version="xvla-base@abc", preset="standard", executor_id="primary",
        snapshot={}, split={}, parameters={},
    )
    client = TestClient(app)
    state = {"status": "QUEUED"}

    def runner(_root, method, path, payload=None):
        if method == "POST":
            state["status"] = "CANCELED"
        return {"status": state["status"], "phase": "canceled"}

    monkeypatch.setattr(training, "_runner_request", runner)
    response = client.delete(f"/training/api/tasks/{task['id']}")
    assert response.status_code == 200
    assert response.json()["deleted_by"] == "test_user"
    assert client.get("/training/api/tasks").json() == []
    assert [row["id"] for row in client.get("/training/api/tasks?deleted=true").json()] == [task["id"]]
    assert client.get(f"/training/api/tasks/{task['id']}").status_code == 404
    assert client.post(f"/training/api/tasks/{task['id']}/restore").status_code == 200
    assert [row["id"] for row in client.get("/training/api/tasks").json()] == [task["id"]]


def test_training_purge_requires_archived_task_and_removes_model_version(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    repo = TrainingRepository(tmp_path)
    task = repo.create(
        name="待清理任务", dataset_id=dataset_id, model_id="xvla",
        model_version="xvla-base@abc", preset="standard", executor_id="primary",
        snapshot={}, split={}, parameters={},
    )
    calls = []
    monkeypatch.setattr(training, "_runner_request", lambda _root, method, path,
                        payload=None: calls.append((method, path)) or {"purged": True})
    with pytest.raises(training.TrainingError, match="回收站"):
        training.purge_task(tmp_path, task["id"])
    assert calls == []
    repo.update(task["id"], status="COMPLETED",
                evaluation={"checkpoint_smoke_test": True},
                artifact={"package_relpath": "models/example.tar.gz"})
    repo.archive(task["id"], "test_user")
    assert training.model_versions(tmp_path)[0]["id"] == task["id"]

    def offline(*_args, **_kwargs):
        raise training.TrainingError("执行器离线", 503)

    monkeypatch.setattr(training, "_runner_request", offline)
    with pytest.raises(training.TrainingError, match="离线"):
        training.purge_task(tmp_path, task["id"])
    assert repo.get(task["id"])["deleted_at"] is not None

    monkeypatch.setattr(training, "_runner_request", lambda _root, method, path,
                        payload=None: calls.append((method, path)) or {"purged": True})
    assert training.purge_task(tmp_path, task["id"]) == {"id": task["id"], "purged": True}
    assert calls == [("DELETE", f"/jobs/{task['id']}")]
    assert repo.get(task["id"]) is None
    assert training.model_versions(tmp_path) == []
    with pytest.raises(training.TrainingError, match="不存在"):
        training.restore_task(tmp_path, task["id"])
    newer = repo.create(
        name="新任务", dataset_id=dataset_id, model_id="xvla",
        model_version="xvla-base@abc", preset="standard", executor_id="primary",
        snapshot={}, split={}, parameters={},
    )
    assert newer["id"] != task["id"]


def test_training_purge_api_requires_exact_id_confirmation(tmp_path, monkeypatch):
    dataset_id = _dataset(tmp_path, monkeypatch)
    monkeypatch.setattr(training_router, "settings", Settings(data_root=tmp_path))
    repo = TrainingRepository(tmp_path)
    task = repo.create(
        name="待清理任务", dataset_id=dataset_id, model_id="xvla",
        model_version="xvla-base@abc", preset="standard", executor_id="primary",
        snapshot={}, split={}, parameters={},
    )
    repo.update(task["id"], status="FAILED")
    repo.archive(task["id"], "test_user")
    monkeypatch.setattr(training, "_runner_request", lambda *_args, **_kwargs: {"purged": True})
    client = TestClient(app)
    url = f"/training/api/tasks/{task['id']}/purge"
    assert client.post(url).status_code == 400
    assert client.post(url, headers={"X-HKC-Training-Purge": "TR999999"}).status_code == 400
    assert repo.get(task["id"]) is not None
    assert client.post(url, headers={"X-HKC-Training-Purge": task["id"]}).status_code == 200
    assert repo.get(task["id"]) is None
