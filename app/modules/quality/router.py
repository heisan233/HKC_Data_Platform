"""Data quality pages and HTTP API; checks execute in quality.service."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import settings
from app.db.datasets import DatasetRepository
from app.modules.datasets import service as datasets
from app.modules.quality import service
from app.modules.quality.catalog import (
    DOCTOR_CHECKS, DOCTOR_REPORT_NAMES, ROBOT_RULE_TYPES, VLM_RULE_TYPES,
)
from app.web import templates

router = APIRouter()


class PlanBody(BaseModel):
    name: str
    robot_type: str
    config: dict
    expected_version: int | None = None


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
    if task is None:
        raise HTTPException(404, "质检任务不存在")
    return task


@router.get("/")
def qc_page(request: Request):
    repo = service.initialize(root())
    _, managed = datasets.catalog(root())
    plans = repo.list_plans()
    show_hidden = request.query_params.get("hidden") == "1"
    all_tasks = repo.list_tasks(visibility="hidden" if show_hidden else "visible")
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
    })


@router.get("/plans/{plan_id}")
def plan_editor(request: Request, plan_id: str):
    plan = service.initialize(root()).get_plan(plan_id)
    if plan is None or plan["archived_at"]:
        raise HTTPException(404, "质检方案不存在")
    return templates.TemplateResponse(request=request, name="qc_plan_editor.html", context={
        "plan": plan, "doctor_checks": DOCTOR_CHECKS,
        "robot_types": ROBOT_RULE_TYPES, "vlm_types": VLM_RULE_TYPES,
    })


@router.get("/reports/{task_id}")
def report_page(request: Request, task_id: str):
    task = task_or_404(task_id)
    report = service.read_report(root(), task)
    dataset = DatasetRepository(root()).get(task["dataset_id"])
    return templates.TemplateResponse(request=request, name="qc_report.html", context={
        "task": task, "report": report, "dataset": dataset,
        "reviews": service.initialize(root()).reviews(task_id),
        "doctor_names": DOCTOR_REPORT_NAMES,
    })


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
def list_plans(robot_type: str | None = None):
    return service.initialize(root()).list_plans(robot_type)


@router.post("/api/plans")
def create_plan(body: PlanBody):
    name = service._text(body.name, "方案名称")
    robot_type = service._text(body.robot_type, "机器人类型")
    config = service.validate_config(body.config, robot_type)
    return service.initialize(root()).create_plan(name, robot_type, config)


@router.post("/api/plans/from-dataset/{dataset_id}")
def create_plan_from_dataset(dataset_id: str):
    return service.plan_from_dataset(root(), dataset_id, bool(settings.vlm_base_url))


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
    if body.expected_version is None:
        raise HTTPException(400, "更新方案必须提交当前版本号")
    config = service.validate_config(body.config, plan["robot_type"])
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
    return service.initialize(root()).list_tasks(
        dataset_id, visibility="hidden" if hidden else "visible")


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = task_or_404(task_id)
    return {"task": task, "report": service.read_report(root(), task),
            "reviews": service.initialize(root()).reviews(task_id)}


@router.post("/api/tasks/{task_id}/hide")
def hide_task(task_id: str):
    repo = service.initialize(root())
    task = repo.get_task(task_id)
    if task is None:
        raise HTTPException(404, "质检任务不存在")
    if task["status"] not in {"COMPLETED", "FAILED", "INTERRUPTED"}:
        raise HTTPException(409, "任务尚未结束，不能移出列表")
    repo.set_task_visibility(task_id, hidden=True)
    return {"id": task_id, "hidden": True}


@router.post("/api/tasks/{task_id}/show")
def show_task(task_id: str):
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
    return service.rerun_task(root(), task_id)
