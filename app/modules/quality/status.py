"""Read-only dataset status derived from the latest quality task."""

from pathlib import Path

from fastapi import HTTPException

from app.db.quality import QualityRepository
from app.modules.datasets import service as datasets
from app.modules.quality.service import raw_fingerprint

LABELS = {
    "uninspected": "未质检",
    "running": "质检中",
    "review": "待人工审核",
    "passed": "已审核通过",
    "recapture": "打回重采",
    "error": "执行异常",
    "stale": "数据已变化",
    "raw_issue": "数据集目录异常",
}


def attach_status(root: Path, items: list[dict]) -> list[dict]:
    """Decorate registered datasets without changing task or review records.

    Hidden tasks still count: hiding a task only affects the QC task list.
    Completed QC results describe only the unchanged dataset snapshot they checked.
    """
    latest = {}
    for task in QualityRepository(root).list_tasks(visibility="all"):
        latest.setdefault(task["dataset_id"], task)
    for item in items:
        task = latest.get(item["id"])
        state = "uninspected"
        if item["state"] != "ready":
            state = "raw_issue"
        elif task is not None:
            if task["status"] in {"QUEUED", "RUNNING"}:
                state = "running"
            elif task["status"] in {"FAILED", "INTERRUPTED"}:
                state = "error"
            elif task["status"] == "COMPLETED":
                folder = datasets.directory(root, item["storage_area"], item["folder"])
                try:
                    unchanged = folder is not None and raw_fingerprint(folder) == task["raw_fingerprint"]
                except (HTTPException, OSError, ValueError):
                    unchanged = False
                if not unchanged:
                    state = "stale"
                elif task["decision"] == "RECAPTURE":
                    state = "recapture"
                elif task["decision"] == "PASS":
                    state = "passed"
                else:
                    state = "review"
        item["qc_state"] = state
        item["qc_label"] = LABELS[state]
        item["qc_task_id"] = task["id"] if task else None
    return items
