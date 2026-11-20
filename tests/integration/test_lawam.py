"""Authenticated WAM routes proxy only validated operations to the private agent."""

import hashlib
import importlib.util
import json
from email.message import Message
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.db.datasets import DatasetRepository
from app.main import app
from app.modules.datasets import service as datasets
from app.modules.datasets import trash as dataset_trash
from app.modules.training import lawam_service
from app.modules.training.lawam_schemas import LaWAMCreate
from tests.quality_helpers import approve_dataset

client = TestClient(app)


def _wam_dataset(root: Path, *, missing_action: bool = False) -> str:
    repo = DatasetRepository(root)
    dataset_id = repo.reserve_id()
    folder = root / "data" / dataset_id
    (folder / "meta/episodes").mkdir(parents=True)
    (folder / "data").mkdir()
    features = {
        "observation.images.image": {"dtype": "video", "shape": [256, 256, 3]},
        "observation.images.image2": {"dtype": "video", "shape": [256, 256, 3]},
        "observation.state": {"dtype": "float32", "shape": [8]},
        "task_index": {"dtype": "int64", "shape": [1]},
    }
    if not missing_action:
        features["action"] = {"dtype": "float32", "shape": [7]}
    (folder / "meta/info.json").write_text(json.dumps({
        "codebase_version": "v3.0", "robot_type": "panda", "total_episodes": 2,
        "total_frames": 2, "features": features,
    }), encoding="utf-8")
    pd.DataFrame({"task_index": [0]}, index=pd.Index(["pick"], name="task")).to_parquet(
        folder / "meta/tasks.parquet")
    pq.write_table(pa.table({"episode_index": [0, 1]}),
                   folder / "meta/episodes/file.parquet")
    pq.write_table(pa.table({"observation.state": [[0.0] * 8] * 2,
                             "action": [[0.0] * 7] * 2,
                             "task_index": [0, 0]}), folder / "data/file.parquet")
    for key in ("observation.images.image", "observation.images.image2"):
        video = folder / "videos" / key / "chunk-000/file-000.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"sample")
    datasets.register_dataset(root, "data", dataset_id, "WAM 测试", reserved_id=dataset_id,
                              dataset_family="wam")
    approve_dataset(root, dataset_id)
    return dataset_id


def test_wam_catalog_and_preflight_require_approved_compatible_dataset(tmp_path) -> None:
    dataset_id = _wam_dataset(tmp_path)
    assert [item["id"] for item in lawam_service.available_datasets(tmp_path)] == [dataset_id]
    checked = lawam_service.preflight(tmp_path, dataset_id, "custom", "0,1")
    assert checked["episodes"] == 2
    assert checked["snapshot"]["dataset_id"] == dataset_id
    with pytest.raises(lawam_service.LaWAMError, match="不在所选数据集中"):
        lawam_service.preflight(tmp_path, dataset_id, "custom", "9")
    DatasetRepository(tmp_path).mark_for_qc(dataset_id)
    assert lawam_service.available_datasets(tmp_path) == []
    with pytest.raises(lawam_service.LaWAMError, match="不可用"):
        lawam_service.preflight(tmp_path, dataset_id)


def test_wam_preflight_reports_missing_action(tmp_path) -> None:
    dataset_id = _wam_dataset(tmp_path, missing_action=True)
    with pytest.raises(lawam_service.LaWAMError, match="action"):
        lawam_service.preflight(tmp_path, dataset_id)


def test_wam_dataset_trash_respects_training_references(tmp_path) -> None:
    dataset_id = _wam_dataset(tmp_path)
    state = tmp_path / "training-runs/lawam/WAM0123456789AB/state.json"
    state.parent.mkdir(parents=True)
    job = {"id": "WAM0123456789AB", "kind": "train", "status": "RUNNING",
           "parameters": {"dataset_id": dataset_id}}
    state.write_text(json.dumps(job), encoding="utf-8")
    with pytest.raises(ValueError, match="仍引用"):
        dataset_trash.trash_dataset(tmp_path, dataset_id, "tester")
    job["status"] = "COMPLETED"
    state.write_text(json.dumps(job), encoding="utf-8")
    dataset_trash.trash_dataset(tmp_path, dataset_id, "tester")
    record = DatasetRepository(tmp_path).get(dataset_id, include_deleted=True)
    assert "仍引用" in dataset_trash.purge_blocker(tmp_path, record)


