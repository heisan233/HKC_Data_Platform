"""Training catalog, lightweight preflight, and executor orchestration.

The platform never imports CUDA or invokes a shell. A separately configured
executor owns model files, GPU processes, and any allow-listed VLM service
control. Missing executor capability is reported, never simulated.
"""

from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path
from threading import Lock

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from app.db.datasets import DatasetRepository
from app.db.quality import QualityRepository
from app.db.training import TrainingRepository
from app.modules.datasets import service as datasets
from app.modules.datasets.lerobot_v3 import parquet_files
from app.modules.quality.service import raw_fingerprint
from app.modules.quality.status import attach_status

ACTIVE_STATUSES = frozenset({"QUEUED", "PREPARING", "RUNNING", "EVALUATING", "PACKAGING"})
FINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELED", "INTERRUPTED"})
ALL_STATUSES = ACTIVE_STATUSES | FINAL_STATUSES
_vlm_control_lock = Lock()
_task_purge_lock = Lock()


class TrainingError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _runner_config(root: Path) -> tuple[str, str, str] | None:
    configured_socket = os.getenv("HKC_TRAINING_RUNNER_SOCKET", "")
    configured_token = os.getenv("HKC_TRAINING_RUNNER_TOKEN_FILE", "")
    socket = Path(configured_socket) if configured_socket else root / "system" / "training-agent.sock"
    token_path = (Path(configured_token) if configured_token else
                  datasets.safe_file(root, "system/training-agent.token"))
    if (socket.exists() and socket.is_socket() and not socket.is_symlink() and
            token_path is not None and token_path.is_file() and not token_path.is_symlink()):
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return "unix", str(socket), token
    url = os.getenv("HKC_TRAINING_RUNNER_URL", "").rstrip("/")
    token = os.getenv("HKC_TRAINING_RUNNER_TOKEN", "")
    if not url or not token or not url.startswith(("http://", "https://")):
        return None
    return "http", url, token


def _runner_request(root: Path, method: str, path: str, payload: dict | None = None) -> dict:
    config = _runner_config(root)
    if config is None:
        raise TrainingError("训练执行器未配置地址或鉴权令牌", 503)
    kind, address, token = config
    try:
        transport = httpx.HTTPTransport(uds=address) if kind == "unix" else None
        base_url = "http://training-agent" if kind == "unix" else address
        timeout = 300 if method == "DELETE" and path.startswith("/jobs/") else (
            110 if path.startswith("/vlm/") else 8)
        with httpx.Client(base_url=base_url, transport=transport, timeout=timeout, trust_env=False,
                          headers={"Authorization": f"Bearer {token}"}) as client:
            response = client.request(method, path, json=payload)
            if response.is_error:
                try:
                    error = response.json().get("error", "")
                except ValueError:
                    error = ""
                raise TrainingError(str(error or f"执行器返回 HTTP {response.status_code}"),
                                    409 if response.status_code < 500 else 503)
            result = response.json()
    except TrainingError:
        raise
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise TrainingError("训练执行器未响应或返回无效结果", 503) from exc
    if not isinstance(result, dict):
        raise TrainingError("训练执行器返回格式无效", 502)
    return result


def environment(root: Path) -> dict:
    if _runner_config(root) is None:
        return {
            "id": "primary", "name": "训练执行器", "online": False,
            "reason": "执行器尚未配置", "models": [], "gpu": None,
            "runtimes": [{"id": "lerobot", "name": "lerobot（Conda）", "kind": "conda",
                          "available": False, "reason": "执行器尚未配置",
                          "model_ids": ["xvla"]}],
            "queue_count": 0, "vlm": {"state": "unknown", "controllable": False},
        }
    try:
        status = _runner_request(root, "GET", "/status")
    except TrainingError as exc:
        return {
            "id": "primary", "name": "训练执行器", "online": False,
            "reason": exc.message, "models": [], "gpu": None,
            "runtimes": [{"id": "lerobot", "name": "lerobot（Conda）", "kind": "conda",
                          "available": False, "reason": exc.message,
                          "model_ids": ["xvla"]}],
            "queue_count": 0, "vlm": {"state": "unknown", "controllable": False},
        }
    return {
        "id": str(status.get("id") or "primary"),
        "name": str(status.get("name") or "训练执行器"),
        "online": status.get("online") is True,
        "reason": str(status.get("reason") or ""),
        "last_heartbeat": status.get("last_heartbeat"),
        "models": status.get("models") if isinstance(status.get("models"), list) else [],
        "runtimes": status.get("runtimes") if isinstance(
            status.get("runtimes"), list) else [],
        "gpu": status.get("gpu") if isinstance(status.get("gpu"), dict) else None,
        "queue_count": int(status.get("queue_count") or 0),
        "vlm": {**(status.get("vlm") if isinstance(status.get("vlm"), dict)
                   else {"state": "unknown", "controllable": False}),
                "qc_paused": QualityRepository(root).vlm_paused()},
    }


