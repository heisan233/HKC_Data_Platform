"""Authenticated host-side LaWAM Docker executor over a private Unix socket.

Only fixed training and LIBERO evaluation operations are accepted. The browser
never receives Docker access or host paths, and model/data mounts stay in the
separate LaWAM Compose service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import socketserver
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

STORAGE = Path(os.environ["HKC_LAWAM_STORAGE"]).resolve()
RUNS = STORAGE / "training-runs" / "lawam"
COMPOSE = Path(os.environ["HKC_LAWAM_COMPOSE"]).resolve()
MODEL_ROOT = COMPOSE.parent.parent
SOCKET_DIR = Path(os.environ["HKC_LAWAM_SOCKET_DIR"]).resolve()
SOCKET = SOCKET_DIR / "agent.sock"
TOKEN = SOCKET_DIR / "token"
GOAL_EPISODES = Path(os.environ.get("HKC_LAWAM_GOAL_EPISODES", "")).resolve()
GPU_ID = os.environ.get("HKC_LAWAM_GPU_ID", "0")
VLM_CONTAINER = os.environ.get("HKC_LAWAM_VLM_CONTAINER", "").strip()
IMAGE = "hkc-lawam-train:lerobot-0.6.2-cu130"
DOCKER = shutil.which("docker") or "/usr/bin/docker"
GUARD = threading.RLock()
TASKS: dict[str, dict] = {}
ACTIVE = {"QUEUED", "RUNNING"}
TASK_ID = re.compile(r"^WAM[0-9A-F]{12}$")
LOSS = re.compile(r"loss_total:([0-9.]+)")
METRIC = re.compile(r"\b([a-z_]+):([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)")
METRIC_KEYS = frozenset({"loss_total", "loss_flow", "loss_perceptual", "loss_distill",
                         "loss_vlm", "grdn", "lr", "step_s", "mem_gb"})
PROGRESS = re.compile(r"Training:\s+(\d+)%\|[^|]*\|\s*(\d+)/(\d+)")
EVAL_RESULT = re.compile(r"\[[^\]]+\]\s+\w+\s+t\d+.*?:\s*(\d+)/(\d+)\s+\((\d+)%\)")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017 - host is py310


def save(task: dict) -> None:
    folder = RUNS / task["id"]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "state.json"
    temporary = folder / "state.json.tmp"
    temporary.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def docker(*args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run([DOCKER, *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


def gpu() -> dict | None:
    try:
        output = subprocess.check_output([
            "nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits", "-i", GPU_ID,
        ], text=True, timeout=5).splitlines()[0]
        name, total, used, free = [part.strip() for part in output.split(",")]
        return {"name": name, "total_mb": int(total), "used_mb": int(used),
                "free_mb": int(free)}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def status() -> dict:
    problems = []
    if not COMPOSE.is_file():
        problems.append("LaWAM Compose 文件不存在")
    if not (MODEL_ROOT / "weights/lawam-pretrain/config.json").is_file():
        problems.append("LaWAM 基座权重不存在")
    if docker("image", "inspect", IMAGE).returncode != 0:
        problems.append("LaWAM Docker 镜像不可用")
    card = gpu()
    if card is None:
        problems.append("GPU 不可用")
    return {"online": not problems, "reason": "；".join(problems), "gpu": card,
            "image": IMAGE, "output": "storage/training-runs/lawam/",
            "queue_count": sum(t["status"] == "QUEUED" for t in TASKS.values())}


def bases() -> list[dict]:
    """Training model choices; checkpoints are exposed only for evaluation."""
    pretrain = MODEL_ROOT / "weights/lawam-pretrain"
    if (pretrain / "config.json").is_file():
        return [{"id": "/models/lawam-pretrain", "label": "LaWAM 预训练基座"}]
    return []


def checkpoints(train_id: str) -> list[dict]:
    parent = TASKS.get(train_id)
    if parent is None or parent["kind"] != "train":
        raise ValueError("训练任务不存在")
    folder = RUNS / train_id / "out/checkpoints"
    if not folder.is_dir() or folder.is_symlink():
        return []
    rows = []
    for path in sorted(folder.glob("*/pretrained_model"), reverse=True):
        if (path.parent.is_symlink() or path.is_symlink() or not path.is_dir()
                or (path / "config.json").is_symlink()
                or not (path / "config.json").is_file()):
            continue
        checkpoint = path.parent.name
        if checkpoint != "last" and not re.fullmatch(r"\d{6}", checkpoint):
            continue
        label = "最后保存" if checkpoint == "last" else f"第 {int(checkpoint)} 步"
        rows.append({"id": f"/data/training-runs/lawam/{train_id}/out/checkpoints/"
                           f"{checkpoint}/pretrained_model", "label": label})
    return rows


def positive_int(data: dict, key: str, low: int, high: int) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{key} 必须在 {low} 到 {high} 之间")
    return value


def selected_dataset(snapshot: dict) -> Path:
    dataset_id = snapshot.get("dataset_id")
    if not isinstance(dataset_id, str) or not re.fullmatch(r"DS[0-9]{6,}", dataset_id):
        raise ValueError("数据集编号无效")
    data_root = STORAGE / "data"
    folder = data_root / dataset_id
    if data_root.is_symlink() or folder.is_symlink() or not folder.is_dir():
        raise ValueError("平台数据集目录不可用或路径不安全")
    if folder.resolve().parent != data_root.resolve():
        raise ValueError("平台数据集目录不安全")
    info_path = folder / "meta" / "info.json"
    if (info_path.parent.is_symlink() or info_path.is_symlink()
            or not info_path.is_file()):
        raise ValueError("数据集元数据不可读取")
    expected_info = snapshot.get("info_sha256")
    if not isinstance(expected_info, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_info):
        raise ValueError("数据集元数据摘要无效")
    if hashlib.sha256(info_path.read_bytes()).hexdigest() != expected_info:
        raise ValueError("数据集元数据已变化，请重新质检")
    digest = hashlib.sha256()
    count = 0
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("数据集包含符号链接")
        if not path.is_file():
            continue
        stat = path.stat()
        digest.update(f"{path.relative_to(folder).as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
        count += 1
    if not count or digest.hexdigest() != snapshot.get("dataset_fingerprint"):
        raise ValueError("数据集文件已变化，请重新质检")
    return folder


def train_payload(data: dict) -> dict:
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("任务名称应为 1 到 80 个字符")
    available = {item["id"] for item in bases()}
    base = data.get("base")
    if base not in available:
        raise ValueError("起点权重不在可用列表中")
    snapshot = data.get("snapshot")
    if not isinstance(snapshot, dict) or data.get("dataset_id") != snapshot.get("dataset_id"):
        raise ValueError("缺少所选数据集快照")
    folder = selected_dataset(snapshot)
    mode = data.get("dataset_mode")
    if mode not in {"all", "goal", "custom"}:
        raise ValueError("数据范围无效")
    episodes = []
    if mode == "custom":
        raw = str(data.get("episodes", ""))
        if not re.fullmatch(r"\d+(\s*,\s*\d+)*", raw) or len(raw) > 10000:
            raise ValueError("自定义 episode 格式无效")
        episodes = [int(item.strip()) for item in raw.split(",")]
    elif mode == "goal":
        try:
            episodes = json.loads(GOAL_EPISODES.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("LIBERO goal episode 列表不可用") from exc
        if not episodes:
            raise ValueError("LIBERO goal episode 列表为空")
    if episodes and (len(episodes) > 2000 or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0 or item > 1000000
            for item in episodes)):
        raise ValueError("episode 列表超出允许范围")
    info = json.loads((folder / "meta" / "info.json").read_text(encoding="utf-8"))
    total_episodes = info.get("total_episodes")
    if (not isinstance(total_episodes, int) or total_episodes < 1 or
            any(item >= total_episodes for item in episodes)):
        raise ValueError("所选 Episode 超出数据集范围")
    steps = positive_int(data, "steps", 1, 200000)
    warmup = positive_int(data, "warmup", 0, steps - 1)
    save_freq = positive_int(data, "save_freq", 1, 200000)
    lr = data.get("lr")
    if isinstance(lr, bool) or not isinstance(lr, (int, float)) or not 0 < lr <= 1:
        raise ValueError("学习率无效")
    result = {"name": name, "base": base, "dataset_id": snapshot["dataset_id"],
              "snapshot": snapshot, "dataset_mode": mode,
              "episodes": episodes, "steps": steps, "warmup": warmup,
              "save_freq": save_freq, "lr": float(lr),
              "batch": positive_int(data, "batch", 1, 64),
              "action_horizon": positive_int(data, "action_horizon", 1, 128),
              "unfreeze_llm_layers": positive_int(data, "unfreeze_llm_layers", 0, 128)}
    for key in ("freeze_vision", "freeze_llm", "stop_vllm"):
        if not isinstance(data.get(key), bool):
            raise TypeError(f"{key} 必须是布尔值")
        result[key] = data[key]
    return result


def eval_payload(data: dict) -> dict:
    train_id = data.get("train_id")
    if not isinstance(train_id, str) or not TASK_ID.fullmatch(train_id):
        raise ValueError("训练任务编号无效")
    parent = TASKS.get(train_id)
    if parent is None or parent["kind"] != "train" or parent.get("deleted_at"):
        raise ValueError("训练任务不存在")
    checkpoint = data.get("checkpoint")
    if checkpoint not in {item["id"] for item in checkpoints(train_id)}:
        raise ValueError("评测检查点不属于所选训练任务")
    suite = data.get("task")
    if suite not in {"libero_object", "libero_spatial", "libero_goal", "libero_10"}:
        raise ValueError("LIBERO 任务无效")
    text = data.get("task_text")
    if text not in {"auto", "none"}:
        raise ValueError("语言指令选项无效")
    return {"train_id": train_id, "checkpoint": checkpoint, "task": suite,
            "task_id": positive_int(data, "task_id", 0, 99),
            "episodes": positive_int(data, "episodes", 1, 100),
            "replan": positive_int(data, "replan", 1, 128), "task_text": text}


def command(task: dict) -> list[str]:
    task_id = task["id"]
    p = task["parameters"]
    if task["kind"] == "train":
        cmd = ["python", "-m", "lerobot.scripts.lerobot_train",
               f"--policy.path={p['base']}",
               f"--dataset.repo_id=local/{p['dataset_id']}",
               "--dataset.root=/datasets/selected", "--dataset.image_transforms.enable=false",
               f"--policy.freeze_vision_backbone={str(p['freeze_vision']).lower()}",
               f"--policy.freeze_llm_backbone={str(p['freeze_llm']).lower()}",
               f"--policy.unfreeze_llm_last_n_layers={p['unfreeze_llm_layers']}",
               "--policy.freeze_embedding=true",
               f"--policy.action_horizon={p['action_horizon']}",
               f"--policy.n_action_steps={p['action_horizon']}",
               "--policy.clip_normalized_actions=true",
               "--policy.pre_snap_gripper_action=true",
               "--policy.binarize_gripper_action=true",
               f"--policy.scheduler_warmup_steps={p['warmup']}",
               f"--policy.scheduler_decay_steps={p['steps']}",
               f"--policy.optimizer_lr={p['lr']}",
               f"--policy.repo_id=local/lawam-{task_id}",
               "--policy.push_to_hub=false", "--policy.device=cuda",
               "--policy.dtype=bfloat16",
               f"--output_dir=/data/training-runs/lawam/{task_id}/out",
               f"--steps={p['steps']}", f"--batch_size={p['batch']}",
               "--num_workers=8", f"--save_freq={p['save_freq']}",
               f"--log_freq={min(100, p['steps'])}", "--wandb.enable=false"]
        if p["episodes"]:
            cmd.append(f"--dataset.episodes={json.dumps(p['episodes'])}")
        return cmd
    return ["python", "/opt/wam-tools/eval_tricks2.py", "--policy", p["checkpoint"],
            "--task", p["task"], "--task_id", str(p["task_id"]),
            "--episodes", str(p["episodes"]), "--max_steps", "400", "--fps", "10",
            "--trick", "grip", "--task_text", p["task_text"],
            "--replan", str(p["replan"]), "--print_task", "--record",
            f"/data/training-runs/lawam/{p['train_id']}/eval/{task_id}"]


def log_info(task: dict) -> dict:
    path = RUNS / task["id"] / "run.log"
    result = {"progress": 0, "step": 0, "loss": None, "result": None, "tail": ""}
    if not path.is_file():
        return result
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - 128 * 1024))
        content = handle.read().decode("utf-8", errors="replace")
    result["tail"] = "\n".join(content.splitlines()[-40:])
    if task["kind"] == "train":
        matches = PROGRESS.findall(content)
        if matches:
            result["progress"], result["step"] = int(matches[-1][0]), int(matches[-1][1])
        losses = LOSS.findall(content)
        if losses:
            result["loss"] = float(losses[-1])
    else:
        matches = EVAL_RESULT.findall(content)
        if matches:
            passed, total, percent = matches[-1]
            result["result"] = {"passed": int(passed), "total": int(total),
                                "success_percent": int(percent)}
    return result


def metric_points(task: dict) -> list[dict]:
    """Incrementally index complete training log lines for chart display."""
    if task["kind"] != "train":
        return []
    folder = RUNS / task["id"]
    log_path = folder / "run.log"
    cache_path = folder / "metrics.json"
    with GUARD:
        cached = {"offset": 0, "points": []}
        if cache_path.is_file() and not cache_path.is_symlink():
            try:
                if cache_path.stat().st_size <= 2 * 1024 * 1024:
                    candidate = json.loads(cache_path.read_text(encoding="utf-8"))
                    if (isinstance(candidate, dict) and isinstance(candidate.get("offset"), int)
                            and isinstance(candidate.get("points"), list)):
                        cached = candidate
            except (OSError, ValueError):
                pass
        if not log_path.is_file() or log_path.is_symlink():
            return cached["points"]
        size = log_path.stat().st_size
        if cached["offset"] < 0 or cached["offset"] > size:
            cached = {"offset": 0, "points": []}
        old_offset = cached["offset"]
        with log_path.open("rb") as handle:
            handle.seek(old_offset)
            while True:
                line = handle.readline()
                if not line or not line.endswith(b"\n"):
                    break
                for fragment in line.decode("utf-8", errors="replace").splitlines():
                    values = {key: float(value) for key, value in METRIC.findall(fragment)
                              if key in METRIC_KEYS and math.isfinite(float(value))}
                    step_match = re.search(r"\bstep:(\d+)\b", fragment)
                    if step_match is None or "loss_total" not in values:
                        continue
                    step = int(step_match.group(1))
                    if not 0 <= step <= 200000:
                        continue
                    point = {"step": step, **values}
                    points = cached["points"]
                    if points and points[-1]["step"] == step:
                        points[-1] = point
                    elif not points or step > points[-1]["step"]:
                        points.append(point)
                cached["offset"] = handle.tell()
        if cached["offset"] != old_offset:
            temporary = folder / "metrics.json.tmp"
            temporary.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            temporary.replace(cache_path)
        return cached["points"]


def public(task: dict) -> dict:
    return {**task, **log_info(task)}


def execute(task: dict) -> None:
    task_id = task["id"]
    try:
        dataset_folder = (selected_dataset(task["parameters"]["snapshot"])
                          if task["kind"] == "train" else MODEL_ROOT / "datasets/libero")
        if task["kind"] == "train" and task["parameters"]["stop_vllm"] and VLM_CONTAINER:
            running = docker("inspect", "--format", "{{.State.Running}}", VLM_CONTAINER)
            if running.returncode == 0 and running.stdout.strip() == "true":
                stopped = docker("stop", VLM_CONTAINER, timeout=90)
                if stopped.returncode != 0:
                    raise RuntimeError("无法停止配置的 vLLM 容器")
                time.sleep(5)
        card = gpu()
        need = 40000 if task["kind"] == "train" and task["parameters"]["batch"] >= 32 else 28000
        if card is None or card["free_mb"] < need:
            with GUARD:
                task["reason"] = f"等待 GPU 空闲显存达到 {need} MB"
                save(task)
            return
        with GUARD:
            task.update(status="RUNNING", reason="", started_at=now())
            save(task)
        env = os.environ.copy()
        env["LAWAM_OUTPUT_ROOT"] = str(RUNS)
        env["LAWAM_GPU_ID"] = GPU_ID
        env["LAWAM_DATASET_ROOT"] = str(dataset_folder)
        container = "hkc-lawam-" + task_id.lower()
        invocation = [DOCKER, "compose", "-f", str(COMPOSE), "--profile", "manual",
                      "run", "--rm", "--no-deps", "--name", container,
                      "lawam", *command(task)]
        log_path = RUNS / task_id / "run.log"
        with log_path.open("ab") as log:
            process = subprocess.Popen(invocation, stdout=log, stderr=subprocess.STDOUT,
                                       env=env, start_new_session=True)
            returncode = process.wait()
        with GUARD:
            task.update(status="CANCELED" if task.get("cancel_requested") else
                        "COMPLETED" if returncode == 0 else "FAILED",
                        exit_code=returncode, completed_at=now())
            save(task)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        with GUARD:
            task.update(status="FAILED", reason=str(exc)[:1000], completed_at=now())
            save(task)


def scheduler() -> None:
    while True:
        with GUARD:
            busy = any(t["status"] == "RUNNING" for t in TASKS.values())
            queued = next((t for t in TASKS.values() if t["status"] == "QUEUED"), None)
        if queued and not busy:
            execute(queued)
        time.sleep(5)


class Handler(BaseHTTPRequestHandler):
    def reply(self, code: int, data: object) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > 16384:
            raise ValueError("请求体大小无效")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise TypeError("请求体必须是 JSON 对象")
        return value

    def dispatch(self, method: str) -> None:
        try:
            expected = TOKEN.read_text(encoding="utf-8").strip()
        except OSError:
            expected = ""
        if not expected or not hmac.compare_digest(self.headers.get("Authorization", ""),
                                                   "Bearer " + expected):
            self.reply(401, {"error": "unauthorized"})
            return
        try:
            parts = urlsplit(self.path).path.strip("/").split("/")
            if method == "GET" and parts == ["status"]:
                self.reply(200, status())
            elif method == "GET" and parts == ["bases"]:
                self.reply(200, bases())
            elif method == "GET" and len(parts) == 3 and parts[0] == "jobs" and TASK_ID.fullmatch(parts[1]) and parts[2] == "checkpoints":
                self.reply(200, checkpoints(parts[1]))
            elif method == "GET" and len(parts) == 3 and parts[0] == "jobs" and TASK_ID.fullmatch(parts[1]) and parts[2] == "metrics":
                task = TASKS.get(parts[1])
                if task is None or task["kind"] != "train":
                    self.reply(404, {"error": "任务不存在"})
                else:
                    self.reply(200, metric_points(task))
            elif method == "GET" and parts == ["jobs"]:
                self.reply(200, [public(t) for t in reversed(list(TASKS.values()))
                                 if t["kind"] == "train" and not t.get("deleted_at")])
            elif method == "GET" and parts == ["deleted-jobs"]:
                self.reply(200, [public(t) for t in reversed(list(TASKS.values()))
                                 if t["kind"] == "train" and t.get("deleted_at")])
            elif method == "GET" and parts == ["evaluations"]:
                self.reply(200, [public(t) for t in reversed(list(TASKS.values()))
                                 if t["kind"] == "evaluation"])
            elif method == "POST" and parts == ["jobs"]:
                if not status()["online"]:
                    raise ValueError("LaWAM 运行环境不可用")
                parameters = train_payload(self.body())
                task_id = "WAM" + uuid4().hex[:12].upper()
                task = {"id": task_id, "kind": "train", "name": parameters["name"],
                        "parameters": parameters, "status": "QUEUED", "reason": "",
                        "created_at": now(), "started_at": None, "completed_at": None}
                with GUARD:
                    TASKS[task_id] = task
                    save(task)
                self.reply(201, public(task))
            elif method == "POST" and parts == ["evaluations"]:
                parameters = eval_payload(self.body())
                task_id = "WAM" + uuid4().hex[:12].upper()
                task = {"id": task_id, "kind": "evaluation", "name": parameters["task"],
                        "parameters": parameters, "status": "QUEUED", "reason": "",
                        "created_at": now(), "started_at": None, "completed_at": None}
                with GUARD:
                    TASKS[task_id] = task
                    save(task)
                self.reply(201, public(task))
            elif len(parts) >= 2 and parts[0] in {"jobs", "evaluations"} and TASK_ID.fullmatch(parts[1]):
                task = TASKS.get(parts[1])
                if task is None or task["kind"] != (
                    "train" if parts[0] == "jobs" else "evaluation"
                ):
                    self.reply(404, {"error": "任务不存在"})
                elif method == "GET" and len(parts) == 2:
                    self.reply(200, public(task))
                elif method == "GET" and parts[2:] == ["logs"]:
                    self.reply(200, {"text": log_info(task)["tail"]})
                elif method == "POST" and parts[2:] == ["cancel"]:
                    with GUARD:
                        if task["status"] not in ACTIVE:
                            raise ValueError("任务不能取消")
                        task["cancel_requested"] = True
                        if task["status"] == "QUEUED":
                            task.update(status="CANCELED", completed_at=now())
                        save(task)
                    if task["status"] == "RUNNING":
                        docker("stop", "--time", "5", "hkc-lawam-" + task["id"].lower(),
                               timeout=20)
                    self.reply(200, public(task))
                elif method == "DELETE" and len(parts) == 2 and task["kind"] == "train":
                    with GUARD:
                        if not task.get("deleted_at"):
                            task["deleted_at"] = now()
                            if task["status"] in ACTIVE:
                                task["cancel_requested"] = True
                                if task["status"] == "QUEUED":
                                    task.update(status="CANCELED", completed_at=now())
                            save(task)
                    if task["status"] == "RUNNING":
                        docker("stop", "--time", "5", "hkc-lawam-" + task["id"].lower(),
                               timeout=20)
                    self.reply(200, public(task))
                elif method == "POST" and parts[2:] == ["restore"] and task["kind"] == "train":
                    with GUARD:
                        if not task.get("deleted_at"):
                            raise ValueError("任务不在回收站")
                        task["deleted_at"] = None
                        save(task)
                    self.reply(200, public(task))
                elif method == "POST" and parts[2:] == ["purge"] and task["kind"] == "train":
                    if self.headers.get("X-HKC-LaWAM-Purge") != task["id"]:
                        raise ValueError("请确认要永久删除的任务编号")
                    with GUARD:
                        if not task.get("deleted_at"):
                            raise ValueError("请先将任务移入回收站")
                        related = [item for item in TASKS.values()
                                   if item["kind"] == "evaluation" and
                                   item["parameters"]["train_id"] == task["id"]]
                        if task["status"] in ACTIVE or any(item["status"] in ACTIVE
                                                           for item in related):
                            raise ValueError("训练或关联评测仍在运行，请等待结束后再永久删除")
                        for item in [*related, task]:
                            folder = RUNS / item["id"]
                            if folder.is_symlink():
                                raise ValueError("任务目录不是普通目录，无法永久删除")
                        for item in [*related, task]:
                            folder = RUNS / item["id"]
                            if folder.is_dir():
                                shutil.rmtree(folder)
                            TASKS.pop(item["id"], None)
                    self.reply(200, {"id": task["id"], "purged": True,
                                     "evaluation_count": len(related)})
                else:
                    self.reply(404, {"error": "未知接口"})
            else:
                self.reply(404, {"error": "未知接口"})
        except (ValueError, TypeError, OSError, subprocess.SubprocessError,
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
    SOCKET_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if SOCKET_DIR.stat().st_mode & 0o077:
        raise RuntimeError("LaWAM socket 目录权限必须是 0700")
    RUNS.mkdir(parents=True, exist_ok=True)
    if not TOKEN.exists():
        descriptor = os.open(TOKEN, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(48))
    if TOKEN.is_symlink() or TOKEN.stat().st_mode & 0o077:
        raise RuntimeError("LaWAM token 类型或权限不安全")
    for path in RUNS.glob("WAM*/state.json"):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
            if not TASK_ID.fullmatch(task["id"]):
                continue
            if task["status"] == "RUNNING":
                task.update(status="INTERRUPTED", reason="执行器重启", completed_at=now())
                save(task)
            TASKS[task["id"]] = task
        except (OSError, ValueError, KeyError):
            continue
    if SOCKET.exists():
        if not SOCKET.is_socket():
            raise RuntimeError("LaWAM socket 路径被其他文件占用")
        SOCKET.unlink()
    threading.Thread(target=scheduler, daemon=True).start()

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    with Server(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
