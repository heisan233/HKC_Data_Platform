"""Read-only aggregation for the platform dashboard."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.db.datasets import DatasetRepository
from app.db.quality import QualityRepository
from app.db.training import TrainingRepository
from app.modules.datasets import service as datasets
from app.modules.quality.status import LABELS as QC_LABELS
from app.modules.robots import service as robots

ACTIVE_TRAINING_STATUSES = frozenset(
    {"QUEUED", "PREPARING", "RUNNING", "EVALUATING", "PACKAGING"}
)
TRAINING_LABELS = {
    "QUEUED": "等待执行",
    "PREPARING": "准备中",
    "RUNNING": "训练中",
    "EVALUATING": "评估中",
    "PACKAGING": "打包中",
    "COMPLETED": "已完成",
    "FAILED": "失败",
    "CANCELED": "已取消",
    "INTERRUPTED": "已中断",
}


def _bytes_label(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


def _qc_state(item: dict, task: dict | None) -> str:
    """Summarize the latest persisted QC state without rescanning large datasets."""

    if item["state"] != "ready":
        return "raw_issue"
    if task is None:
        return "uninspected"
    if task["status"] in {"QUEUED", "RUNNING"}:
        return "running"
    if task["status"] in {"FAILED", "INTERRUPTED"}:
        return "error"
    if task["status"] == "COMPLETED":
        if task["decision"] == "PASS":
            return "passed"
        if task["decision"] == "RECAPTURE":
            return "recapture"
        return "review"
    return "uninspected"


def _storage(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    try:
        total, used, free = shutil.disk_usage(root)
    except OSError:
        return {
            "available": False,
            "total": "—",
            "used": "—",
            "free": "—",
            "percent": 0,
        }
    percent = round((used / total) * 100, 1) if total else 0
    return {
        "available": True,
        "total": _bytes_label(total),
        "used": _bytes_label(used),
        "free": _bytes_label(free),
        "percent": percent,
    }


def dashboard_snapshot(root: Path) -> dict:
    """Build one truthful, read-only overview from existing platform records."""

    pending_folders, managed = datasets.catalog(root)
    dataset_records = DatasetRepository(root).list()
    by_id = {item["id"]: item for item in managed}

    qc_tasks = QualityRepository(root).list_tasks(visibility="all")
    latest_qc: dict[str, dict] = {}
    for task in qc_tasks:
        latest_qc.setdefault(task["dataset_id"], task)
    for item in managed:
        state = _qc_state(item, latest_qc.get(item["id"]))
        item["qc_state"] = state
        item["qc_label"] = QC_LABELS[state]

    robot_rows = robots.list_robots(root)
    training_tasks = TrainingRepository(root).list(deleted=False)

    raw_count = sum(item["storage_area"] == "raw" for item in managed)
    derived_count = sum(item["storage_area"] == "derived" for item in managed)
    total_episodes = sum(
        item["episodes"] for item in managed if isinstance(item.get("episodes"), int)
    )
    total_frames = sum(
        item["frames"] for item in managed if isinstance(item.get("frames"), int)
    )
    qc_counts = {
        state: sum(item["qc_state"] == state for item in managed)
        for state in QC_LABELS
    }
    active_training = sum(
        task["status"] in ACTIVE_TRAINING_STATUSES for task in training_tasks
    )
    completed_training = sum(task["status"] == "COMPLETED" for task in training_tasks)
    model_count = sum(
        task["status"] == "COMPLETED" and bool(task.get("artifact"))
        for task in training_tasks
    )

    unlinked = sum(not item.get("collector_robot_id") for item in managed)
    invalid = sum(item["state"] != "ready" for item in managed)
    failed_training = sum(task["status"] == "FAILED" for task in training_tasks)
    todo = [
        {
            "label": "待登记 Raw 目录",
            "count": len(pending_folders),
            "hint": "目录已存在，但尚未获得 DS 编号",
            "href": "/data/manage/",
            "tone": "warning",
        },
        {
            "label": "待人工审核",
            "count": qc_counts["review"],
            "hint": "自动质检完成，等待最终判定",
            "href": "/data/raw/?qc=review",
            "tone": "warning",
        },
        {
            "label": "未关联机器人",
            "count": unlinked,
            "hint": "可在机器人管理中建立采集关系",
            "href": "/robots/",
            "tone": "neutral",
        },
        {
            "label": "异常数据集",
            "count": invalid,
            "hint": "目录缺失、元数据无效或登记后发生变化",
            "href": "/data/manage/?view=registered",
            "tone": "danger",
        },
        {
            "label": "失败训练任务",
            "count": failed_training,
            "hint": "进入训练页查看错误和运行日志",
            "href": "/training/",
            "tone": "danger",
        },
    ]

    recent_datasets = []
    for record in dataset_records[:6]:
        item = by_id.get(record["id"])
        if item is not None:
            recent_datasets.append(item)

    recent_qc = []
    for task in qc_tasks[:5]:
        item = by_id.get(task["dataset_id"])
        recent_qc.append(
            {
                **task,
                "dataset_name": item["name"] if item else task["dataset_id"],
                "status_label": {
                    "QUEUED": "排队中",
                    "RUNNING": "质检中",
                    "COMPLETED": "已完成",
                    "FAILED": "失败",
                    "INTERRUPTED": "已中断",
                }.get(task["status"], task["status"]),
            }
        )

    recent_training = [
        {**task, "status_label": TRAINING_LABELS.get(task["status"], task["status"])}
        for task in training_tasks[:5]
    ]

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "stats": {
            "datasets": len(managed),
            "raw": raw_count,
            "derived": derived_count,
            "episodes": total_episodes,
            "frames": total_frames,
            "robots": len(robot_rows),
            "active_robots": sum(row["status"] == "active" for row in robot_rows),
            "qc_passed": qc_counts["passed"],
            "qc_review": qc_counts["review"],
            "qc_running": qc_counts["running"],
            "qc_uninspected": qc_counts["uninspected"],
            "training": len(training_tasks),
            "training_active": active_training,
            "training_completed": completed_training,
            "models": model_count,
        },
        "storage": _storage(root),
        "todo": todo,
        "recent_datasets": recent_datasets,
        "recent_qc": recent_qc,
        "recent_training": recent_training,
    }