def models(root: Path) -> list[dict]:
    env = environment(root)
    installed = [row for row in env["models"] if isinstance(row, dict)]
    if installed:
        return [
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or row.get("id") or "未命名模型"),
                "version": str(row.get("version") or ""),
                "available": env["online"] and row.get("available") is True,
                "reason": str(row.get("reason") or env["reason"] or ""),
                "presets": row.get("presets") if isinstance(row.get("presets"), list) else [],
                "parameter_schema": row.get("parameter_schema") if isinstance(
                    row.get("parameter_schema"), dict) else {},
            }
            for row in installed if row.get("id")
        ]
    return [{
        "id": "xvla", "name": "X-VLA", "version": "",
        "available": False, "reason": env["reason"] or "执行器尚未登记基座模型",
        "presets": [], "parameter_schema": {},
    }]


def registered_datasets(root: Path) -> list[dict]:
    rows = DatasetRepository(root).list()
    by_id = {row["id"]: row for row in rows}

    def lineage(row: dict, seen: set[str] | None = None) -> list[str]:
        seen = set(seen or ())
        result = []
        for source in row["source_dataset_ids"]:
            if source in seen:
                continue
            seen.add(source)
            parent = by_id.get(source)
            if parent is not None:
                result.extend(lineage(parent, seen))
            result.append(source)
        return list(dict.fromkeys(result))

    summaries = [datasets.summary(root, row["storage_relpath"], row,
                                  row["storage_area"]) for row in rows]
    attach_status(root, summaries)
    for item, row in zip(summaries, rows, strict=True):
        item["source_dataset_ids"] = row["source_dataset_ids"]
        item["lineage"] = [*lineage(row), row["id"]]
        folder = datasets.directory(root, row["storage_area"], row["storage_relpath"])
        item["annotation_status"] = (
            "有平台标注记录" if folder is not None and
            (folder / "meta" / "annotations" / "episodes.parquet").is_file()
            else "未发现平台标注记录"
        )
    return summaries


def _episode_indexes(folder: Path) -> list[int]:
    paths = parquet_files(folder / "meta" / "episodes")
    if not paths:
        raise ValueError("缺少 LeRobot v3 Episode 元数据")
    indexes: set[int] = set()
    try:
        for path in paths:
            safe = datasets.safe_file(folder, str(path.relative_to(folder)))
            if safe is None:
                raise ValueError("Episode 元数据路径不安全")
            if "episode_index" not in pq.read_schema(safe).names:
                raise ValueError("Episode 元数据缺少 episode_index")
            column = pq.read_table(safe, columns=["episode_index"])["episode_index"]
            for value in column.to_pylist():
                indexes.add(int(value))
    except (OSError, pa.ArrowException, TypeError, OverflowError) as exc:
        raise ValueError("Episode 元数据无法读取") from exc
    return sorted(indexes)


def _sample_action(folder: Path) -> None:
    paths = parquet_files(folder / "data")
    if not paths:
        raise ValueError("缺少 LeRobot v3 帧数据")
    path = datasets.safe_file(folder, str(paths[0].relative_to(folder)))
    if path is None:
        raise ValueError("帧数据路径不安全")
    try:
        reader = pq.ParquetFile(path)
        if "action" not in reader.schema_arrow.names:
            raise ValueError("缺少 X-VLA 微调需要的 action 字段")
        sample = next(reader.iter_batches(batch_size=1, columns=["action"]), None)
        if sample is None or sample.num_rows == 0:
            raise ValueError("帧数据为空")
    except (OSError, pa.ArrowException) as exc:
        raise ValueError("无法抽样读取 action 帧数据") from exc


