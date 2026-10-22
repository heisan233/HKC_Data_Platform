"""Data management: register existing raw folders, then browse managed assets."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.db.datasets import DatasetRepository
from app.db.quality import QualityRepository
from app.modules.conversion import service as conversion_service
from app.modules.datasets import service
from app.modules.quality.status import attach_status
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
    attach_status(storage_root(), managed)
    converted = conversion_service.list_results(storage_root())
    return templates.TemplateResponse(request=request, name="data.html", context={
        "pending": pending, "managed": managed, "converted": converted,
        "review_count": sum(item.get("qc_state") == "review" for item in managed),
        "passed_count": sum(item.get("qc_state") == "passed" for item in managed),
        "recapture_count": sum(item.get("qc_state") == "recapture" for item in managed),
        "report_count": sum(item["report_count"] for item in managed),
    })


@router.get("/manage/")
def manage_page(request: Request, q: str = "", view: str = "pending"):
    pending, managed = service.catalog(storage_root())
    if view == "registered":
        attach_status(storage_root(), managed)
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
    folder = service.directory(
        storage_root(), record["storage_area"], record["storage_relpath"]
    )
    if folder is None:
        raise HTTPException(409, "原始数据目录缺失")
    info, error = service.dataset_info(folder)
    if error:
        raise HTTPException(409, error)
    return DatasetRepository(storage_root()).accept_rescan(
        dataset_id, service.info_digest(folder), service.robot_type_label(info))


@router.get("/raw/")
def raw_list(request: Request, q: str = "", qc: str = "all"):
    _, managed = service.catalog(storage_root())
    attach_status(storage_root(), managed)
    if qc in {"uninspected", "review", "passed", "recapture", "running", "error", "stale", "raw_issue"}:
        managed = [item for item in managed if item["qc_state"] == qc]
    else:
        qc = "all"
    term = q.strip().casefold()
    if term:
        managed = [row for row in managed if term in " ".join(
            str(row.get(key) or "") for key in ("id", "name", "folder", "robot_type")).casefold()]
    return templates.TemplateResponse(request=request, name="data_raw.html", context={
        "items": managed, "query": q.strip(), "qc_filter": qc,
    })


def asset_page(request: Request, dataset_id: str):
    record = record_or_404(dataset_id)
    item = service.summary(storage_root(), record["storage_relpath"], record)
    attach_status(storage_root(), [item])
    detail = service.detail(storage_root(), record)
    return templates.TemplateResponse(request=request, name="data_raw_detail.html", context={
        "item": item, "detail": detail, "area": record["storage_area"], "record": record,
        "reports": service.reports_for(storage_root(), dataset_id),
    })


@router.get("/raw/{dataset_id}")
def raw_detail(request: Request, dataset_id: str):
    return asset_page(request, dataset_id)


def video_file(dataset_id: str, video_path: str):
    record = record_or_404(dataset_id)
    folder = service.directory(
        storage_root(), record["storage_area"], record["storage_relpath"]
    )
    file = service.safe_file(folder / "videos", video_path) if folder else None
    if file is None or file.suffix.lower() != ".mp4":
        raise HTTPException(404, "视频不存在")
    return FileResponse(file, media_type="video/mp4")


@router.get("/raw/{dataset_id}/video/{video_path:path}")
def raw_video(dataset_id: str, video_path: str):
    return video_file(dataset_id, video_path)


@router.get("/datasets/{dataset_id}/video/{video_path:path}")
def dataset_video(dataset_id: str, video_path: str):
    return video_file(dataset_id, video_path)


@router.get("/reports/")
def reports_page(request: Request, view: str = "active"):
    _, managed = service.catalog(storage_root())
    view = "trash" if view == "trash" else "active"
    if view == "trash":
        groups = [
            {"dataset": row, "reports": service.trashed_reports_for(storage_root(), row["id"])}
            for row in managed
        ]
    else:
        groups = [
            {"dataset": row, "reports": service.reports_for(storage_root(), row["id"])}
            for row in managed
        ]
    groups = [group for group in groups if group["reports"]]
    return templates.TemplateResponse(request=request, name="data_reports.html", context={
        "groups": groups,
        "view": view,
        "active_count": sum(row["report_count"] for row in managed),
        "trash_count": sum(
            len(service.trashed_reports_for(storage_root(), row["id"])) for row in managed
        ),
    })


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


@router.delete("/reports/{dataset_id}/{filename}")
def delete_report(dataset_id: str, filename: str):
    record_or_404(dataset_id)
    folder = service.directory(storage_root() / "reports", "qc", dataset_id)
    file = service.safe_file(folder, filename) if folder and Path(filename).name == filename else None
    if file is None or filename.startswith("."):
        raise HTTPException(404, "报告文件不存在")
    if filename.startswith("QC-") and filename.endswith(".json"):
        task = QualityRepository(storage_root()).get_task(filename[:-5])
        if task is not None:
            if task["dataset_id"] != dataset_id:
                raise HTTPException(409, "报告与质检任务的数据集不一致")
            if task["status"] in {"QUEUED", "RUNNING"}:
                raise HTTPException(409, "质检任务仍在执行，不能删除报告")
    try:
        metadata = service.move_report_to_trash(storage_root(), dataset_id, filename)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "报告移入回收站失败") from exc
    return {**metadata, "trashed": True}


@router.get("/reports/trash/{dataset_id}/{filename}")
def trashed_report(dataset_id: str, filename: str, download: bool = False):
    record_or_404(dataset_id)
    file = service.trashed_report_file(storage_root(), dataset_id, filename)
    if file is None:
        raise HTTPException(404, "回收站报告不存在")
    if download:
        return FileResponse(file, filename=file.name)
    if file.suffix.lower() == ".json":
        data = service.read_json(file)
        if data is not None:
            return data
    return FileResponse(file)


@router.post("/reports/trash/{dataset_id}/{filename}/restore")
def restore_report(dataset_id: str, filename: str):
    record_or_404(dataset_id)
    try:
        return service.restore_trashed_report(storage_root(), dataset_id, filename)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "恢复报告失败") from exc
