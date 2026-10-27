"""Training page and authenticated training APIs."""

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse

from app.core.config import settings
from app.modules.training import service
from app.modules.training.schemas import PreflightRequest, RetryRequest, TrainingCreate
from app.web import templates

router = APIRouter()


def _error(exc: service.TrainingError) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


@router.get("/")
async def training_page(request: Request):
    return templates.TemplateResponse(request=request, name="training.html")


@router.get("/api/catalog")
def catalog(request: Request) -> dict:
    return {
        "datasets": service.registered_datasets(settings.data_root),
        "models": service.models(settings.data_root),
        "environment": service.environment(settings.data_root),
        "can_control_vlm": service.is_operator(request.state.user["username"]),
    }


@router.post("/api/preflight")
def preflight(payload: PreflightRequest) -> dict:
    try:
        return service.preflight(settings.data_root, payload.dataset_id, payload.model_id)
    except service.TrainingError as exc:
        raise _error(exc) from exc


@router.get("/api/tasks")
def tasks(status: str | None = None, model_id: str | None = None,
          dataset_id: str | None = None, deleted: bool = False) -> list[dict]:
    return service.list_tasks(settings.data_root, status=status, model_id=model_id,
                              dataset_id=dataset_id, deleted=deleted)


@router.post("/api/tasks", status_code=201)
def create_task(payload: TrainingCreate) -> dict:
    try:
        return service.create_task(settings.data_root, **payload.model_dump())
    except service.TrainingError as exc:
        raise _error(exc) from exc


@router.delete("/api/tasks/{task_id}")
def delete_task(request: Request, task_id: str) -> dict:
    try:
        return service.delete_task(settings.data_root, task_id,
                                   request.state.user["username"])
    except service.TrainingError as exc:
        raise _error(exc) from exc


@router.post("/api/tasks/{task_id}/restore")
def restore_task(task_id: str) -> dict:
    try:
        return service.restore_task(settings.data_root, task_id)
    except service.TrainingError as exc:
        raise _error(exc) from exc


@router.post("/api/tasks/{task_id}/purge")
def purge_task(
    task_id: str,
    confirmation: str | None = Header(default=None, alias="X-HKC-Training-Purge"),
) -> dict:
    if confirmation != task_id:
        raise HTTPException(400, "请确认要永久删除的任务编号")
    try:
        return service.purge_task(settings.data_root, task_id)
    except service.TrainingError as exc:
        raise _error(exc) from exc


@router.get("/api/tasks/{task_id}")
def task_detail(task_id: str) -> dict:
    try:
        return service.get_task(settings.data_root, task_id)
    except service.TrainingError as exc:
        raise _error(exc) from exc


@router.get("/api/tasks/{task_id}/logs")
def task_logs(task_id: str, before: int | None = None) -> dict:
    try:
        return service.get_task_logs(settings.data_root, task_id, before)
    except service.TrainingError as exc:
        raise _error(exc) from exc


@router.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict:
    try:
        return service.cancel_task(settings.data_root, task_id)
    except service.TrainingError as exc:
        raise _error(exc) from exc


@router.post("/api/tasks/{task_id}/retry", status_code=201)
def retry_task(task_id: str, payload: RetryRequest) -> dict:
    try:
        return service.retry_task(settings.data_root, task_id, payload.name)
    except service.TrainingError as exc:
        raise _error(exc) from exc


@router.get("/api/models")
def model_versions() -> list[dict]:
    return service.model_versions(settings.data_root)


@router.get("/api/models/{task_id}/download")
def download_model(task_id: str) -> FileResponse:
    try:
        path: Path = service.model_package(settings.data_root, task_id)
    except service.TrainingError as exc:
        raise _error(exc) from exc
    return FileResponse(path, filename=path.name, media_type="application/gzip")


@router.post("/api/vlm/{operation}")
def control_vlm(
    request: Request,
    operation: str,
    confirmation: str | None = Header(default=None, alias="X-HKC-Training-Action"),
) -> dict:
    if confirmation != "confirm":
        raise HTTPException(400, "请明确确认 VLM 服务操作")
    try:
        return service.vlm_control(settings.data_root, request.state.user["username"], operation)
    except service.TrainingError as exc:
        raise _error(exc) from exc
