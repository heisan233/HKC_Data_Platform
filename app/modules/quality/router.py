"""Data quality pages and HTTP API; checks execute in quality.service."""

from __future__ import annotations

import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import settings
from app.db.datasets import DatasetRepository
from app.modules.datasets import service as datasets
from app.modules.datasets.ego_preview import PreviewError, create_preview
from app.modules.quality import service, vlm
from app.modules.quality.catalog import (
    DOCTOR_CHECKS,
    DOCTOR_REPORT_NAMES,
    FAMILY_LABELS,
    ROBOT_RULE_TYPES,
    VLM_RULE_TYPES,
)
from app.web import templates

router = APIRouter()


class PlanBody(BaseModel):
    name: str
    robot_type: str
    dataset_family: str
    config: dict
    expected_version: int | None = None


class TemplatePlanBody(BaseModel):
    name: str
    dataset_family: str
    reference_dataset_id: str | None = None
    robot_type: str | None = None


class TaskBody(BaseModel):
    dataset_id: str
    plan_id: str
    plan_version: int
    selections: dict[str, list[str]] | None = None


class BatchTaskBody(BaseModel):
    dataset_ids: list[str]
    plan_id: str
    plan_version: int
    selections: dict[str, list[str]] | None = None


class ReviewBody(BaseModel):
    decision: str
    reason: str = ""


def root() -> Path:
    return settings.data_root


def task_or_404(task_id: str) -> dict:
    task = service.initialize(root()).get_task(task_id)
    if task is None or DatasetRepository(root()).get(task["dataset_id"]) is None:
        raise HTTPException(404, "质检任务不存在")
    return task


def _prepare_report_display(report: dict | None, task: dict) -> dict | None:
    if report is None:
        return None
    episodes = [episode
                for rule in report.get("sections", {}).get("vlm", {}).get("results", [])
                for episode in rule.get("episodes", [])]
    task_labels: dict[int, str] = {}
    if any(not episode.get("task") for episode in episodes):
        try:
            _, folder = datasets.resolve_registered_dataset(root(), task["dataset_id"])
            task_labels = vlm.episode_tasks(folder)
        except (OSError, ValueError):
            task_labels = {}
    for episode in episodes:
        episode["display_summary"] = vlm.display_summary(episode)
        episode["task"] = episode.get("task") or task_labels.get(
            int(episode.get("episode", 0)), "未填写"
        )
    return report


@router.get("/")
def qc_page(request: Request):
    repo = service.initialize(root())
    _, managed = datasets.catalog(root())
    managed = [item for item in managed if item["availability"] == "unavailable"]
    plans = repo.list_plans()
    show_hidden = request.query_params.get("hidden") == "1"
    active_ids = {item["id"] for item in DatasetRepository(root()).list()}
    all_tasks = [task for task in repo.list_tasks(visibility="hidden" if show_hidden else "visible")
                 if task["dataset_id"] in active_ids]
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    page_size = 20
    page_count = max(1, (len(all_tasks) + page_size - 1) // page_size)
    page = min(page, page_count)
    tasks = all_tasks[(page - 1) * page_size:page * page_size]
    by_id = {item["id"]: item for item in managed}
    for task in tasks:
        task["dataset"] = by_id.get(task["dataset_id"])
        task["has_report"] = service.report_path(root(), task).is_file()
    return templates.TemplateResponse(request=request, name="qc.html", context={
        "datasets": managed, "plans": plans, "tasks": tasks,
        "show_hidden": show_hidden, "hidden_count": repo.hidden_task_count(),
        "task_page": page, "task_page_count": page_count, "task_total": len(all_tasks),
        "doctor_checks": DOCTOR_CHECKS,
    })


@router.get("/plans/")
def plans_page(request: Request):
    _, managed = datasets.catalog(root())
    return templates.TemplateResponse(request=request, name="qc_plans.html", context={
        "plans": service.initialize(root()).list_plans(),
        "datasets": managed,
        "family_labels": FAMILY_LABELS,
    })


@router.get("/plans/{plan_id}")
def plan_editor(request: Request, plan_id: str):
    plan = service.initialize(root()).get_plan(plan_id)
    if plan is None or plan["archived_at"]:
        raise HTTPException(404, "质检方案不存在")
    return templates.TemplateResponse(request=request, name="qc_plan_editor.html", context={
        "plan": plan, "doctor_checks": DOCTOR_CHECKS,
        "robot_types": ROBOT_RULE_TYPES, "vlm_types": VLM_RULE_TYPES,
        "family_labels": FAMILY_LABELS,
    })


@router.get("/reports/{task_id}")
def report_page(request: Request, task_id: str):
    task = task_or_404(task_id)
    report = _prepare_report_display(service.read_report(root(), task), task)
    dataset = DatasetRepository(root()).get(task["dataset_id"])
    return templates.TemplateResponse(request=request, name="qc_report.html", context={
        "task": task, "report": report, "dataset": dataset,
        "reviews": service.initialize(root()).reviews(task_id),
        "doctor_names": DOCTOR_REPORT_NAMES,
    })


@router.get("/reports/{task_id}/episodes/{episode_index}/video")
def report_episode_video(task_id: str, episode_index: int, rule_id: str):
    task = task_or_404(task_id)
    report = service.read_report(root(), task)
    if report is None:
        raise HTTPException(404, "质检报告不存在")
    rules = report.get("sections", {}).get("vlm", {}).get("results", [])
    rule = next((row for row in rules if row.get("id") == rule_id), None)
    episode = next((row for row in (rule or {}).get("episodes", [])
                    if row.get("episode") == episode_index), None)
    video = episode.get("video") if isinstance(episode, dict) else None
    if not isinstance(video, dict) or not isinstance(video.get("source_file"), str):
        raise HTTPException(404, "该 Episode 没有可查看的问题视频")
    try:
        _, folder = datasets.resolve_registered_dataset(root(), task["dataset_id"])
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if service.raw_fingerprint(folder) != report.get("raw_fingerprint"):
        raise HTTPException(409, "数据集文件已变化，不能作为本次质检的原始证据")
    relative = video["source_file"]
    source = datasets.safe_file(folder, relative) if relative.startswith("videos/") else None
    start, end = video.get("from_timestamp"), video.get("to_timestamp")
    if (source is None or source.suffix.lower() != ".mp4"
            or not isinstance(start, (int, float)) or not math.isfinite(start)
            or not isinstance(end, (int, float)) or not math.isfinite(end)
            or start < 0 or end <= start or end - start > 3600):
        raise HTTPException(404, "问题视频不存在或时间范围无效")
    try:
        preview = create_preview(
            root(), task["dataset_id"], source,
            start_seconds=float(start), duration_seconds=float(end - start),
        )
    except PreviewError as exc:
        raise HTTPException(503, str(exc)) from exc
    return FileResponse(preview, media_type="video/mp4")


@router.get("/reports/{task_id}/download")
def download_report(task_id: str):
    task = task_or_404(task_id)
    path = service.report_path(root(), task)
    if not path.is_file():
        raise HTTPException(404, "报告文件不存在")
    return FileResponse(path, filename=path.name, media_type="application/json")


@router.get("/api/catalog")
def catalog():
    return {"doctor_checks": [{"id": key, "name": name} for key, name in DOCTOR_CHECKS],
            "robot_rule_types": ROBOT_RULE_TYPES, "vlm_rule_types": VLM_RULE_TYPES,
            "vlm_available": bool(settings.vlm_base_url)}


@router.get("/api/plans")
def list_plans(robot_type: str | None = None, dataset_family: str | None = None):
    return service.initialize(root()).list_plans(robot_type, dataset_family)


@router.post("/api/plans")
def create_plan(body: PlanBody):
    name = service._text(body.name, "方案名称")
    robot_type = service._text(body.robot_type, "机器人类型")
    config = service.validate_config(body.config, robot_type, body.dataset_family)
    try:
        return service.initialize(root()).create_plan(name, robot_type, body.dataset_family, config)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/plans/from-dataset/{dataset_id}")
def create_plan_from_dataset(dataset_id: str):
    try:
        return service.plan_from_dataset(root(), dataset_id, bool(settings.vlm_base_url))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/plans/from-template")
def create_plan_from_template(body: TemplatePlanBody):
    return service.plan_from_template(
        root(), body.name, body.dataset_family, bool(settings.vlm_base_url),
        body.reference_dataset_id, body.robot_type,
    )


@router.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    plan = service.initialize(root()).get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, "质检方案不存在")
    return plan


