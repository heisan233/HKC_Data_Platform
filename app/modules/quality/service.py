"""Quality plan validation, execution orchestration, and unified reports."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from fastapi import HTTPException

from app.db.datasets import DatasetRepository
from app.db.quality import QualityRepository
from app.modules.datasets import service as datasets
from app.modules.quality.catalog import DOCTOR_IDS, ROBOT_RULE_TYPES, VLM_RULE_TYPES, default_config

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hkc-quality")
_start_lock = Lock()
_initialized_roots: set[Path] = set()
_logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def initialize(root: Path) -> QualityRepository:
    repo = QualityRepository(root)
    with _start_lock:
        if root not in _initialized_roots:
            interrupted = repo.interrupt_running()
            _initialized_roots.add(root)
            for task_id in interrupted:
                task = repo.get_task(task_id)
                report = read_report(root, task) if task else None
                if report is not None:
                    report.update(status="INTERRUPTED", outcome="REVIEW",
                                  error=task["error"], completed_at=task["completed_at"])
                    try:
                        save_report(root, task, report)
                    except Exception:
                        _logger.exception("无法写入中断的质检报告：%s", task_id)
            for task_id in repo.queued_task_ids():
                _submit_task(root, task_id)
        else:
            repo.initialize()
    return repo


def _fail_unhandled(root: Path, task_id: str, exc: Exception) -> None:
    """Persist failures that occur before the normal task error handler starts."""
    _logger.exception("质检执行器异常：%s", task_id, exc_info=exc)
    try:
        repo = QualityRepository(root)
        task = repo.get_task(task_id)
        if task is None or task["status"] in {"COMPLETED", "FAILED", "INTERRUPTED"}:
            return
        error = f"质检执行器异常：{type(exc).__name__}: {exc}"[:2000]
        completed_at = utc_now()
        report = read_report(root, task)
        if report is not None:
            report.update(status="FAILED", outcome="REVIEW", error=error,
                          completed_at=completed_at)
            try:
                save_report(root, task, report)
            except Exception:
                _logger.exception("无法写入质检失败报告：%s", task_id)
        repo.set_task(task_id, status="FAILED", phase="error", error=error,
                      completed_at=completed_at)
    except Exception:
        _logger.exception("无法记录质检执行器异常：%s", task_id)


def _execute_task(root: Path, task_id: str) -> None:
    try:
        run_task(root, task_id)
    except Exception as exc:  # noqa: BLE001 - background futures are otherwise unobserved
        _fail_unhandled(root, task_id, exc)


def _submit_task(root: Path, task_id: str) -> None:
    try:
        _executor.submit(_execute_task, root, task_id)
    except Exception as exc:  # noqa: BLE001 - e.g. executor already shut down
        _fail_unhandled(root, task_id, exc)


def _text(value: object, label: str, limit: int = 100) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise HTTPException(400, f"{label}须为 1–{limit} 个字符")
    return value.strip()


def _number(value: object, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(400, f"{label}必须是数字")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise HTTPException(400, f"{label}须在 {low}–{high} 之间")
    return result


def _unique_rule_ids(rows: object, label: str) -> list[dict]:
    if not isinstance(rows, list) or len(rows) > 50:
        raise HTTPException(400, f"{label}规则列表无效或超过 50 项")
    seen = set()
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(400, f"{label}规则格式无效")
        rule_id = _text(row.get("id"), "规则 ID", 64)
        if not all(char.isalnum() or char in "_-" for char in rule_id) or rule_id in seen:
            raise HTTPException(400, f"{label}规则 ID 不合法或重复")
        seen.add(rule_id)
        result.append(row)
    return result


def validate_config(config: object, robot_type: str) -> dict:
    """Reject unsupported engines/parameters before a plan can be published."""
    if not isinstance(config, dict):
        raise HTTPException(400, "质检方案配置无效")
    robot = config.get("robot")
    if not isinstance(robot, dict) or robot.get("robot_type") != robot_type:
        raise HTTPException(400, "机器人类型与质检方案不一致")
    for key in ("state_key", "action_key"):
        _text(robot.get(key), key, 100)
    for key in ("state_dim", "action_dim"):
        value = robot.get(key)
        if value is not None:
            _number(value, key, 1, 10000)
    expected_fps = robot.get("expected_fps")
    if expected_fps is not None:
        _number(expected_fps, "预期 FPS", 0.1, 500)
    video_keys = robot.get("video_keys")
    if not isinstance(video_keys, list) or len(video_keys) > 20 or any(
        not isinstance(key, str) or not 1 <= len(key) <= 100 or
        "/" in key or "\\" in key or "\x00" in key for key in video_keys
    ) or len(set(video_keys)) != len(video_keys):
        raise HTTPException(400, "相机字段列表无效")
    limits = robot.get("joint_limits", {})
    if not isinstance(limits, dict) or len(limits) > 100:
        raise HTTPException(400, "关节限位无效")
    for name, pair in limits.items():
        _text(name, "关节名称", 100)
        if not isinstance(pair, list) or len(pair) != 2:
            raise HTTPException(400, "关节限位应为 [最小值, 最大值]")
        lower = _number(pair[0], "关节下限", -1e9, 1e9)
        upper = _number(pair[1], "关节上限", -1e9, 1e9)
        if lower >= upper:
            raise HTTPException(400, "关节下限必须小于上限")

    doctor = config.get("doctor")
    if not isinstance(doctor, dict):
        raise HTTPException(400, "Doctor 配置无效")
    checks = _unique_rule_ids(doctor.get("checks"), "Doctor")
    if any(row["id"] not in DOCTOR_IDS or not isinstance(row.get("enabled"), bool) for row in checks):
        raise HTTPException(400, "Doctor 包含不支持的检查项或启停值")
    if doctor.get("max_episodes") is not None:
        _number(doctor["max_episodes"], "Doctor 抽样数", 1, 100000)

    robot_rules = _unique_rule_ids(config.get("robot_rules"), "机器人专项")
    for row in robot_rules:
        kind = row.get("kind")
        if kind not in ROBOT_RULE_TYPES:
            raise HTTPException(400, f"不支持的机器人规则：{kind}")
        _text(row.get("name"), "规则名称")
        if not isinstance(row.get("enabled"), bool) or row.get("severity") not in {"WARN", "FAIL"}:
            raise HTTPException(400, "机器人规则的启停或严重度无效")
        params = row.get("params")
        if not isinstance(params, dict):
            raise HTTPException(400, "机器人规则参数无效")
        allowed = ROBOT_RULE_TYPES[kind]["params"]
        if set(params) != set(allowed):
            raise HTTPException(400, f"{row['name']} 参数应为 {', '.join(allowed) or '空'}")
        for key, value in params.items():
            if key == "field":
                if value not in {"state", "action"}:
                    raise HTTPException(400, "关节限位字段只能为 state 或 action")
            else:
                _number(value, key, 0, 1e9)

    vlm = config.get("vlm")
    if not isinstance(vlm, dict):
        raise HTTPException(400, "VLM 配置无效")
    _text(vlm.get("model"), "VLM 模型名", 100)
    _number(vlm.get("timeout_seconds"), "VLM 超时", 5, 600)
    _number(vlm.get("max_tokens"), "VLM 输出 token", 32, 2048)
    if vlm.get("max_episodes") is not None:
        _number(vlm["max_episodes"], "VLM 抽样数", 1, 100000)
    vlm_rules = _unique_rule_ids(vlm.get("rules"), "VLM")
    for row in vlm_rules:
        if row.get("kind") not in VLM_RULE_TYPES:
            raise HTTPException(400, "不支持的 VLM 规则类型")
        _text(row.get("name"), "VLM 规则名称")
        _text(row.get("prompt"), "VLM 判断要求", 1500)
        if not isinstance(row.get("enabled"), bool) or not isinstance(row.get("camera_key"), str):
            raise HTTPException(400, "VLM 规则启停或相机字段无效")
        if row["camera_key"] and row["camera_key"] not in video_keys:
            raise HTTPException(400, "VLM 规则选择了未配置的相机")
    return deepcopy(config)


def plan_from_dataset(root: Path, dataset_id: str, vlm_available: bool) -> dict:
    try:
        _, folder = datasets.resolve_registered_dataset(root, dataset_id)
    except ValueError as exc:
        raise HTTPException(404, "数据集未登记") from exc

    info, error = datasets.dataset_info(folder)
    if error:
        raise HTTPException(409, error)
    config = default_config(info, vlm_enabled=vlm_available)
    robot_type = info["robot_type"].strip()
    return initialize(root).create_plan(f"{robot_type} 标准质检", robot_type, config)


def resolved_snapshot(config: dict, selections: dict | None) -> dict:
    result = deepcopy(config)
    if selections is None:
        return result
    groups = {
        "doctor": result["doctor"]["checks"],
        "robot_rules": result["robot_rules"],
        "vlm": result["vlm"]["rules"],
    }
    if set(selections) != set(groups):
        raise HTTPException(400, "须提交 Doctor、机器人专项和 VLM 的启用项")
    for group, rows in groups.items():
        ids = selections[group]
        if not isinstance(ids, list) or len(ids) != len(set(ids)) or any(
            not isinstance(item, str) for item in ids
        ):
            raise HTTPException(400, f"{group} 启用项无效")
        known = {row["id"] for row in rows}
        if not set(ids).issubset(known):
            raise HTTPException(400, f"{group} 包含未知规则")
        for row in rows:
            row["enabled"] = row["id"] in ids
    return result


def raw_fingerprint(folder: Path) -> str:
    digest = hashlib.sha256()
    count = 0
    for file in sorted(folder.rglob("*")):
        if not file.is_file() or file.is_symlink():
            continue
        relative = file.relative_to(folder).as_posix()
        if datasets.safe_file(folder, relative) is None:
            continue
        stat = file.stat()
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
        count += 1
    if count == 0:
        raise HTTPException(409, "数据集目录中没有可读取的文件")
    return digest.hexdigest()


def report_path(root: Path, task: dict) -> Path:
    return root / "reports" / "qc" / task["dataset_id"] / f"{task['id']}.json"


def save_report(root: Path, task: dict, report: dict) -> None:
    destination = report_path(root, task)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)


def read_report(root: Path, task: dict) -> dict | None:
    path = report_path(root, task)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def create_task(root: Path, dataset_id: str, plan_id: str, plan_version: int,
                selections: dict | None) -> dict:
    repo = initialize(root)
    dataset = DatasetRepository(root).get(dataset_id)
    plan = repo.get_plan(plan_id, plan_version)
    if dataset is None or plan is None:
        raise HTTPException(404, "数据集或质检方案不存在")
    if plan["archived_at"]:
        raise HTTPException(409, "质检方案已删除，请选择其他方案")
    try:
        _, folder = datasets.resolve_registered_dataset(root, dataset_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    info, error = datasets.dataset_info(folder)
    if error:
        raise HTTPException(409, error)
    if info["robot_type"].strip() != plan["robot_type"]:
        raise HTTPException(409, "数据集 robot_type 与质检方案不匹配")
    if datasets.info_digest(folder) != dataset["info_sha256"]:
        raise HTTPException(409, "数据集元数据已变化，请在数据管理中核对并重新扫描")
    snapshot = resolved_snapshot(plan["config"], selections)
    if not any(row["enabled"] for row in [
        *snapshot["doctor"]["checks"], *snapshot["robot_rules"], *snapshot["vlm"]["rules"]
    ]):
        raise HTTPException(400, "至少启用一项检查")
    task = repo.create_task(dataset_id, plan, snapshot, raw_fingerprint(folder))
    save_report(root, task, {
        "schema_version": 1, "task_id": task["id"], "dataset_id": dataset_id,
        "plan_id": plan_id, "plan_version": plan["version"],
        "storage_area": dataset["storage_area"],
        "storage_relpath": dataset["storage_relpath"],
        "raw_fingerprint": task["raw_fingerprint"],
        "snapshot": snapshot, "status": "QUEUED", "outcome": None,
        "created_at": task["created_at"],
        "sections": {key: {"status": "PENDING", "results": []}
                     for key in ("doctor", "robot_rules", "vlm")},
    })
    _submit_task(root, task["id"])
    return task


def create_tasks(root: Path, dataset_ids: list[str], plan_id: str, plan_version: int,
                 selections: dict | None) -> dict:
    """Create one independent task per dataset, retaining per-dataset failures."""
    if not 1 <= len(dataset_ids) <= 20 or len(dataset_ids) != len(set(dataset_ids)):
        raise HTTPException(400, "每批请选择 1–20 个不重复的数据集")
    if any(not isinstance(item, str) or not item.strip() for item in dataset_ids):
        raise HTTPException(400, "数据集编号无效")
    created, errors = [], []
    for dataset_id in dataset_ids:
        try:
            created.append(create_task(root, dataset_id, plan_id, plan_version, selections))
        except HTTPException as exc:
            errors.append({"dataset_id": dataset_id, "message": str(exc.detail)})
    return {"created": created, "errors": errors}


def rerun_task(root: Path, previous_id: str) -> dict:
    repo = initialize(root)
    previous = repo.get_task(previous_id)
    if previous is None:
        raise HTTPException(404, "原质检任务不存在")
    dataset = DatasetRepository(root).get(previous["dataset_id"])
    plan = repo.get_plan(previous["plan_id"], previous["plan_version"])
    if dataset is None or plan is None:
        raise HTTPException(409, "原数据集或质检方案版本缺失")
    try:
        _, folder = datasets.resolve_registered_dataset(root, previous["dataset_id"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if raw_fingerprint(folder) != previous["raw_fingerprint"]:
        raise HTTPException(409, "数据集文件已变化，请选择当前方案新建质检任务")
    task = repo.create_task(previous["dataset_id"], plan, previous["snapshot"],
                            previous["raw_fingerprint"])
    save_report(root, task, {
        "schema_version": 1, "task_id": task["id"], "dataset_id": task["dataset_id"],
        "plan_id": plan["id"], "plan_version": plan["version"],
        "storage_area": dataset["storage_area"],
        "storage_relpath": dataset["storage_relpath"],
        "raw_fingerprint": task["raw_fingerprint"],
        "snapshot": task["snapshot"], "status": "QUEUED", "outcome": None,
        "created_at": task["created_at"], "rerun_of": previous_id,
        "sections": {key: {"status": "PENDING", "results": []}
                     for key in ("doctor", "robot_rules", "vlm")},
    })
    _submit_task(root, task["id"])
    return task


def review_task(root: Path, task_id: str, decision: str, reason: str) -> dict:
    if decision not in {"PASS", "REVIEW", "RECAPTURE"}:
        raise HTTPException(400, "不支持的人工判定")
    reason = reason.strip()
    if decision == "RECAPTURE" and not reason:
        raise HTTPException(400, "打回重采必须填写原因")
    if len(reason) > 1000:
        raise HTTPException(400, "复核理由不能超过 1000 字")
    repo = initialize(root)
    existing = repo.get_task(task_id)
    if existing is None or existing["status"] != "COMPLETED":
        raise HTTPException(409, "只能复核已完成的任务")
    if read_report(root, existing) is None:
        raise HTTPException(409, "报告文件不存在，不能复核；请重新质检")
    if decision == "PASS" and existing["outcome"] != "PASS" and not reason:
        raise HTTPException(400, "覆盖系统结果为通过时必须填写复核理由")
    task = repo.review(task_id, decision, reason)
    if task is None:
        raise HTTPException(409, "只能复核已完成的任务")
    report = read_report(root, task)
    if report is not None:
        report["decision"] = decision
        report["reviews"] = repo.reviews(task_id)
        save_report(root, task, report)
    return task


def _doctor(folder: Path, snapshot: dict, timeout: int) -> dict:
    checks = [row["id"] for row in snapshot["doctor"]["checks"] if row["enabled"]]
    if not checks:
        return {"status": "SKIPPED", "results": [], "reason": "本次未启用 Doctor 检查"}
    command = [sys.executable, "-m", "lerobot_doctor.cli", "check", str(folder),
               "--checks", ",".join(checks), "--json"]
    maximum = snapshot["doctor"].get("max_episodes")
    if maximum:
        command.extend(["--max-episodes", str(int(maximum))])
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout,
                                check=False, env={**os.environ, "HF_HUB_OFFLINE": "1"})
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "results": [], "reason": "Doctor 执行超时"}
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return {"status": "ERROR", "results": [],
                "reason": (result.stderr or result.stdout or "Doctor 未输出有效 JSON")[-2000:]}
    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), list):
        return {"status": "ERROR", "results": [], "reason": "Doctor JSON 结构无效"}
    rows = payload["checks"]
    status = payload.get("overall_severity", "ERROR")
    if status not in {"PASS", "WARN", "FAIL"} or result.returncode not in (0, 1):
        status = "ERROR"
    total_episodes = payload.get("total_episodes")
    sampled = bool(maximum and isinstance(total_episodes, int) and maximum < total_episodes)
    return {"status": status, "version": payload.get("version"),
            "results": rows, "summary": payload.get("summary", {}),
            "sampled": sampled, "checked_limit": maximum,
            "reason": result.stderr[-1000:] if result.returncode not in (0, 1) else ""}


def _outcome(report: dict) -> str:
    sections = report["sections"]
    statuses = [section["status"] for section in sections.values() if section["status"] != "SKIPPED"]
    if not statuses:
        return "PARTIAL"
    if "FAIL" in statuses:
        return "FAIL"
    if any(status in {"WARN", "REVIEW", "ERROR"} for status in statuses):
        return "REVIEW"
    if any(row["status"] == "SKIPPED" and row.get("reason") != "本次未启用"
           for row in sections["robot_rules"].get("results", [])):
        return "PARTIAL"
    if sections["doctor"].get("sampled") or sections["vlm"].get("sampled"):
        return "PARTIAL"
    return "PASS"


def run_task(root: Path, task_id: str) -> None:
    """Runs in the local worker thread; every stage is reflected in one durable report."""
    repo = QualityRepository(root)
    task = repo.get_task(task_id)
    if task is None:
        return
    report = read_report(root, task)
    if report is None:
        repo.set_task(task_id, status="FAILED", phase="report", error="质检报告不可读取",
                      completed_at=utc_now())
        return
    if not repo.claim_task(task_id):
        return
    try:
        from app.core.config import settings
        from app.modules.quality.robot_rules import run_robot_rules
        from app.modules.quality.vlm import run_vlm_rules

        report["status"] = "RUNNING"
        save_report(root, task, report)
        try:
            _, folder = datasets.resolve_registered_dataset(root, task["dataset_id"])
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        if raw_fingerprint(folder) != task["raw_fingerprint"]:
            raise RuntimeError("数据集在任务启动后发生变化")

        report["sections"]["doctor"] = _doctor(
            folder, task["snapshot"], settings.doctor_timeout_seconds
        )
        repo.set_task(task_id, phase="robot_rules")
        save_report(root, task, report)
        report["sections"]["robot_rules"] = run_robot_rules(folder, task["snapshot"])
        repo.set_task(task_id, phase="vlm")
        save_report(root, task, report)
        report["sections"]["vlm"] = run_vlm_rules(
            root, task_id, folder, task["snapshot"], settings.vlm_base_url,
            settings.vlm_api_token,
        )
        report["outcome"] = _outcome(report)
        report["status"] = "COMPLETED"
        report["completed_at"] = utc_now()
        save_report(root, task, report)
        repo.set_task(task_id, status="COMPLETED", phase="done",
                      outcome=report["outcome"], completed_at=report["completed_at"])
    except Exception as exc:  # noqa: BLE001 - persist unexpected worker failures
        report["status"] = "FAILED"
        report["outcome"] = "REVIEW"
        report["error"] = str(exc)[:2000]
        report["completed_at"] = utc_now()
        save_report(root, task, report)
        repo.set_task(task_id, status="FAILED", phase="error", error=report["error"],
                      completed_at=report["completed_at"])