def preflight(root: Path, dataset_id: str, model_id: str = "xvla") -> dict:
    if model_id != "xvla":
        raise TrainingError("该模型尚未接入训前校验", 422)
    try:
        record, folder = datasets.resolve_registered_dataset(root, dataset_id)
    except ValueError as exc:
        raise TrainingError(str(exc), 404) from exc
    item = datasets.summary(root, record["storage_relpath"], record,
                            record["storage_area"])
    attach_status(root, [item])
    issues: list[str] = []
    if item["state"] != "ready":
        issues.append(item["error"] or "登记后的数据集已变化或不可读取")
    if item["qc_state"] != "passed":
        issues.append("选中的最终 DS 尚无有效的质检通过结论")
    try:
        fingerprint = raw_fingerprint(folder)
    except (OSError, ValueError) as exc:
        fingerprint = ""
        issues.append(f"数据集快照无法读取：{exc}")
    info, error = datasets.dataset_info(folder)
    if error:
        issues.append(error)
    episodes: list[int] = []
    if info:
        if not isinstance(info.get("total_frames"), int) or info["total_frames"] < 1:
            issues.append("数据集没有可训练的数据帧")
        try:
            episodes = _episode_indexes(folder)
            if len(episodes) < 2:
                issues.append("至少需要 2 个 Episode 才能划分训练集和验证集")
            if info.get("total_episodes") != len(episodes):
                issues.append("Episode 数量与 meta/info.json 不一致")
        except ValueError as exc:
            issues.append(str(exc))
        try:
            _sample_action(folder)
        except ValueError as exc:
            issues.append(str(exc))
    digest = record["info_sha256"]
    seed = int(hashlib.sha256(f"{dataset_id}:{digest}".encode()).hexdigest()[:8], 16)
    shuffled = episodes.copy()
    random.Random(seed).shuffle(shuffled)
    validation_count = max(1, round(len(episodes) * 0.1)) if len(episodes) >= 2 else 0
    split = {
        "seed": seed,
        "train_episode_ids": sorted(shuffled[validation_count:]),
        "validation_episode_ids": sorted(shuffled[:validation_count]),
    }
    return {
        "dataset_id": dataset_id,
        "model_id": model_id,
        "passed": not issues,
        "issues": issues,
        "dataset": {
            "id": dataset_id, "name": record["name"],
            "storage_area": record["storage_area"],
            "source_dataset_ids": record["source_dataset_ids"],
            "qc_state": item["qc_state"], "qc_label": item["qc_label"],
            "qc_task_id": item["qc_task_id"],
            "episodes": len(episodes), "frames": info.get("total_frames") if info else 0,
        },
        "snapshot": {
            "dataset_id": dataset_id, "storage_area": record["storage_area"],
            "storage_relpath": record["storage_relpath"],
            "info_sha256": digest, "qc_task_id": item["qc_task_id"],
            "dataset_fingerprint": fingerprint,
            "source_dataset_ids": record["source_dataset_ids"],
        },
        "split": split,
    }


def _model_for_submission(root: Path, model_id: str, preset: str,
                          executor_id: str, runtime_id: str,
                          overrides: dict | None = None) -> tuple[dict, dict, dict]:
    env = environment(root)
    if not env["online"] or env["id"] != executor_id:
        raise TrainingError(env["reason"] or "所选训练执行器离线", 503)
    runtime = next((row for row in env.get("runtimes", [])
                    if isinstance(row, dict) and row.get("id") == runtime_id), None)
    if runtime is None:
        raise TrainingError("所选 Conda 训练环境未登记", 422)
    if runtime.get("available") is not True:
        raise TrainingError(str(runtime.get("reason") or "所选 Conda 训练环境不可用"), 409)
    if model_id not in runtime.get("model_ids", []):
        raise TrainingError("所选 Conda 训练环境不支持此模型", 422)
    model = next((row for row in models(root) if row["id"] == model_id), None)
    if model is None or not model["available"]:
        raise TrainingError(model["reason"] if model else "模型不可用", 409)
    selected = next((row for row in model["presets"]
                     if isinstance(row, dict) and row.get("id") == preset), None)
    if selected is None:
        raise TrainingError("训练预设不可用", 422)
    base = selected.get("parameters")
    if not isinstance(base, dict):
        raise TrainingError("执行器未提供有效训练参数", 502)
    params = dict(base)
    if overrides:
        schema = model["parameter_schema"]
        if set(overrides) - set(schema):
            raise TrainingError("包含执行器未开放的训练参数", 422)
        for key, value in overrides.items():
            rule = schema[key]
            if not isinstance(rule, dict) or type(value) is not int:
                raise TrainingError(f"{key} 必须为整数", 422)
            if ("choices" in rule and value not in rule["choices"] or
                    "min" in rule and value < rule["min"] or
                    "max" in rule and value > rule["max"]):
                raise TrainingError(f"{key} 超出执行器允许范围", 422)
            params[key] = value
        if params.get("log_freq", 0) > params.get("steps", 0):
            raise TrainingError("指标记录间隔不能大于训练步数", 422)
    return model, params, runtime