@pytest.fixture
def agent(monkeypatch):
    calls = []

    def fake(method, path, payload=None, headers=None):
        calls.append((method, path, payload) if headers is None else
                     (method, path, payload, headers))
        if (path in {"/jobs", "/deleted-jobs", "/evaluations", "/bases"}
                or path.endswith(("/metrics", "/checkpoints"))) and method == "GET":
            return []
        return {"id": "WAM0123456789AB", "online": True}

    monkeypatch.setattr(lawam_service, "request", fake)
    monkeypatch.setattr(lawam_service, "preflight", lambda *_args: {
        "snapshot": {"dataset_id": "DS000001", "info_sha256": "a" * 64,
                     "dataset_fingerprint": "b" * 64},
    })
    return calls


def test_lawam_read_routes_use_agent(agent) -> None:
    for route in ("status", "bases", "jobs", "deleted-jobs", "evaluations"):
        response = client.get(f"/training/lawam/api/{route}")
        assert response.status_code == 200
    assert agent == [("GET", f"/{route}", None) for route in
                     ("status", "bases", "jobs", "deleted-jobs", "evaluations")]


def test_lawam_job_detail_delete_and_restore_use_agent(agent) -> None:
    identifier = "WAM0123456789AB"
    assert client.get(f"/training/lawam/api/jobs/{identifier}").status_code == 200
    assert client.get(f"/training/lawam/api/jobs/{identifier}/metrics").status_code == 200
    assert client.get(f"/training/lawam/api/jobs/{identifier}/checkpoints").status_code == 200
    assert client.delete(f"/training/lawam/api/jobs/{identifier}").status_code == 200
    assert client.post(f"/training/lawam/api/jobs/{identifier}/restore").status_code == 200
    assert agent == [
        ("GET", f"/jobs/{identifier}", None),
        ("GET", f"/jobs/{identifier}/metrics", None),
        ("GET", f"/jobs/{identifier}/checkpoints", None),
        ("DELETE", f"/jobs/{identifier}", None),
        ("POST", f"/jobs/{identifier}/restore", {}),
    ]


def test_lawam_purge_requires_confirmation(agent) -> None:
    identifier = "WAM0123456789AB"
    path = f"/training/lawam/api/jobs/{identifier}/purge"
    assert client.post(path).status_code == 400
    assert not agent
    response = client.post(path, headers={"X-HKC-LaWAM-Purge": identifier})
    assert response.status_code == 200
    assert agent == [("POST", f"/jobs/{identifier}/purge", {},
                      {"X-HKC-LaWAM-Purge": identifier})]


def test_lawam_submit_validates_and_forwards_parameters(agent) -> None:
    payload = {
        "name": "smoke", "base": "/models/lawam-pretrain", "dataset_id": "DS000001",
        "dataset_mode": "custom",
        "episodes": "0,1", "steps": 2, "batch": 1, "action_horizon": 24,
        "warmup": 0, "lr": 0.0001, "unfreeze_llm_layers": 0, "save_freq": 1,
        "freeze_vision": True, "freeze_llm": True, "stop_vllm": False,
    }
    response = client.post("/training/lawam/api/jobs", json=payload)
    assert response.status_code == 201
    assert agent[-1] == ("POST", "/jobs", {**payload, "snapshot": {
        "dataset_id": "DS000001", "info_sha256": "a" * 64,
        "dataset_fingerprint": "b" * 64}})
    bad = client.post("/training/lawam/api/jobs", json={**payload, "steps": 0})
    assert bad.status_code == 422
    assert len(agent) == 1


def test_lawam_episode_commas_normalize_before_preflight() -> None:
    payload = LaWAMCreate.model_validate({
        "name": "smoke", "base": "/models/lawam-pretrain", "dataset_id": "DS000001",
        "dataset_mode": "custom", "episodes": " 0， 1、2,3 ",
        "steps": 2, "batch": 1, "action_horizon": 24, "warmup": 0,
        "lr": 0.0001, "unfreeze_llm_layers": 0, "save_freq": 1,
        "freeze_vision": True, "freeze_llm": True, "stop_vllm": False,
    })
    assert payload.episodes == "0, 1,2,3"


def test_lawam_evaluation_and_cancel_use_agent(agent) -> None:
    evaluation = {
        "train_id": "WAM0123456789AB", "checkpoint": "/models/lawam-pretrain",
        "task": "libero_object", "task_id": 0, "episodes": 1,
        "replan": 1, "task_text": "auto",
    }
    response = client.post("/training/lawam/api/evaluations", json=evaluation)
    assert response.status_code == 201
    assert agent[-1] == ("POST", "/evaluations", evaluation)
    response = client.post("/training/lawam/api/jobs/WAM0123456789AB/cancel")
    assert response.status_code == 200
    assert agent[-1] == ("POST", "/jobs/WAM0123456789AB/cancel", {})


