"""SmolVLA training path for the host executor using an offline Docker runtime."""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import time
from pathlib import Path


def stage_model(agent, work: Path) -> Path:
    """Build a private config copy while keeping the registered base immutable."""
    staged = work / "base-model"
    staged.mkdir(parents=True, exist_ok=False)
    for name in (
        "config.json",
        "policy_preprocessor.json",
        "policy_postprocessor.json",
        "policy_preprocessor_step_5_normalizer_processor.safetensors",
        "policy_postprocessor_step_0_unnormalizer_processor.safetensors",
    ):
        shutil.copy2(agent.SMOLVLA_MODEL / name, staged / name)

    config_path = staged / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["vlm_model_name"] = str(agent.SMOLVLA_VLM)
    config["load_vlm_weights"] = False
    config["push_to_hub"] = False
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    processor_path = staged / "policy_preprocessor.json"
    processor = json.loads(processor_path.read_text(encoding="utf-8"))
    tokenizer_steps = [step for step in processor.get("steps", [])
                       if step.get("registry_name") == "tokenizer_processor"]
    if len(tokenizer_steps) != 1:
        raise ValueError("SmolVLA 基座预处理器缺少唯一的 tokenizer 配置")
    tokenizer_steps[0]["config"]["tokenizer_name"] = str(agent.SMOLVLA_VLM)
    processor_path.write_text(json.dumps(processor, ensure_ascii=False), encoding="utf-8")
    (staged / "model.safetensors").symlink_to(agent.SMOLVLA_MODEL / "model.safetensors")
    return staged


def build_train_command(agent, job: dict, folder: Path, output: Path,
                        model: Path) -> list[str]:
    params = job["parameters"]
    rename_map = agent.camera_rename_map(folder, agent.SMOLVLA_MODEL)
    command = [
        "python", "-m", "lerobot.scripts.lerobot_train",
        "--dataset.repo_id", f"local/{job['dataset']['dataset_id']}",
        "--dataset.root", str(folder),
        "--dataset.episodes", json.dumps(job["split"]["train_episode_ids"]),
        f"--policy.path={model}",
        "--policy.train_expert_only=true",
        "--policy.freeze_vision_encoder=true",
        "--policy.load_vlm_weights=false",
        f"--policy.vlm_model_name={agent.SMOLVLA_VLM}",
        "--policy.dtype=bfloat16",
        "--policy.push_to_hub=false",
        "--output_dir", str(output),
        "--steps", str(params["steps"]),
        "--batch_size", str(params["batch_size"]),
        "--num_workers", str(params["num_workers"]),
        "--log_freq", str(params["log_freq"]),
        "--save_freq", str(params["steps"]),
        "--wandb.enable", "false",
    ]
    if rename_map:
        command.append(f"--rename_map={json.dumps(rename_map, separators=(',', ':'))}")
    return agent.runtime_command(job["id"], "train", command,
                                 use_gpu=True, model_id="smolvla")


def package_model(agent, job: dict, checkpoint: Path, evaluation_path: Path,
                  evaluation: dict) -> dict:
    required = (checkpoint / "config.json", checkpoint / "model.safetensors",
                evaluation_path)
    if any(not path.is_file() or path.is_symlink() for path in required):
        raise RuntimeError("SmolVLA checkpoint 或评估文件缺失")
    files: list[tuple[Path, str]] = []
    for source, prefix in ((checkpoint, "pretrained_model"),
                           (agent.SMOLVLA_VLM, "smolvlm_resources")):
        for path in sorted(source.rglob("*")):
            if ".cache" in path.relative_to(source).parts:
                continue
            if path.is_symlink():
                raise RuntimeError("SmolVLA 模型包不接受符号链接")
            if path.is_file():
                files.append((path, f"{prefix}/{path.relative_to(source).as_posix()}"))
    files.append((evaluation_path, "evaluation.json"))
    manifest = {
        "task_id": job["id"], "dataset": job["dataset"], "split": job["split"],
        "parameters": job["parameters"], "base_model_id": "smolvla",
        "base_model_version": agent.SMOLVLA_VERSION, "evaluation": evaluation,
        "files_sha256": {name: agent.sha256_file(path) for path, name in files},
    }
    manifest_path = agent.JOBS / job["id"] / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    agent.MODELS.mkdir(parents=True, exist_ok=True)
    package = agent.MODELS / f"{job['id']}-smolvla.tar.gz"
    temporary = agent.MODELS / f".{job['id']}-smolvla.tar.gz.tmp"
    try:
        with tarfile.open(temporary, "w:gz", compresslevel=1) as archive:
            for path, name in files:
                archive.add(path, arcname=name, recursive=False)
            archive.add(manifest_path, arcname="manifest.json", recursive=False)
        if job.get("cancel_requested"):
            raise InterruptedError("打包已取消")
        temporary.replace(package)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {"package_relpath": f"models/{package.name}",
            "sha256": agent.sha256_file(package), "size_bytes": package.stat().st_size}