def create_task(root: Path, *, name: str, dataset_id: str, model_id: str,
                preset: str, executor_id: str, parameters: dict | None = None,
                runtime_id: str = "lerobot",
                retry_of: str | None = None) -> dict:
    clean_name = name.strip()
    if not 1 <= len(clean_name) <= 100:
        raise TrainingError("任务名称须为 1–100 个字符", 422)
    checked = preflight(root, dataset_id, model_id)
    if not checked["passed"]:
        raise TrainingError("；".join(checked["issues"]), 409)
    model, params, _runtime = _model_for_submission(
        root, model_id, preset, executor_id, runtime_id, parameters)
    task = TrainingRepository(root).create(
        name=clean_name, dataset_id=dataset_id, model_id=model_id,
        model_version=model["version"], preset=preset, executor_id=executor_id,
        runtime_id=runtime_id,
        snapshot=checked["snapshot"], split=checked["split"],
        parameters=params, retry_of=retry_of,
    )
    try:
        _runner_request(root, "POST", "/jobs", {
            "id": task["id"], "name": clean_name,
            "model_id": model_id, "model_version": model["version"],
            "dataset": checked["snapshot"], "split": checked["split"],
            "preset": preset, "parameters": params, "runtime_id": runtime_id,
        })
    except TrainingError as exc:
        TrainingRepository(root).update(task["id"], status="FAILED", phase="queue",
                                        error=f"提交执行器失败：{exc.message}")
        raise TrainingError(f"任务 {task['id']} 已记录，但提交执行器失败：{exc.message}", 503) from exc
    return task


def _verified_artifact(root: Path, artifact: object) -> dict:
    if not isinstance(artifact, dict):
        raise TrainingError("执行器未返回模型包清单")
    relative = artifact.get("package_relpath")
    digest = artifact.get("sha256")
    if not isinstance(relative, str) or not relative.startswith("models/"):
        raise TrainingError("模型包路径不在 models/ 内")
    if not isinstance(digest, str) or len(digest) != 64:
        raise TrainingError("模型包缺少 SHA-256 校验值")
    path = datasets.safe_file(root, relative)
    if path is None:
        raise TrainingError("模型包不存在或路径不安全")
    with path.open("rb") as package:
        actual = hashlib.file_digest(package, "sha256").hexdigest()
    if actual != digest:
        raise TrainingError("模型包文件校验失败")
    return {"package_relpath": relative, "sha256": digest, "size_bytes": path.stat().st_size}


def sync_task(root: Path, task: dict) -> dict:
    if task["status"] not in ACTIVE_STATUSES:
        if task.get("delete_requested_at") and task["status"] in FINAL_STATUSES \
                and task["deleted_at"] is None:
            return TrainingRepository(root).archive(
                task["id"], task["delete_requested_by"] or "system") or task
        return task
    try:
        state = _runner_request(root, "GET", f"/jobs/{task['id']}")
    except TrainingError:
        return task
    status = state.get("status")
    if status not in ALL_STATUSES:
        return task
    try:
        progress = min(100, max(0, int(state.get("progress") or 0)))
    except (TypeError, ValueError):
        progress = task["progress"]
    updates: dict = {
        "status": status,
        "phase": str(state.get("phase") or task["phase"])[:80],
        "progress": progress,
        "error": str(state.get("error") or "")[:2000] or None,
    }
    if isinstance(state.get("latest_metric"), dict):
        updates["latest_metric"] = state["latest_metric"]
    if state.get("started_at"):
        updates["started_at"] = str(state["started_at"])
    if status in FINAL_STATUSES:
        updates["completed_at"] = str(state.get("completed_at") or "") or task["updated_at"]
    if status == "COMPLETED":
        evaluation = state.get("evaluation")
        try:
            if not isinstance(evaluation, dict) or not evaluation.get("checkpoint_smoke_test"):
                raise TrainingError("模型加载与推理冒烟测试尚未通过")
            updates["artifact"] = _verified_artifact(root, state.get("artifact"))
            updates["evaluation"] = evaluation
        except TrainingError as exc:
            updates.update(status="FAILED", phase="artifact_check", error=exc.message)
    updated = TrainingRepository(root).update(task["id"], **updates)
    if updated["status"] in FINAL_STATUSES and updated.get("delete_requested_at"):
        return TrainingRepository(root).archive(
            task["id"], updated["delete_requested_by"] or "system") or updated
    return updated


