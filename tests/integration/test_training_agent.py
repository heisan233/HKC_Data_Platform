"""Executor rejects arbitrary paths, models, and mutable dataset snapshots."""

import hashlib
import importlib.util
import json
import subprocess
import tarfile
from pathlib import Path

import pytest


def _agent(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    model = tmp_path / "model"
    tokenizer = tmp_path / "tokenizer"
    for path in (root, model, tokenizer):
        path.mkdir()
    (model / "config.json").write_text(json.dumps({"input_features": {
        "observation.images.image": {"type": "VISUAL"},
        "observation.images.image2": {"type": "VISUAL"},
        "observation.images.image3": {"type": "VISUAL"},
    }}))
    (model / "model.safetensors").write_bytes(b"base weights")
    (model / "policy_postprocessor.json").write_text("{}")
    (model / "policy_preprocessor.json").write_text(json.dumps({"steps": [
        {"registry_name": "tokenizer_processor", "config": {"tokenizer_name": "remote/name"}},
    ]}))
    monkeypatch.setenv("HKC_TRAINING_STORAGE", str(root))
    monkeypatch.setenv("HKC_TRAINING_MODEL", str(model))
    monkeypatch.setenv("HKC_TRAINING_TOKENIZER", str(tokenizer))
    monkeypatch.setenv("HKC_TRAINING_SOCKET_DIR", str(tmp_path / "private-socket"))
    monkeypatch.setenv("HKC_TRAINING_WORK_DIR", str(tmp_path / "private-work"))
    script = Path(__file__).resolve().parents[2] / "deploy" / "training_agent.py"
    spec = importlib.util.spec_from_file_location("hkc_training_agent_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.WORK_DIR == tmp_path / "private-work"
    return module, root


def _payload(agent, root):
    folder = root / "derived" / "ready"
    (folder / "meta").mkdir(parents=True)
    info = folder / "meta" / "info.json"
    info.write_text(json.dumps({"codebase_version": "v3.0", "features": {
        "observation.images.head": {"dtype": "video"},
        "observation.images.hand_right": {"dtype": "video"},
    }}))
    fingerprint = hashlib.sha256()
    stat = info.stat()
    fingerprint.update(f"meta/info.json\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return {
        "id": "TR000001", "model_id": "xvla", "model_version": agent.MODEL_VERSION,
        "preset": "standard", "parameters": agent.PRESETS["standard"],
        "runtime_id": agent.RUNTIME_ID,
        "dataset": {"dataset_id": "DS000001", "storage_area": "derived",
                    "storage_relpath": "ready", "info_sha256": agent.sha256_file(info),
                    "dataset_fingerprint": fingerprint.hexdigest()},
        "split": {"train_episode_ids": [0], "validation_episode_ids": [1]},
    }


def test_agent_accepts_only_registered_snapshot_and_safe_parameters(tmp_path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)
    payload = _payload(agent, root)
    assert agent.validate_payload(payload)[0] == "TR000001"
    payload["runtime_id"] = "unregistered"
    with pytest.raises(ValueError, match="Conda"):
        agent.validate_payload(payload)
    payload["runtime_id"] = agent.RUNTIME_ID
    payload["parameters"] = {"steps": 999999}
    with pytest.raises(ValueError, match="参数"):
        agent.validate_payload(payload)
    payload["parameters"] = {**agent.PRESETS["standard"], "steps": 300,
                             "batch_size": 2, "log_freq": 10}
    assert agent.validate_payload(payload)[0] == "TR000001"
    for bad in ({**payload["parameters"], "steps": True},
                {**payload["parameters"], "batch_size": 9},
                {**payload["parameters"], "num_workers": 8},
                {**payload["parameters"], "shell": "rm"}):
        payload["parameters"] = bad
        with pytest.raises(ValueError):
            agent.validate_payload(payload)


def test_agent_purge_removes_only_terminal_job_output(tmp_path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)
    _payload(agent, root)
    job_id = "TR000001"
    job_dir = agent.JOBS / job_id
    work_dir = agent.WORK_DIR / job_id
    job_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    agent.MODELS.mkdir()
    (job_dir / "train.log").write_text("training log")
    (work_dir / "checkpoint").write_bytes(b"checkpoint")
    (work_dir / "base-link").symlink_to(agent.MODEL, target_is_directory=True)
    package = agent.MODELS / f"{job_id}-xvla.tar.gz"
    package.write_bytes(b"package")
    other = agent.MODELS / "TR000002-xvla.tar.gz"
    other.write_bytes(b"other package")
    agent.STATE[job_id] = {"id": job_id, "status": "RUNNING"}
    with pytest.raises(ValueError, match="已结束"):
        agent.purge_job(job_id)
    assert package.exists()
    agent.STATE[job_id]["status"] = "COMPLETED"
    assert agent.purge_job(job_id) == {"id": job_id, "purged": True}
    assert not job_dir.exists() and not work_dir.exists() and not package.exists()
    assert other.read_bytes() == b"other package"
    assert (agent.MODEL / "model.safetensors").read_bytes() == b"base weights"
    assert (root / "derived" / "ready" / "meta" / "info.json").exists()
    assert job_id not in agent.STATE
    assert agent.purge_job(job_id)["purged"] is True  # Retry after partial failure is safe.


def test_agent_purge_refuses_symlinked_job_directory(tmp_path, monkeypatch):
    agent, _root = _agent(tmp_path, monkeypatch)
    external = tmp_path / "unrelated"
    external.mkdir()
    (external / "keep.txt").write_text("do not delete")
    agent.JOBS.mkdir()
    (agent.JOBS / "TR000001").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        agent.purge_job("TR000001")
    assert (external / "keep.txt").read_text() == "do not delete"


def test_agent_parses_real_metric_lines_and_ignores_unverified_values(tmp_path, monkeypatch):
    agent, _ = _agent(tmp_path, monkeypatch)
    text = ("Training:  1%| 20/2000 [00:10<00:20]INFO 2026-10-26 09:54:38 "
            "ot_train.py:435 step:20 smpl:20 ep:0 epch:0.01 loss:2.5 grdn:7.2 "
            "lr:1.7e-06 updt_s:0.1\n"
            "step:40 loss:9 grdn:9 lr:2e-06 epch:0.02\n"
            "Training: 2%| 40/2000 INFO ot_train.py:435 step:40 smpl:40 "
            "epch:0.02 loss:nan grdn:8 lr:2e-06\n")
    points = agent.parse_training_metrics(text, 2000)
    assert len(points) == 1
    assert points[0]["learning_rate"] == 1.7e-06
    assert points[0]["gradient_norm"] == 7.2
    assert points[0]["epoch"] == 0.01


def test_agent_rejects_changed_dataset_or_path_escape(tmp_path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)
    payload = _payload(agent, root)
    payload["dataset"]["storage_relpath"] = "../outside"
    with pytest.raises(ValueError, match="目录"):
        agent.validate_payload(payload)
    payload["dataset"]["storage_relpath"] = "ready"
    info = root / "derived" / "ready" / "meta" / "info.json"
    info.write_text('{"codebase_version":"v3.0","changed":true}')
    with pytest.raises(ValueError, match="已变化"):
        agent.validate_payload(payload)


def test_train_command_uses_lerobot_policy_override_syntax(tmp_path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)
    payload = _payload(agent, root)
    folder = root / "derived" / "ready"
    output = agent.WORK_DIR / payload["id"] / "output"

    command = agent.build_train_command(payload, folder, output)

    assert command[:3] == [str(agent.PYTHON), "-m", "lerobot.scripts.lerobot_train"]
    assert f"--policy.path={agent.MODEL}" in command
    assert f"--policy.tokenizer_name={agent.TOKENIZER}" in command
    assert "--policy.push_to_hub=false" in command
    assert "--policy.path" not in command
    assert "--policy.tokenizer_name" not in command
    assert "--policy.push_to_hub" not in command
    assert command[command.index("--dataset.root") + 1] == str(folder)
    assert "--rename_map=" + json.dumps({
        "observation.images.head": "observation.images.image",
        "observation.images.hand_right": "observation.images.image2",
    }, separators=(",", ":")) in command


def test_camera_mapping_rejects_more_views_than_model(tmp_path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)
    info = root / "raw" / "too-many" / "meta" / "info.json"
    info.parent.mkdir(parents=True)
    info.write_text(json.dumps({"features": {
        f"observation.images.camera_{index}": {"dtype": "video"}
        for index in range(4)
    }}))
    with pytest.raises(ValueError, match="支持 1 至 3 路"):
        agent.camera_rename_map(info.parent.parent)


def test_local_model_stage_rewrites_only_private_copy(tmp_path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    staged = agent.prepare_local_model(work)
    assert (staged / "model.safetensors").is_symlink()
    assert (staged / "model.safetensors").resolve() == agent.MODEL / "model.safetensors"
    assert json.loads((staged / "config.json").read_text())["tokenizer_name"] == str(agent.TOKENIZER)
    assert json.loads((staged / "policy_preprocessor.json").read_text())["steps"][0]["config"][
        "tokenizer_name"] == str(agent.TOKENIZER)
    assert json.loads((agent.MODEL / "policy_preprocessor.json").read_text())["steps"][0]["config"][
        "tokenizer_name"] == "remote/name"
    payload = _payload(agent, root)
    command = agent.build_train_command(payload, root / "derived" / "ready", work / "output", staged)
    assert f"--policy.path={staged}" in command


def test_model_package_contains_tokenizer_stats_and_checksums(tmp_path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)
    payload = _payload(agent, root)
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}")
    (checkpoint / "model.safetensors").write_bytes(b"weights")
    (checkpoint / "policy_preprocessor.json").write_text("{}")
    (agent.TOKENIZER / "tokenizer_config.json").write_text("{}")
    (agent.TOKENIZER / "vocab.json").write_text("{}")
    (agent.TOKENIZER / "merges.txt").write_text("merges")
    folder = root / "derived" / "ready"
    (folder / "meta" / "stats.json").write_text("{}")
    result_file = tmp_path / "evaluation.json"
    result_file.write_text('{"checkpoint_smoke_test": true}')
    (agent.JOBS / payload["id"]).mkdir(parents=True)

    artifact = agent.package_model(payload, checkpoint, result_file, folder,
                                   {"checkpoint_smoke_test": True})
    package = root / artifact["package_relpath"]
    assert artifact["sha256"] == agent.sha256_file(package)
    with tarfile.open(package) as archive:
        names = set(archive.getnames())
        assert {"pretrained_model/model.safetensors", "pretrained_model/config.json",
                "pretrained_model/policy_preprocessor.json", "tokenizer/vocab.json",
                "tokenizer/merges.txt", "dataset_stats.json", "evaluation.json",
                "manifest.json"} <= names
        manifest = json.load(archive.extractfile("manifest.json"))
        assert manifest["files_sha256"]["tokenizer/vocab.json"] == agent.sha256_file(
            agent.TOKENIZER / "vocab.json")


def test_vlm_control_uses_only_authorized_service_command(tmp_path, monkeypatch):
    agent, _ = _agent(tmp_path, monkeypatch)
    monkeypatch.setattr(agent, "VLM_SERVICE", "qwen3vl.service")
    calls = []
    current = {"state": "active"}

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[:2] == ["/usr/bin/systemctl", "is-active"]:
            return subprocess.CompletedProcess(command, 0, current["state"] + "\n", "")
        if command[:3] == ["/usr/bin/sudo", "-n", "-l"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", "qwen3vl.service"]:
            current["state"] = "inactive"
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    assert agent.control_vlm("stop")["state"] == "stopped"
    assert ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", "qwen3vl.service"] in calls
    with pytest.raises(ValueError, match="不受支持"):
        agent.control_vlm("restart")