@router.put("/api/plans/{plan_id}")
def update_plan(plan_id: str, body: PlanBody):
    repo = service.initialize(root())
    plan = repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, "质检方案不存在")
    if body.robot_type != plan["robot_type"]:
        raise HTTPException(400, "机器人类型不可在已有方案中修改；请新建方案")
    if body.dataset_family != plan["dataset_family"]:
        raise HTTPException(400, "数据集类别不可在已有方案中修改；请新建方案")
    if body.expected_version is None:
        raise HTTPException(400, "更新方案必须提交当前版本号")
    config = service.validate_config(body.config, plan["robot_type"], plan["dataset_family"])
    try:
        return repo.update_plan(plan_id, service._text(body.name, "方案名称"),
                                config, body.expected_version)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/api/plans/{plan_id}")
def delete_plan(plan_id: str):
    if not service.initialize(root()).archive_plan(plan_id):
        raise HTTPException(404, "质检方案不存在或已删除")
    return {"id": plan_id, "archived": True}


@router.post("/api/tasks")
def create_task(body: TaskBody):
    return service.create_task(root(), body.dataset_id, body.plan_id,
                               body.plan_version, body.selections)


@router.post("/api/tasks/batch")
def create_tasks(body: BatchTaskBody):
    return service.create_tasks(root(), body.dataset_ids, body.plan_id,
                                body.plan_version, body.selections)


@router.get("/api/tasks")
def list_tasks(dataset_id: str | None = None, hidden: bool = False):
    active_ids = {item["id"] for item in DatasetRepository(root()).list()}
    return [task for task in service.initialize(root()).list_tasks(
        dataset_id, visibility="hidden" if hidden else "visible")
        if task["dataset_id"] in active_ids]


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = task_or_404(task_id)
    return {"task": task, "report": service.read_report(root(), task),
            "reviews": service.initialize(root()).reviews(task_id)}


@router.post("/api/tasks/{task_id}/hide")
def hide_task(task_id: str):
    repo = service.initialize(root())
    task = task_or_404(task_id)
    if task["status"] not in {"COMPLETED", "FAILED", "INTERRUPTED"}:
        raise HTTPException(409, "任务尚未结束，不能移出列表")
    repo.set_task_visibility(task_id, hidden=True)
    return {"id": task_id, "hidden": True}


@router.post("/api/tasks/{task_id}/show")
def show_task(task_id: str):
    task_or_404(task_id)
    task = service.initialize(root()).set_task_visibility(task_id, hidden=False)
    if task is None:
        raise HTTPException(404, "质检任务不存在")
    return {"id": task_id, "hidden": False}


@router.post("/api/tasks/{task_id}/review")
def review_task(task_id: str, body: ReviewBody):
    task_or_404(task_id)
    return service.review_task(root(), task_id, body.decision, body.reason)


@router.post("/api/tasks/{task_id}/rerun")
def rerun_task(task_id: str):
    task_or_404(task_id)
    return service.rerun_task(root(), task_id)