def run(job: dict, agent) -> None:
    job_id = job["id"]
    try:
        folder = agent.dataset_folder(job["dataset"])
        params = job["parameters"]
        work = agent.SMOLVLA_WORK_DIR / job_id
        output = work / "output"
        work.mkdir(parents=True, exist_ok=False)
        staged_model = stage_model(agent, work)
        training_folder = agent.prepare_training_dataset(folder, work, "smolvla")
        if job.get("cancel_requested"):
            agent.update(job, status="CANCELED", phase="canceled",
                         completed_at=agent.now())
            return
        command = build_train_command(agent, job, training_folder, output, staged_model)
        agent.update(job, status="PREPARING", phase="starting", started_at=agent.now())
        log_path = agent.JOBS / job_id / "train.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write("Launching approved offline SmolVLA base fine-tuning preset.\n")
            log.write("Camera rename map: " + json.dumps(
                agent.camera_rename_map(folder, agent.SMOLVLA_MODEL), ensure_ascii=False) + "\n")
            log.flush()
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True, cwd=agent.JOBS / job_id)
            with agent.GUARD:
                agent.PROCESS[job_id] = process
            agent.update(job, status="RUNNING", phase="training", progress=5)
            while process.poll() is None:
                changes = agent.capture_training_metrics(job, log_path)
                card = agent.gpu()
                if card is not None:
                    history = list(job.get("gpu_history") or [])
                    history.append({"at": agent.now(),
                                    "utilization_percent": card["utilization_percent"],
                                    "used_gb": card["used_gb"],
                                    "total_gb": card["total_gb"]})
                    changes["gpu_history"] = history[-1000:]
                if changes:
                    agent.update(job, **changes)
                time.sleep(5)
            code = process.wait()
            final_metrics = agent.capture_training_metrics(job, log_path)
            if final_metrics:
                agent.update(job, **final_metrics)
        with agent.GUARD:
            agent.PROCESS.pop(job_id, None)
        if job.get("cancel_requested"):
            agent.update(job, status="CANCELED", phase="canceled",
                         completed_at=agent.now())
            return
        if code != 0:
            raise RuntimeError(f"SmolVLA 训练进程退出码 {code}；请查看任务日志")
        checkpoint = output / "checkpoints" / f"{params['steps']:06d}" / "pretrained_model"
        if not (checkpoint / "model.safetensors").is_file():
            raise RuntimeError("SmolVLA 训练完成但没有生成 checkpoint 权重")
        agent.update(job, status="EVALUATING", phase="validation", progress=85)
        result_file = agent.JOBS / job_id / "evaluation.json"
        evaluate_command = agent.runtime_command(job_id, "evaluate", [
            "python", str(Path(__file__).with_name("training_smolvla_evaluate.py")),
            str(training_folder), f"local/{job['dataset']['dataset_id']}",
            str(checkpoint), str(result_file),
            *[str(i) for i in job["split"]["validation_episode_ids"]],
        ], use_gpu=True, model_id="smolvla")
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(evaluate_command, stdout=log,
                                       stderr=subprocess.STDOUT,
                                       start_new_session=True, cwd=agent.JOBS / job_id)
            with agent.GUARD:
                agent.PROCESS[job_id] = process
            code = process.wait()
        with agent.GUARD:
            agent.PROCESS.pop(job_id, None)
        if job.get("cancel_requested"):
            agent.update(job, status="CANCELED", phase="canceled",
                         completed_at=agent.now())
            return
        if code != 0 or not result_file.is_file():
            raise RuntimeError("SmolVLA checkpoint 加载或留出 Episode 评估失败；请查看日志")
        evaluation = json.loads(result_file.read_text(encoding="utf-8"))
        if not evaluation.get("checkpoint_smoke_test"):
            raise RuntimeError("SmolVLA checkpoint 前向冒烟测试未通过")
        agent.update(job, status="PACKAGING", phase="package", progress=95)
        try:
            artifact = package_model(agent, job, checkpoint, result_file, evaluation)
        except InterruptedError:
            agent.update(job, status="CANCELED", phase="canceled",
                         completed_at=agent.now())
            return
        agent.update(job, status="COMPLETED", phase="complete", progress=100,
                     evaluation=evaluation, artifact=artifact,
                     completed_at=agent.now())
        shutil.rmtree(work)
    except Exception as exc:  # noqa: BLE001 - worker must persist unexpected failures
        agent.update(job, status="FAILED", phase="error",
                     error=str(exc)[:2000], completed_at=agent.now())