def get_task(root: Path, task_id: str) -> dict:
    task = TrainingRepository(root).get(task_id)
    if task is None or task["deleted_at"] is not None:
        raise TrainingError("训练任务不存在", 404)
    task = sync_task(root, task)
    if task["deleted_at"] is not None:
        raise TrainingError("训练任务已移入回收站", 404)
    try:
        state = _runner_request(root, "GET", f"/jobs/{task_id}")
        task["telemetry_online"] = True
        task["metric_history"] = state.get("metric_history") if isinstance(
            state.get("metric_history"), list) else []
        task["gpu_history"] = state.get("gpu_history") if isinstance(
            state.get("gpu_history"), list) else []
        if isinstance(state.get("latest_metric"), dict):
            task["latest_metric"] = state["latest_metric"]
        task["gpu"] = environment(root)["gpu"]
        logs = get_task_logs(root, task_id)
        task["logs"] = logs["text"]
        task["log_start"] = logs["start"]
        task["log_size"] = logs["size"]
    except TrainingError:
        task["telemetry_online"] = False
        task["metric_history"] = []
        task["gpu_history"] = []
        task["gpu"] = None
        task["logs"] = "执行器暂不可连接，日志暂不可读"
        task["log_start"] = 0
        task["log_size"] = 0
    return task


def get_task_logs(root: Path, task_id: str, before: int | None = None) -> dict:
    task = TrainingRepository(root).get(task_id)
    if task is None or task["deleted_at"] is not None:
        raise TrainingError("训练任务不存在", 404)
    if before is not None and before < 0:
        raise TrainingError("日志位置无效", 422)
    suffix = f"?before={before}" if before is not None else ""
    result = _runner_request(root, "GET", f"/jobs/{task_id}/logs{suffix}")
    return {"text": str(result.get("text") or ""),
            "start": int(result.get("start") or 0),
            "end": int(result.get("end") or 0),
            "size": int(result.get("size") or 0)}


def list_tasks(root: Path, **filters) -> list[dict]:
    tasks = TrainingRepository(root).list(**filters)
    synced = [sync_task(root, task) for task in tasks[:100]]
    if filters.get("deleted", False) is not None:
        return [task for task in synced if bool(task["deleted_at"]) == filters.get("deleted", False)]
    return synced


def delete_task(root: Path, task_id: str, username: str) -> dict:
    repo = TrainingRepository(root)
    task = repo.get(task_id)
    if task is None:
        raise TrainingError("训练任务不存在", 404)
    if task["deleted_at"] is not None:
        raise TrainingError("训练任务已在回收站")
    task = sync_task(root, task)
    if task["deleted_at"] is not None:
        return task
    if task["status"] in ACTIVE_STATUSES:
        if task.get("delete_requested_at"):
            return task
        try:
            _runner_request(root, "POST", f"/jobs/{task_id}/cancel", {})
        except TrainingError:
            # The job may have finished between the status read and cancellation.
            task = sync_task(root, task)
            if task["status"] not in FINAL_STATUSES:
                raise
        else:
            task = repo.request_delete(task_id, username)
            if task is None:
                task = repo.get(task_id)
            task = sync_task(root, task)
            if task["deleted_at"] is not None or task["status"] in ACTIVE_STATUSES:
                return task
    archived = repo.archive(task_id, username)
    if archived is None:
        raise TrainingError("任务状态已变化，请刷新后重试")
    return archived


def restore_task(root: Path, task_id: str) -> dict:
    with _task_purge_lock:
        repo = TrainingRepository(root)
        task = repo.get(task_id)
        if task is None:
            raise TrainingError("训练任务不存在", 404)
        if task["deleted_at"] is None:
            raise TrainingError("训练任务不在回收站")
        restored = repo.restore(task_id)
        if restored is None:
            raise TrainingError("任务状态已变化，请刷新后重试")
        return restored


