"""Conversion and registered dataset archive HTTP endpoints."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import settings
from app.modules.conversion import service as conversion_service
from app.modules.export import service as export_service
from app.web import templates

router = APIRouter()


class ConvertBody(BaseModel):
    source_dataset_id: str
    profile_id: str = conversion_service.PROFILE_ID


@router.get("/")
def export_page(request: Request):
    root = settings.data_root
    return templates.TemplateResponse(
        request=request,
        name="export.html",
        context={"sources": conversion_service.list_sources(root),
                 "tasks": conversion_service.list_tasks(root),
                 "profiles": conversion_service.PROFILES,
                 "exportables": export_service.list_exportable(root)},
    )


@router.get("/api/datasets")
def datasets():
    return conversion_service.list_sources(settings.data_root)


@router.get("/api/profiles")
def profiles():
    return conversion_service.PROFILES


@router.get("/api/exportable-datasets")
def exportable_datasets():
    return export_service.list_exportable(settings.data_root)


@router.post("/api/conversions", status_code=202)
def create_conversion(body: ConvertBody):
    try:
        return conversion_service.create_task(settings.data_root, body.source_dataset_id, body.profile_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/conversions")
def conversions():
    return conversion_service.list_tasks(settings.data_root)


@router.get("/api/conversions/{task_id}")
def conversion(task_id: str):
    try:
        task = conversion_service.get_task(settings.data_root, task_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if task is None:
        raise HTTPException(404, "转换任务不存在")
    return task


@router.get("/conversions/{task_id}/files/{filename}")
def download_result(task_id: str, filename: str):
    try:
        file = conversion_service.result_file(settings.data_root, task_id, filename)
    except (ValueError, TypeError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(file, filename=filename, media_type="application/octet-stream")


@router.get("/archive/{dataset_id}")
def download_dataset(dataset_id: str):
    try:
        archive = export_service.archive_dataset(settings.data_root, dataset_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(archive, filename=f"{dataset_id}.tar.gz", media_type="application/gzip")
