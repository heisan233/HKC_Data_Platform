"""Dataset conversion and compressed export UI/API."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings
from app.modules.export import service
from app.web import templates

router = APIRouter()


class ConvertBody(BaseModel):
    source_name: str


@router.get("/")
def export_page(request: Request):
    root = settings.data_root
    return templates.TemplateResponse(
        request=request, name="export.html",
        context={
            "derived": service.list_datasets(root, "derived"),
            "converted": service.list_datasets(root, "converted"),
            "tasks": service.initialize(root).list(),
        },
    )


@router.get("/api/datasets")
def datasets():
    root = settings.data_root
    return {"derived": service.list_datasets(root, "derived"),
            "converted": service.list_datasets(root, "converted")}


@router.post("/api/conversions", status_code=202)
def create_conversion(body: ConvertBody):
    try:
        return service.create_task(settings.data_root, body.source_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/conversions")
def conversions():
    return service.initialize(settings.data_root).list()


@router.get("/api/conversions/{task_id}")
def conversion(task_id: str):
    task = service.initialize(settings.data_root).get(task_id)
    if task is None:
        raise HTTPException(404, "转换任务不存在")
    return task


@router.get("/archive/{area}/{name}")
def download_archive(area: str, name: str):
    if area not in {"derived", "converted"}:
        raise HTTPException(404, "数据集区域不存在")
    try:
        folder = service.export_folder(settings.data_root, area, name)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    if folder is None:
        raise HTTPException(404, "数据集不存在")
    filename = quote(f"{area}-{name}.tar.gz")
    return StreamingResponse(
        service.archive_stream(folder), media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