def purge_task(root: Path, task_id: str) -> dict:
    """Permanently remove an archived task and its executor-owned output."""
    with _task_purge_lock:
        repo = TrainingRepository(root)
        task = repo.get(task_id)
        if task is None:
            raise TrainingError("训练任务不存在", 404)
        if task["deleted_at"] is None:
            raise TrainingError("只能永久删除回收站中的任务")
        if task["status"] not in FINAL_STATUSES:
            raise TrainingError("任务尚未结束，不能永久删除")
        # Executor owns the private scratch directory and model package. Keep the
        # SQLite record recoverable if cleanup fails or the executor is offline.
        _runner_request(root, "DELETE", f"/jobs/{task_id}")
        if not repo.purge(task_id):
            raise TrainingError("任务状态已变化，请刷新后重试")
        return {"id": task_id, "purged": True}


def cancel_task(root: Path, task_id: str) -> dict:
    task = get_task(root, task_id)
    if task["status"] not in ACTIVE_STATUSES:
        raise TrainingError("只有排队或正在执行的任务可取消")
    _runner_request(root, "POST", f"/jobs/{task_id}/cancel", {})
    return TrainingRepository(root).update(task_id, phase="cancel_requested")


def retry_task(root: Path, task_id: str, name: str | None = None) -> dict:
    task = get_task(root, task_id)
    if task["status"] not in {"FAILED", "CANCELED", "INTERRUPTED"}:
        raise TrainingError("只有失败、取消或中断的任务可重试")
    return create_task(root, name=name or task["name"], dataset_id=task["dataset_id"],
                       model_id=task["model_id"], preset=task["preset"],
                       executor_id=task["executor_id"],
                       runtime_id=task.get("runtime_id") or "lerobot",
                       parameters={key: task["parameters"][key]
                                   for key in ("steps", "batch_size", "log_freq")
                                   if key in task["parameters"]},
                       retry_of=task_id)


def model_versions(root: Path) -> list[dict]:
    for task in TrainingRepository(root).list(deleted=None):
        if task["status"] in ACTIVE_STATUSES:
            sync_task(root, task)
    return [{**task, "version_id": f"MV{task['seq']:06d}"}
            for task in TrainingRepository(root).list(status="COMPLETED", deleted=None)
            if task["artifact"] and task["evaluation"]]


def model_package(root: Path, task_id: str) -> Path:
    task = TrainingRepository(root).get(task_id)
    if task is None or task["status"] != "COMPLETED" or not task["artifact"]:
        raise TrainingError("此任务没有通过校验的模型包", 404)
    verified = _verified_artifact(root, task["artifact"])
    return datasets.safe_file(root, verified["package_relpath"])


def is_operator(username: str) -> bool:
    allowed = {item.strip() for item in os.getenv("HKC_TRAINING_OPERATORS", "").split(",")
               if item.strip()}
    return username in allowed


def vlm_control(root: Path, username: str, operation: str) -> dict:
    if not is_operator(username):
        raise TrainingError("仅配置的训练操作员可控制 VLM 服务", 403)
    if operation not in {"stop", "start"}:
        raise TrainingError("不支持的 VLM 操作", 422)
    state = environment(root)
    if not state["online"] or state["vlm"].get("controllable") is not True:
        raise TrainingError("执行器未提供受控 VLM 服务启停能力", 503)
    from app.modules.quality import service as quality_service

    repo = QualityRepository(root)
    with _vlm_control_lock:
        if operation == "stop":
            was_paused = repo.vlm_paused()
            active = repo.pause_vlm()
            if active:
                if not was_paused:
                    repo.resume_vlm()
                raise TrainingError("仍有使用 VLM 的质检任务在运行，请等待完成：" +
                                    "、".join(active[:5]), 409)
            try:
                return _runner_request(root, "POST", "/vlm/stop", {})
            except TrainingError:
                # A timeout can occur after systemd has already stopped VLM.
                # Keep the gate closed unless the runner confirms it is still running.
                try:
                    current = _runner_request(root, "GET", "/status")
                    still_running = current.get("vlm", {}).get("state") == "running"
                except TrainingError:
                    still_running = False
                if still_running and not was_paused:
                    repo.resume_vlm()
                raise
        result = _runner_request(root, "POST", "/vlm/start", {})
        repo.resume_vlm()
        quality_service.resume_queued_tasks(root)
        return result
