"""Host-side X-VLA executor. Run with the EXISTING LeRobot interpreter.

The platform talks to this process over an authenticated Unix socket in an
administrator-configured private directory. No arbitrary command or path is
accepted from HTTP.
This process does not install packages or modify the Conda environment.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import socketserver
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(os.environ["HKC_TRAINING_STORAGE"]).resolve()
MODEL = Path(os.environ["HKC_TRAINING_MODEL"]).resolve()
TOKENIZER = Path(os.environ["HKC_TRAINING_TOKENIZER"]).resolve()
PYTHON = Path(os.environ.get("HKC_TRAINING_PYTHON", sys.executable)).resolve()
SOCKET_DIR = Path(os.environ["HKC_TRAINING_SOCKET_DIR"]).resolve()
WORK_DIR = Path(os.environ.get("HKC_TRAINING_WORK_DIR", str(SOCKET_DIR / "work"))).resolve()
SOCKET = SOCKET_DIR / "agent.sock"
TOKEN_FILE = SOCKET_DIR / "token"
JOBS = ROOT / "training-runs"
MODELS = ROOT / "models"
GUARD = threading.RLock()
STATE: dict[str, dict] = {}
PROCESS: dict[str, subprocess.Popen] = {}
JOB_ID = re.compile(r"^TR\d{6,}$")
RUNTIME_ID = "lerobot"
RUNTIME_NAME = "lerobot（Conda）"
PRESETS = {
    "standard": {"steps": 2000, "batch_size": 1, "num_workers": 0, "log_freq": 20},
    "small": {"steps": 500, "batch_size": 1, "num_workers": 0, "log_freq": 10},
}
PARAMETER_SCHEMA = {
    "steps": {"min": 100, "max": 10000},
    "batch_size": {"choices": [1, 2]},
    "log_freq": {"choices": [10, 20, 50, 100]},
}
MIN_FREE_MB = int(os.environ.get("HKC_TRAINING_MIN_FREE_MB", "18000"))
MIN_FREE_WORK_GB = int(os.environ.get("HKC_TRAINING_MIN_WORK_GB", "12"))
VLM_SERVICE = os.environ.get("HKC_TRAINING_VLM_SERVICE", "").strip()
MODEL_VERSION = "xvla-base"
MODEL_STAT: tuple[int, int] | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017 - LeRobot is py310


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save(job: dict) -> None:
    folder = JOBS / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / "state.json.tmp"
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(folder / "state.json")


def purge_job(job_id: str) -> dict:
    """Delete only files owned by one terminal training job."""
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("训练任务编号无效")
    with GUARD:
        job = STATE.get(job_id)
        if job is not None and job["status"] not in {
            "COMPLETED", "FAILED", "CANCELED", "INTERRUPTED"
        }:
            raise ValueError("只能清理已结束的训练任务")
        if job_id in PROCESS:
            raise ValueError("训练进程仍在运行，不能清理")
        folders = (WORK_DIR / job_id, JOBS / job_id)
        packages = (MODELS / f"{job_id}-xvla.tar.gz",
                    MODELS / f".{job_id}-xvla.tar.gz.tmp")
        for folder in folders:
            if folder.is_symlink():
                raise ValueError("任务目录不允许是符号链接")
            if folder.exists() and not folder.is_dir():
                raise ValueError("任务路径不是目录")
        for package in packages:
            if package.is_symlink():
                raise ValueError("模型包不允许是符号链接")
            if package.exists() and not package.is_file():
                raise ValueError("模型包路径不是文件")
        for folder in folders:
            if folder.exists():
                shutil.rmtree(folder)
        for package in packages:
            if package.exists():
                package.unlink()
        STATE.pop(job_id, None)
    return {"id": job_id, "purged": True}


def update(job: dict, **changes) -> None:
    with GUARD:
        job.update(changes)
        save(job)


def gpu() -> dict | None:
    try:
        output = subprocess.check_output([
            "nvidia-smi", "--query-gpu=name,memory.free,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits"], text=True, timeout=5)
        name, free, total, utilization = [item.strip() for item in output.splitlines()[0].split(",")]
        return {"name": name, "free_gb": round(int(free) / 1024, 1),
                "total_gb": round(int(total) / 1024, 1), "free_mb": int(free),
                "used_gb": round((int(total) - int(free)) / 1024, 1),
                "utilization_percent": int(utilization)}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def ready() -> tuple[bool, str]:
    for path in [MODEL / "config.json", MODEL / "model.safetensors",
                 TOKENIZER / "tokenizer_config.json", PYTHON]:
        if not path.is_file():
            return False, f"训练依赖缺失：{path.name}"
    if not ((TOKENIZER / "tokenizer.json").is_file() or
            all((TOKENIZER / name).is_file() for name in ("vocab.json", "merges.txt"))):
        return False, "BART tokenizer 缺少词表文件"
    current = (MODEL / "model.safetensors").stat()
    if MODEL_STAT is not None and (current.st_size, current.st_mtime_ns) != MODEL_STAT:
        return False, "基座权重在执行器启动后发生变化，请重启执行器"
    return True, ""


def vlm_state() -> dict:
    if not VLM_SERVICE or not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", VLM_SERVICE):
        return {"state": "unknown", "controllable": False,
                "reason": "未配置受控 VLM 服务"}
    current = subprocess.run(["/usr/bin/systemctl", "is-active", VLM_SERVICE],
                             capture_output=True, text=True, timeout=5, check=False)
    state = "running" if current.stdout.strip() == "active" else "stopped"
    permitted = all(subprocess.run(
        ["/usr/bin/sudo", "-n", "-l", "/usr/bin/systemctl", operation, VLM_SERVICE],
        capture_output=True, text=True, timeout=5, check=False,
    ).returncode == 0 for operation in ("start", "stop"))
    return {"state": state, "controllable": permitted,
            "reason": "" if permitted else "执行器尚未获授该服务的受限启停权限"}


def control_vlm(operation: str) -> dict:
    if operation not in {"start", "stop"}:
        raise ValueError("VLM 操作不受支持")
    before = vlm_state()
    if not before["controllable"]:
        raise ValueError(before["reason"])
    if operation == "start" and any(
        job["status"] in {"PREPARING", "RUNNING", "EVALUATING", "PACKAGING"}
        for job in STATE.values()
    ):
        raise ValueError("训练正在使用 GPU，请在训练结束后恢复 VLM")
    expected = "running" if operation == "start" else "stopped"
    if before["state"] == expected:
        return before
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", operation, VLM_SERVICE],
        capture_output=True, text=True, timeout=90, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"VLM 服务{operation}失败：{(result.stderr or result.stdout)[-500:]}")
    after = vlm_state()
    if after["state"] != expected:
        raise RuntimeError("VLM 服务未达到预期状态")
    return after


def dataset_folder(snapshot: dict) -> Path:
    area = snapshot.get("storage_area")
    relative = snapshot.get("storage_relpath")
    if area not in {"raw", "derived"} or not isinstance(relative, str):
        raise ValueError("数据集快照路径无效")
    if not relative or Path(relative).name != relative or relative.startswith("."):
        raise ValueError("数据集目录名称不安全")
    folder = (ROOT / area / relative).resolve(strict=True)
    if folder.parent != (ROOT / area).resolve():
        raise ValueError("数据集目录超出授权存储区")
    info = folder / "meta" / "info.json"
    if not info.is_file() or info.is_symlink():
        raise ValueError("数据集信息文件不存在或路径不安全")
    digest = hashlib.sha256(info.read_bytes()).hexdigest()
    if digest != snapshot.get("info_sha256"):
        raise ValueError("数据集信息已变化，请重新创建任务")
    files_digest = hashlib.sha256()
    count = 0
    for file in sorted(folder.rglob("*")):
        if not file.is_file() or file.is_symlink():
            continue
        relative = file.relative_to(folder).as_posix()
        stat = file.stat()
        files_digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
        count += 1
    if count == 0 or files_digest.hexdigest() != snapshot.get("dataset_fingerprint"):
        raise ValueError("数据集快照已变化，请重新创建任务")
    return folder


def validate_parameters(preset: str, parameters: object) -> dict:
    if preset not in PRESETS or not isinstance(parameters, dict):
        raise ValueError("训练预设或参数无效")
    expected = set(PRESETS[preset])
    if set(parameters) != expected or any(type(value) is not int for value in parameters.values()):
        raise ValueError("训练参数只能包含执行器允许的整数项")
    steps = parameters["steps"]
    if not PARAMETER_SCHEMA["steps"]["min"] <= steps <= PARAMETER_SCHEMA["steps"]["max"]:
        raise ValueError("steps 超出允许范围")
    if parameters["batch_size"] not in PARAMETER_SCHEMA["batch_size"]["choices"]:
        raise ValueError("batch_size 未经执行器允许")
    if parameters["log_freq"] not in PARAMETER_SCHEMA["log_freq"]["choices"]:
        raise ValueError("指标记录间隔未经执行器允许")
    if parameters["log_freq"] > steps or parameters["num_workers"] != PRESETS[preset]["num_workers"]:
        raise ValueError("训练参数组合无效")
    return parameters


def parse_training_metrics(content: str, total_steps: int) -> list[dict]:
    """Extract only real LeRobot metric lines paired with exact progress steps."""
    points: dict[int, dict] = {}
    for line in content.splitlines():
        if "ot_train.py:" not in line or "loss:" not in line or "INFO " not in line:
            continue
        progress = re.findall(r"(\d+)\s*/\s*(\d+)", line.split("INFO ", 1)[0])
        if not progress:
            continue
        step, total = map(int, progress[-1])
        if total != total_steps or not 0 < step <= total:
            continue
        values = dict(re.findall(r"\b(loss|grdn|lr|epch|smpl):([^\s]+)", line))
        try:
            loss = float(values["loss"])
            learning_rate = float(values["lr"])
            gradient_norm = float(values["grdn"])
            epoch = float(values["epch"])
        except (KeyError, ValueError):
            continue
        if not all(math.isfinite(value) for value in
                   (loss, learning_rate, gradient_norm, epoch)):
            continue
        points[step] = {
            "step": step, "loss": loss, "learning_rate": learning_rate,
            "gradient_norm": gradient_norm, "epoch": epoch,
            "samples_label": values.get("smpl", ""),
        }
    return [points[step] for step in sorted(points)]


def capture_training_metrics(job: dict, log_path: Path) -> dict:
    try:
        with log_path.open("rb") as reader:
            reader.seek(max(0, log_path.stat().st_size - 131072))
            tail = reader.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    total = job["parameters"]["steps"]
    parsed = parse_training_metrics(tail, total)
    history = {point["step"]: point for point in job.get("metric_history") or []
               if isinstance(point, dict) and type(point.get("step")) is int}
    history.update({point["step"]: point for point in parsed})
    changes: dict = {}
    if history:
        ordered = [history[step] for step in sorted(history)][-1000:]
        latest = ordered[-1]
        changes["metric_history"] = ordered
        changes["latest_metric"] = {
            "step": latest["step"], "total_steps": total,
            "training_loss": latest["loss"],
            "learning_rate": latest.get("learning_rate"),
            "gradient_norm": latest.get("gradient_norm"),
            "epoch": latest.get("epoch"),
            "samples_label": latest.get("samples_label"),
        }
        changes["progress"] = min(80, 5 + int(75 * latest["step"] / total))
    else:
        progress = re.findall(r"(\d+)\s*/\s*(\d+)", tail)
        if progress:
            step, reported_total = map(int, progress[-1])
            if reported_total == total:
                changes["progress"] = min(80, 5 + int(75 * step / total))
                changes["latest_metric"] = {"step": step, "total_steps": total}
    return changes


def validate_payload(payload: dict) -> tuple[str, Path]:
    job_id = payload.get("id")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("任务编号无效")
    if payload.get("model_id") != "xvla":
        raise ValueError("执行器只支持 X-VLA 基座微调")
    if payload.get("model_version") != MODEL_VERSION:
        raise ValueError("基座模型版本已变化，请重新确认任务")
    if payload.get("runtime_id", RUNTIME_ID) != RUNTIME_ID:
        raise ValueError("所选 Conda 训练环境未在执行器登记")
    preset = payload.get("preset")
    validate_parameters(preset, payload.get("parameters"))
    split = payload.get("split")
    if not isinstance(split, dict):
        raise TypeError("数据划分缺失")
    train = split.get("train_episode_ids")
    validation = split.get("validation_episode_ids")
    if not isinstance(train, list) or not isinstance(validation, list) or not train or not validation:
        raise ValueError("训练/验证 Episode 划分无效")
    if any(not isinstance(i, int) or i < 0 for i in train + validation):
        raise ValueError("Episode 编号无效")
    if set(train) & set(validation):
        raise ValueError("训练与验证 Episode 重叠")
    folder = dataset_folder(payload.get("dataset") or {})
    camera_rename_map(folder)
    return job_id, folder


def camera_rename_map(folder: Path) -> dict[str, str]:
    """Map real camera names to this base model's visual slots without editing the DS.

    X-VLA masks unused views, so a two-camera dataset can use a three-view base.
    The head camera is the primary view when generic model names are required.
    """
    info = json.loads((folder / "meta" / "info.json").read_text(encoding="utf-8"))
    model_config = json.loads((MODEL / "config.json").read_text(encoding="utf-8"))
    dataset_features = info.get("features")
    model_features = model_config.get("input_features")
    if not isinstance(dataset_features, dict) or not isinstance(model_features, dict):
        raise TypeError("数据集或基座模型缺少 feature 配置")
    cameras = [key for key, feature in dataset_features.items()
               if key.startswith("observation.images.") and isinstance(feature, dict)
               and feature.get("dtype") in {"video", "image"}]
    slots = [key for key, feature in model_features.items()
             if isinstance(feature, dict) and feature.get("type") == "VISUAL"]
    if not cameras or len(cameras) > len(slots):
        raise ValueError(f"数据集有 {len(cameras)} 路相机，X-VLA 基座支持 1 至 {len(slots)} 路")
    reserved = set(cameras) & set(slots)
    available = [key for key in slots if key not in reserved]
    unmapped = sorted((key for key in cameras if key not in reserved),
                      key=lambda key: (not key.endswith(".head"), key))
    return dict(zip(unmapped, available[:len(unmapped)], strict=True))


def prepare_local_model(work_root: Path) -> Path:
    """Stage immutable base weights with an offline tokenizer config for this job.

    The source model and Conda environment are never changed. Linking the large
    weight file avoids a second multi-GB copy on the private ext4 work disk.
    """
    staged = work_root / "base-model"
    staged.mkdir(parents=True, exist_ok=False)
    for name in ("config.json", "policy_postprocessor.json"):
        shutil.copy2(MODEL / name, staged / name)
    config = json.loads((staged / "config.json").read_text(encoding="utf-8"))
    config["tokenizer_name"] = str(TOKENIZER)
    (staged / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    processor = json.loads((MODEL / "policy_preprocessor.json").read_text(encoding="utf-8"))
    tokenizer_steps = [step for step in processor.get("steps", [])
                       if step.get("registry_name") == "tokenizer_processor"]
    if len(tokenizer_steps) != 1:
        raise ValueError("基座预处理器缺少唯一的 tokenizer 配置")
    tokenizer_steps[0]["config"]["tokenizer_name"] = str(TOKENIZER)
    (staged / "policy_preprocessor.json").write_text(
        json.dumps(processor, ensure_ascii=False), encoding="utf-8")
    (staged / "model.safetensors").symlink_to(MODEL / "model.safetensors")
    return staged


def prepare_training_dataset(folder: Path, work_root: Path) -> Path:
    """Make a read-only view of DS files, correcting only legacy task metadata.

    LeRobot expects the task text to be the tasks.parquet index. Older uploaded
    files may store it as a regular column, which otherwise reaches the
    tokenizer as an integer. The registered Raw/Derived DS stays untouched.
    """
    import pandas as pd  # already supplied by the host LeRobot environment

    source_meta = folder / "meta"
    tasks_file = source_meta / "tasks.parquet"
    if not tasks_file.is_file() or tasks_file.is_symlink():
        raise ValueError("数据集缺少安全的 meta/tasks.parquet")
    tasks = pd.read_parquet(tasks_file)
    if "task_index" not in tasks.columns or tasks["task_index"].tolist() != list(range(len(tasks))):
        raise ValueError("数据集 task_index 必须与任务表行序一致并从 0 连续编号")
    if "task" in tasks.columns:
        if tasks["task"].isna().any() or not tasks["task"].map(
            lambda value: isinstance(value, str) and bool(value.strip())
        ).all():
            raise ValueError("数据集任务文本为空或格式无效")
        tasks = tasks.set_index("task")
    elif tasks.index.name != "task" or not all(
        isinstance(value, str) and value.strip() for value in tasks.index
    ):
        raise ValueError("数据集任务表缺少可用的 task 文本")
    staged = work_root / "dataset-view"
    staged.mkdir(parents=True, exist_ok=False)
    for entry in folder.iterdir():
        if entry.is_symlink():
            raise ValueError("数据集顶层不接受符号链接")
        if entry.name != "meta":
            (staged / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    staged_meta = staged / "meta"
    staged_meta.mkdir()
    for entry in source_meta.iterdir():
        if entry.is_symlink():
            raise ValueError("数据集元数据不接受符号链接")
        if entry.name != "tasks.parquet":
            (staged_meta / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    tasks.to_parquet(staged_meta / "tasks.parquet")
    return staged


def package_model(job: dict, checkpoint: Path, result_file: Path,
                  dataset_folder_path: Path, evaluation: dict) -> dict:
    """Create a self-contained, checksummed export without changing training files."""
    job_id = job["id"]
    required = [checkpoint / "config.json", checkpoint / "model.safetensors",
                TOKENIZER / "tokenizer_config.json"]
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"模型导出缺少安全的必要文件：{path.name}")
    files: list[tuple[Path, str]] = []
    for source, prefix in ((checkpoint, "pretrained_model"), (TOKENIZER, "tokenizer")):
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(f"模型导出不接受符号链接：{path.name}")
            if path.is_file():
                files.append((path, f"{prefix}/{path.relative_to(source).as_posix()}"))
    stats = dataset_folder_path / "meta" / "stats.json"
    has_stats = stats.is_file() and not stats.is_symlink()
    if has_stats:
        files.append((stats, "dataset_stats.json"))
    files.append((result_file, "evaluation.json"))
    for row in evaluation.get("action_episodes", []):
        episode_id = row.get("episode_id")
        if type(episode_id) is not int or episode_id < 0:
            raise RuntimeError("动作评估 Episode 编号无效")
        trace = result_file.parent / "action-traces" / f"episode-{episode_id}.json"
        if not trace.is_file() or trace.is_symlink():
            raise RuntimeError(f"动作评估曲线缺失：Episode {episode_id}")
        files.append((trace, f"action-traces/episode-{episode_id}.json"))
    checksums = {name: sha256_file(path) for path, name in files}
    manifest = {
        "task_id": job_id, "dataset": job["dataset"], "split": job["split"],
        "parameters": job["parameters"], "base_model_id": "xvla",
        "base_model_version": MODEL_VERSION, "evaluation": evaluation,
        "files_sha256": checksums,
        "dataset_stats": ("dataset_stats.json" if has_stats else
                          "pretrained_model/policy_preprocessor.json and processor state"),
    }
    manifest_path = JOBS / job_id / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    MODELS.mkdir(parents=True, exist_ok=True)
    package = MODELS / f"{job_id}-xvla.tar.gz"
    temporary = MODELS / f".{job_id}-xvla.tar.gz.tmp"
    try:
        with tarfile.open(temporary, "w:gz") as archive:
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
            "sha256": sha256_file(package), "size_bytes": package.stat().st_size}


def build_train_command(job: dict, folder: Path, output: Path,
                        model_path: Path | None = None) -> list[str]:
    """Build the fixed LeRobot CLI; policy overrides must use --key=value.

    LeRobot's special ``policy.path`` parser only recognizes that one-token
    form. The same form is required when it collects policy overrides after
    loading the pretrained config.
    """
    params = job["parameters"]
    rename_map = camera_rename_map(folder)
    model_path = model_path or MODEL
    command = [str(PYTHON), "-m", "lerobot.scripts.lerobot_train",
            "--dataset.repo_id", f"local/{job['dataset']['dataset_id']}",
            "--dataset.root", str(folder),
            "--dataset.episodes", json.dumps(job["split"]["train_episode_ids"]),
            f"--policy.path={model_path}",
            f"--policy.tokenizer_name={TOKENIZER}",
            "--policy.push_to_hub=false", "--output_dir", str(output),
            "--steps", str(params["steps"]), "--batch_size", str(params["batch_size"]),
            "--num_workers", str(params["num_workers"]),
            "--log_freq", str(params["log_freq"]),
            "--save_freq", str(params["steps"]), "--eval_freq", "0",
            "--wandb.enable", "false"]
    if rename_map:
        command.append(f"--rename_map={json.dumps(rename_map, separators=(',', ':'))}")
    return command


def run(job: dict) -> None:
    job_id = job["id"]
    try:
        folder = dataset_folder(job["dataset"])
        params = job["parameters"]
        output = WORK_DIR / job_id / "output"
        output.parent.mkdir(parents=True, exist_ok=False)
        staged_model = prepare_local_model(output.parent)
        training_folder = prepare_training_dataset(folder, output.parent)
        command = build_train_command(job, training_folder, output, staged_model)
        update(job, status="PREPARING", phase="starting", started_at=now())
        log_path = JOBS / job_id / "train.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write("Launching approved X-VLA base fine-tuning preset.\n")
            log.write(f"Camera rename map: {json.dumps(camera_rename_map(folder), ensure_ascii=False)}\n")
            log.flush()
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True, cwd=JOBS / job_id)
            with GUARD:
                PROCESS[job_id] = process
            update(job, status="RUNNING", phase="training", progress=5)
            while process.poll() is None:
                changes = capture_training_metrics(job, log_path)
                card = gpu()
                if card is not None:
                    history = list(job.get("gpu_history") or [])
                    history.append({"at": now(), "utilization_percent": card["utilization_percent"],
                                    "used_gb": card["used_gb"], "total_gb": card["total_gb"]})
                    changes["gpu_history"] = history[-1000:]
                if changes:
                    update(job, **changes)
                time.sleep(5)
            code = process.wait()
            final_metrics = capture_training_metrics(job, log_path)
            if final_metrics:
                update(job, **final_metrics)
        with GUARD:
            PROCESS.pop(job_id, None)
        if job.get("cancel_requested"):
            update(job, status="CANCELED", phase="canceled", completed_at=now())
            return
        if code != 0:
            raise RuntimeError(f"训练进程退出码 {code}；请查看任务日志")
        update(job, status="EVALUATING", phase="validation", progress=85)
        checkpoint = output / "checkpoints" / f"{params['steps']:06d}" / "pretrained_model"
        if not (checkpoint / "model.safetensors").is_file():
            raise RuntimeError("训练完成但没有生成 checkpoint 权重")
        result_file = JOBS / job_id / "evaluation.json"
        with (JOBS / job_id / "train.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(PYTHON), str(Path(__file__).with_name("training_evaluate.py")),
                 str(training_folder), f"local/{job['dataset']['dataset_id']}", str(checkpoint),
                 str(result_file), *[str(i) for i in job["split"]["validation_episode_ids"]]],
                stdout=log, stderr=subprocess.STDOUT, cwd=JOBS / job_id,
                start_new_session=True,
            )
            with GUARD:
                PROCESS[job_id] = process
            code = process.wait()
        with GUARD:
            PROCESS.pop(job_id, None)
        if job.get("cancel_requested"):
            update(job, status="CANCELED", phase="canceled", completed_at=now())
            return
        if code != 0 or not result_file.is_file():
            raise RuntimeError("checkpoint 加载或独立 Episode 离线评估失败；请查看日志")
        evaluation = json.loads(result_file.read_text(encoding="utf-8"))
        if not evaluation.get("checkpoint_smoke_test"):
            raise RuntimeError("模型加载与前向冒烟测试未通过")
        update(job, status="PACKAGING", phase="package", progress=95)
        try:
            artifact = package_model(job, checkpoint, result_file, folder, evaluation)
        except InterruptedError:
            update(job, status="CANCELED", phase="canceled", completed_at=now())
            return
        update(job, status="COMPLETED", phase="complete", progress=100,
               evaluation=evaluation, artifact=artifact, completed_at=now())
        try:
            shutil.rmtree(WORK_DIR / job_id)
        except OSError as exc:
            with (JOBS / job_id / "train.log").open("a", encoding="utf-8") as log:
                log.write(f"\nModel package complete; scratch cleanup deferred: {exc}\n")
    except Exception as exc:  # noqa: BLE001 - worker must persist every unexpected failure
        update(job, status="FAILED", phase="error", error=str(exc)[:2000], completed_at=now())


def scheduler() -> None:
    while True:
        with GUARD:
            queued = next((job for job in STATE.values() if job["status"] == "QUEUED"), None)
            busy = any(job["status"] in {"PREPARING", "RUNNING", "EVALUATING", "PACKAGING"}
                       for job in STATE.values())
        if queued and not busy:
            card = gpu()
            work_free_gb = shutil.disk_usage(WORK_DIR).free / (1024 ** 3)
            if card and card["free_mb"] >= MIN_FREE_MB and work_free_gb >= MIN_FREE_WORK_GB:
                run(queued)
            else:
                update(queued, phase="waiting_gpu",
                       error=(f"工作盘空间不足，需至少 {MIN_FREE_WORK_GB} GB" if
                              work_free_gb < MIN_FREE_WORK_GB else
                              f"GPU 空闲显存不足，需至少 {MIN_FREE_MB / 1024:.1f} GB"))
        time.sleep(10)


class Handler(BaseHTTPRequestHandler):
    def reply(self, code: int, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self) -> bool:
        try:
            expected = TOKEN_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            expected = ""
        import hmac
        return bool(expected) and hmac.compare_digest(self.headers.get("Authorization", ""),
                                                      "Bearer " + expected)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 65536:
            raise ValueError("请求体过大")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise TypeError("请求体必须为对象")
        return value

    def dispatch(self, method: str) -> None:
        if not self.authorized():
            self.reply(401, {"error": "unauthorized"})
            return
        try:
            parsed = urlsplit(self.path)
            parts = parsed.path.strip("/").split("/")
            if method == "GET" and parts == ["status"]:
                model_ok, reason = ready()
                card = gpu()
                if card is None:
                    model_ok, reason = False, "执行器未检测到可用 GPU"
                self.reply(200, {"id": "primary", "name": "本机 LeRobot 执行器", "online": True,
                                 "last_heartbeat": now(), "gpu": card,
                                 "queue_count": sum(job["status"] == "QUEUED" for job in STATE.values()),
                                 "runtimes": [{"id": RUNTIME_ID, "name": RUNTIME_NAME,
                                               "kind": "conda", "available": model_ok,
                                               "reason": reason, "model_ids": ["xvla"]}],
                                 "models": [{"id": "xvla", "name": "X-VLA", "version": MODEL_VERSION,
                                             "available": model_ok, "reason": reason,
                                             "presets": [{"id": key, "name": key,
                                                          "parameters": value}
                                                         for key, value in PRESETS.items()],
                                             "parameter_schema": PARAMETER_SCHEMA}],
                                 "vlm": vlm_state()})
            elif method == "POST" and len(parts) == 2 and parts[0] == "vlm":
                self.reply(200, control_vlm(parts[1]))
            elif method == "POST" and parts == ["jobs"]:
                payload = self.body()
                job_id, _ = validate_payload(payload)
                payload["runtime_id"] = RUNTIME_ID
                model_ok, reason = ready()
                if not model_ok:
                    raise ValueError(reason)
                with GUARD:
                    if job_id in STATE or (JOBS / job_id).exists():
                        raise ValueError("训练任务编号已存在")
                    payload.update(status="QUEUED", phase="queue", progress=0,
                                   created_at=now(), started_at=None, completed_at=None,
                                   error=None, latest_metric=None, metric_history=[],
                                   evaluation=None, artifact=None)
                    STATE[job_id] = payload
                    save(payload)
                self.reply(201, {"id": job_id, "status": "QUEUED"})
            elif len(parts) >= 2 and parts[0] == "jobs" and JOB_ID.fullmatch(parts[1]):
                job_id = parts[1]
                with GUARD:
                    job = STATE.get(job_id)
                if method == "DELETE" and len(parts) == 2:
                    self.reply(200, purge_job(job_id))
                elif job is None:
                    self.reply(404, {"error": "任务不存在"})
                elif method == "GET" and len(parts) == 2:
                    self.reply(200, job)
                elif method == "GET" and parts[2:] == ["logs"]:
                    log = JOBS / job_id / "train.log"
                    size = log.stat().st_size if log.exists() else 0
                    raw_before = parse_qs(parsed.query).get("before", [str(size)])[0]
                    before = min(size, max(0, int(raw_before)))
                    start = max(0, before - 20000)
                    with log.open("rb") if log.exists() else open(os.devnull, "rb") as reader:
                        reader.seek(start)
                        content = reader.read(before - start).decode("utf-8", errors="replace")
                    self.reply(200, {"text": content, "start": start,
                                     "end": before, "size": size})
                elif method == "POST" and parts[2:] == ["cancel"]:
                    if job["status"] not in {"QUEUED", "PREPARING", "RUNNING", "EVALUATING", "PACKAGING"}:
                        raise ValueError("任务不能取消")
                    update(job, cancel_requested=True)
                    process = PROCESS.get(job_id)
                    if process is not None:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    elif job["status"] == "QUEUED":
                        update(job, status="CANCELED", phase="canceled", completed_at=now())
                    self.reply(200, {"id": job_id, "status": job["status"]})
                else:
                    self.reply(404, {"error": "未知接口"})
            else:
                self.reply(404, {"error": "未知接口"})
        except (ValueError, TypeError, OSError, RuntimeError, subprocess.TimeoutExpired,
                json.JSONDecodeError) as exc:
            self.reply(400, {"error": str(exc)})

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")

    def log_message(self, _format: str, *_args) -> None:
        return


def main() -> None:
    global MODEL_STAT, MODEL_VERSION
    SOCKET_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if SOCKET_DIR.stat().st_mode & 0o077:
        raise RuntimeError("执行器通信目录权限过宽，应为 0700")
    JOBS.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if WORK_DIR.stat().st_mode & 0o077:
        raise RuntimeError("训练工作目录权限过宽，应为 0700")
    if (MODEL / "model.safetensors").is_file():
        MODEL_VERSION = "xvla-base@" + sha256_file(MODEL / "model.safetensors")[:16]
        stat = (MODEL / "model.safetensors").stat()
        MODEL_STAT = (stat.st_size, stat.st_mtime_ns)
    if not TOKEN_FILE.exists():
        descriptor = os.open(TOKEN_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(48))
    if TOKEN_FILE.is_symlink() or not TOKEN_FILE.is_file():
        raise RuntimeError("执行器令牌文件类型不安全")
    if TOKEN_FILE.stat().st_mode & 0o077:
        raise RuntimeError("执行器令牌文件权限过宽，应为 0600")
    for path in JOBS.glob("TR*/state.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if JOB_ID.fullmatch(job["id"]):
                if job["status"] in {"RUNNING", "PREPARING", "EVALUATING", "PACKAGING"}:
                    job.update(status="INTERRUPTED", phase="executor_restart",
                               error="执行器重启，中断记录已保留", completed_at=now())
                    save(job)
                if not any("learning_rate" in point for point in
                           job.get("metric_history", []) if isinstance(point, dict)):
                    log = path.parent / "train.log"
                    if log.is_file() and log.stat().st_size <= 64 * 1024 * 1024:
                        parsed = parse_training_metrics(
                            log.read_text(encoding="utf-8", errors="replace"),
                            job["parameters"]["steps"],
                        )
                        if parsed:
                            latest = parsed[-1]
                            job["metric_history"] = parsed[-1000:]
                            job["latest_metric"] = {
                                "step": latest["step"],
                                "total_steps": job["parameters"]["steps"],
                                "training_loss": latest["loss"],
                                "learning_rate": latest["learning_rate"],
                                "gradient_norm": latest["gradient_norm"],
                                "epoch": latest["epoch"],
                                "samples_label": latest["samples_label"],
                            }
                            save(job)
                STATE[job["id"]] = job
        except (OSError, ValueError, KeyError):
            continue
    if SOCKET.exists():
        if SOCKET.is_socket():
            SOCKET.unlink()
        else:
            raise RuntimeError("执行器 socket 路径已被非 socket 文件占用")
    threading.Thread(target=scheduler, daemon=True).start()
    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
    with Server(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