def test_lawam_agent_rejects_unlisted_weights_and_builds_fixed_command(
    monkeypatch, tmp_path: Path,
) -> None:
    model = tmp_path / "model"
    (model / "docker").mkdir(parents=True)
    (model / "docker/compose.yaml").write_text("services: {}", encoding="utf-8")
    (model / "weights/lawam-pretrain").mkdir(parents=True)
    (model / "weights/lawam-pretrain/config.json").write_text("{}", encoding="utf-8")
    goal = tmp_path / "goal.json"
    goal.write_text(json.dumps([379, 380]), encoding="utf-8")
    folder = tmp_path / "storage/data/DS000001/meta"
    folder.mkdir(parents=True)
    info_path = folder / "info.json"
    info_path.write_text(json.dumps({"total_episodes": 500}), encoding="utf-8")
    digest = hashlib.sha256(info_path.read_bytes()).hexdigest()
    stat = info_path.stat()
    fingerprint = hashlib.sha256(
        f"meta/info.json\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode()).hexdigest()
    monkeypatch.setenv("HKC_LAWAM_STORAGE", str(tmp_path / "storage"))
    monkeypatch.setenv("HKC_LAWAM_COMPOSE", str(model / "docker/compose.yaml"))
    monkeypatch.setenv("HKC_LAWAM_SOCKET_DIR", str(tmp_path / "control"))
    monkeypatch.setenv("HKC_LAWAM_GOAL_EPISODES", str(goal))
    agent_path = Path(__file__).resolve().parents[2] / "deploy/lawam_agent.py"
    spec = importlib.util.spec_from_file_location("lawam_agent_test", agent_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = {
        "name": "smoke", "base": "/models/lawam-pretrain", "dataset_mode": "goal",
        "dataset_id": "DS000001", "snapshot": {"dataset_id": "DS000001",
                                              "info_sha256": digest,
                                              "dataset_fingerprint": fingerprint},
        "steps": 2, "batch": 1, "action_horizon": 24, "warmup": 0,
        "lr": 0.0001, "unfreeze_llm_layers": 0, "save_freq": 1,
        "freeze_vision": True, "freeze_llm": True, "stop_vllm": False,
    }
    parameters = module.train_payload(payload)
    command = module.command({"id": "WAM0123456789AB", "kind": "train",
                              "parameters": parameters})
    assert "--dataset.episodes=[379, 380]" in command
    assert "--dataset.root=/datasets/selected" in command
    assert "--dataset.repo_id=local/DS000001" in command
    assert "--output_dir=/data/training-runs/lawam/WAM0123456789AB/out" in command
    assert "--log_freq=2" in command
    assert module.bases() == [{"id": "/models/lawam-pretrain", "label": "LaWAM 预训练基座"}]
    info_path.write_text('{"total_episodes": 500, "changed": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="元数据已变化"):
        module.train_payload(payload)
    with pytest.raises(ValueError, match="起点权重"):
        module.train_payload({**payload, "base": "/etc/passwd"})


def test_lawam_agent_soft_delete_and_restore_preserve_artifacts(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("HKC_LAWAM_STORAGE", str(tmp_path / "storage"))
    monkeypatch.setenv("HKC_LAWAM_COMPOSE", str(tmp_path / "model/docker/compose.yaml"))
    monkeypatch.setenv("HKC_LAWAM_SOCKET_DIR", str(tmp_path / "control"))
    path = Path(__file__).resolve().parents[2] / "deploy/lawam_agent.py"
    spec = importlib.util.spec_from_file_location("lawam_agent_delete_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TOKEN.parent.mkdir(parents=True)
    module.TOKEN.write_text("secret", encoding="utf-8")
    identifier = "WAM0123456789AB"
    task = {"id": identifier, "kind": "train", "status": "COMPLETED",
            "parameters": {}, "name": "test"}
    module.TASKS[identifier] = task
    artifact = module.RUNS / identifier / "out/checkpoint"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("keep", encoding="utf-8")
    handler = object.__new__(module.Handler)
    handler.headers = Message()
    handler.headers["Authorization"] = "Bearer secret"
    replies = []
    handler.reply = lambda code, data: replies.append((code, data))
    handler.path = f"/jobs/{identifier}"
    handler.dispatch("DELETE")
    assert replies[-1][0] == 200
    assert task["deleted_at"]
    state_path = module.RUNS / identifier / "state.json"
    assert json.loads(state_path.read_text(encoding="utf-8"))["deleted_at"]
    assert artifact.read_text(encoding="utf-8") == "keep"
    handler.path = "/jobs"
    handler.dispatch("GET")
    assert replies[-1] == (200, [])
    handler.path = "/deleted-jobs"
    handler.dispatch("GET")
    assert replies[-1][1][0]["id"] == identifier
    handler.path = f"/jobs/{identifier}/restore"
    handler.dispatch("POST")
    assert replies[-1][0] == 200
    assert task["deleted_at"] is None
    assert json.loads(state_path.read_text(encoding="utf-8"))["deleted_at"] is None
    assert artifact.is_file()
    handler.path = f"/jobs/{identifier}"
    handler.dispatch("DELETE")
    evaluation_id = "WAMFFFFFFFFFFFF"
    evaluation = {"id": evaluation_id, "kind": "evaluation", "status": "RUNNING",
                  "parameters": {"train_id": identifier}, "name": "eval"}
    module.TASKS[evaluation_id] = evaluation
    evaluation_artifact = module.RUNS / evaluation_id / "run.log"
    evaluation_artifact.parent.mkdir(parents=True)
    evaluation_artifact.write_text("evaluation", encoding="utf-8")
    handler.headers["X-HKC-LaWAM-Purge"] = identifier
    handler.path = f"/jobs/{identifier}/purge"
    handler.dispatch("POST")
    assert replies[-1][0] == 400
    assert artifact.is_file()
    evaluation["status"] = "COMPLETED"
    handler.dispatch("POST")
    assert replies[-1] == (200, {"id": identifier, "purged": True,
                                 "evaluation_count": 1})
    assert identifier not in module.TASKS
    assert evaluation_id not in module.TASKS
    assert not artifact.exists()
    assert not evaluation_artifact.exists()


def test_lawam_metrics_and_task_checkpoints(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HKC_LAWAM_STORAGE", str(tmp_path / "storage"))
    monkeypatch.setenv("HKC_LAWAM_COMPOSE", str(tmp_path / "model/docker/compose.yaml"))
    monkeypatch.setenv("HKC_LAWAM_SOCKET_DIR", str(tmp_path / "control"))
    path = Path(__file__).resolve().parents[2] / "deploy/lawam_agent.py"
    spec = importlib.util.spec_from_file_location("lawam_agent_metrics_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    identifier = "WAM0123456789AB"
    other = "WAMFFFFFFFFFFFF"
    module.TASKS[identifier] = {"id": identifier, "kind": "train", "status": "COMPLETED"}
    module.TASKS[other] = {"id": other, "kind": "train", "status": "COMPLETED"}
    folder = module.RUNS / identifier
    folder.mkdir(parents=True)
    log = folder / "run.log"
    log.write_text("INFO step:1 loss_total:0.5 loss_flow:0.4 lr:1e-4 grdn:0.2\n"
                   "INFO step:2 loss_total:0.3 loss_flow:0.2 lr:9e-5 step_s:0.6\n",
                   encoding="utf-8")
    points = module.metric_points(module.TASKS[identifier])
    assert [point["step"] for point in points] == [1, 2]
    assert points[1]["loss_total"] == 0.3
    assert points[1]["lr"] == 9e-5
    assert module.metric_points(module.TASKS[identifier]) == points
    with log.open("ab") as handle:
        handle.write(b"INFO step:3 loss_total:0.2")
    assert module.metric_points(module.TASKS[identifier]) == points
    with log.open("ab") as handle:
        handle.write(b" mem_gb:17.2\n")
    assert [point["step"] for point in module.metric_points(module.TASKS[identifier])] == [1, 2, 3]
    model = folder / "out/checkpoints/000003/pretrained_model"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    checkpoint = module.checkpoints(identifier)[0]["id"]
    assert checkpoint.endswith("/000003/pretrained_model")
    assert module.checkpoints(other) == []
    payload = {"train_id": identifier, "checkpoint": checkpoint, "task": "libero_object",
               "task_id": 0, "episodes": 1, "replan": 1, "task_text": "auto"}
    assert module.eval_payload(payload)["checkpoint"] == checkpoint
    with pytest.raises(ValueError, match="不属于"):
        module.eval_payload({**payload, "train_id": other})
