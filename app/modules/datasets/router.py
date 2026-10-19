"""Data management: register existing raw folders, then browse managed assets."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.db.datasets import DatasetRepository
from app.modules.datasets import service
from app.web import templates


router = APIRouter()


class Registration(BaseModel):
    folders: list[str] = Field(min_length=1, max_length=100)
    names: dict[str, str] = Field(default_factory=dict)


class DisplayChange(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


def storage_root() -> Path:
    return settings.data_root


def record_or_404(dataset_id: str) -> dict:
    repo = DatasetRepository(storage_root())
    repo.initialize()
    record = repo.get(dataset_id)
    if record is None:
        raise HTTPException(404, "数据集未登记")
    return record


@router.get("/")
def data_page(request: Request):
    pending, managed = service.catalog(storage_root())
    return templates.TemplateResponse(request=request, name="data.html", context={
        "pending": pending, "managed": managed,
        "detected_count": sum(item["detected"] for item in managed),
        "report_count": sum(item["report_count"] for item in managed),
    })


@router.get("/manage/")
def manage_page(request: Request, q: str = "", view: str = "pending"):
    pending, managed = service.catalog(storage_root())
    rows = pending if view != "registered" else managed
    term = q.strip().casefold()
    if term:
        rows = [row for row in rows if term in " ".join(
            str(row.get(key) or "") for key in ("id", "name", "folder", "robot_type")).casefold()]
    return templates.TemplateResponse(request=request, name="data_manage.html", context={
        "items": rows, "pending_count": len(pending), "registered_count": len(managed),
        "view": "registered" if view == "registered" else "pending", "query": q.strip(),
    })


@router.post("/api/register")
def register(body: Registration):
    return service.register_selected(storage_root(), body.folders, body.names)


@router.patch("/api/datasets/{dataset_id}")
def update_display(dataset_id: str, body: DisplayChange):
    record_or_404(dataset_id)
    name = body.name.strip()
    description = body.description.strip()
    tags = list(dict.fromkeys(tag.strip() for tag in body.tags if tag.strip()))
    if not 1 <= len(name) <= 100:
        raise HTTPException(400, "名称须为 1–100 个字符")
    if len(description) > 500 or len(tags) > 10 or any(len(tag) > 30 for tag in tags):
        raise HTTPException(400, "备注或标签超出长度限制")
    return DatasetRepository(storage_root()).update_display(dataset_id, name, description, tags)


@router.post("/api/datasets/{dataset_id}/rescan")
def accept_rescan(dataset_id: str):
    record = record_or_404(dataset_id)
    folder = service.directory(storage_root(), "raw", record["raw_relpath"])
    if folder is None:
        raise HTTPException(409, "原始数据目录缺失")
    info, error = service.dataset_info(folder)
    if error:
        raise HTTPException(409, error)
    return DatasetRepository(storage_root()).accept_rescan(
        dataset_id, service.info_digest(folder), info["robot_type"].strip())


@router.get("/raw/")
def raw_list(request: Request, q: str = ""):
    _, managed = service.catalog(storage_root())
    term = q.strip().casefold()
    if term:
        managed = [row for row in managed if term in " ".join(
            str(row.get(key) or "") for key in ("id", "name", "folder", "robot_type")).casefold()]
    return templates.TemplateResponse(request=request, name="data_raw.html", context={
        "items": managed, "query": q.strip(),
    })


def asset_page(request: Request, dataset_id: str, area: str):
    record = record_or_404(dataset_id)
    item = service.summary(storage_root(), record["raw_relpath"], record)
    detail = service.detail(storage_root(), record, area)
    return templates.TemplateResponse(request=request, name="data_raw_detail.html", context={
        "item": item, "detail": detail, "area": area, "record": record,
        "reports": service.reports_for(storage_root(), dataset_id),
    })


@router.get("/raw/{dataset_id}")
def raw_detail(request: Request, dataset_id: str):
    return asset_page(request, dataset_id, "raw")


@router.get("/detected/")
def detected_list(request: Request, q: str = ""):
    _, managed = service.catalog(storage_root())
    items = [row for row in managed if row["detected"]]
    term = q.strip().casefold()
    if term:
        items = [row for row in items if term in " ".join(
            str(row.get(key) or "") for key in ("id", "name", "robot_type")).casefold()]
    return templates.TemplateResponse(request=request, name="data_detected.html", context={
        "items": items, "query": q.strip(),
    })


@router.get("/detected/{dataset_id}")
def detected_detail(request: Request, dataset_id: str):
    record_or_404(dataset_id)
    if service.directory(storage_root(), "detected", dataset_id) is None:
        raise HTTPException(404, "质检产物目录不存在")
    return asset_page(request, dataset_id, "detected")


def video_file(dataset_id: str, video_path: str, area: str):
    record = record_or_404(dataset_id)
    name = record["raw_relpath"] if area == "raw" else record["id"]
    folder = service.directory(storage_root(), area, name)
    file = service.safe_file(folder / "videos", video_path) if folder else None
    if file is None or file.suffix.lower() != ".mp4":
        raise HTTPException(404, "视频不存在")
    return FileResponse(file, media_type="video/mp4")


@router.get("/raw/{dataset_id}/video/{video_path:path}")
def raw_video(dataset_id: str, video_path: str):
    return video_file(dataset_id, video_path, "raw")


@router.get("/detected/{dataset_id}/video/{video_path:path}")
def detected_video(dataset_id: str, video_path: str):
    return video_file(dataset_id, video_path, "detected")


@router.get("/reports/")
def reports_page(request: Request):
    _, managed = service.catalog(storage_root())
    groups = [{"dataset": row, "reports": service.reports_for(storage_root(), row["id"])}
              for row in managed if row["report_count"]]
    return templates.TemplateResponse(request=request, name="data_reports.html", context={"groups": groups})


@router.get("/reports/{dataset_id}/{filename}")
def report_file(dataset_id: str, filename: str, download: bool = False):
    record_or_404(dataset_id)
    folder = service.directory(storage_root() / "reports", "qc", dataset_id)
    file = service.safe_file(folder, filename) if folder else None
    if file is None:
        raise HTTPException(404, "报告文件不存在")
    if download:
        return FileResponse(file, filename=file.name)
    if file.suffix.lower() == ".json":
        data = service.read_json(file)
        if data is not None:
            return data
    return FileResponse(file)
