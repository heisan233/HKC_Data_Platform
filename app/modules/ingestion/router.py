"""原始数据集上传页面与 HTTP API。"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.modules.ingestion import ego_session
from app.modules.ingestion.service import (
    clear_upload_records,
    format_bytes,
    get_upload_limits,
    list_upload_records,
    receive_dataset_upload,
)
from app.web import templates

router = APIRouter()


class EgoSessionBody(BaseModel):
    filename: str
    dataset_name: str
    description: str = ""


@router.get("/", response_class=HTMLResponse)
async def ingestion_page(request: Request) -> HTMLResponse:
    """展示上传表单和最近记录。"""

    limits = get_upload_limits()
    return templates.TemplateResponse(
        request=request,
        name="ingestion.html",
        context={
            "records": list_upload_records(settings.data_root),
            "max_upload_size": format_bytes(limits.max_upload_bytes),
        },
    )


@router.post("/api/uploads", response_class=JSONResponse)
async def upload_dataset(
    dataset_name: Annotated[str, Form()],
    archive: Annotated[UploadFile, File()],
    dataset_family: Annotated[str, Form()] = "vla",
    robot_type: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> JSONResponse:
    """接收 VLA、WAM 或 EGO ZIP 数据集。"""

    outcome = await receive_dataset_upload(
        data_root=settings.data_root,
        upload=archive,
        dataset_name=dataset_name,
        dataset_family=dataset_family,
        description=description,
        robot_type=robot_type,
    )
    return JSONResponse(status_code=outcome.status_code, content=outcome.payload)


@router.delete("/api/uploads/records", response_class=JSONResponse)
def delete_upload_records() -> JSONResponse:
    try:
        count = clear_upload_records(settings.data_root)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "清空上传记录失败") from exc
    return JSONResponse(content={"deleted": count})


@router.post("/api/ego-sessions")
def create_ego_session(body: EgoSessionBody):
    try:
        return ego_session.create(settings.data_root, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/ego-sessions/{session_id}")
def ego_session_status(session_id: str):
    try:
        return ego_session.status(settings.data_root, session_id)
    except ego_session.UploadValidationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.put("/api/ego-sessions/{session_id}/chunks")
async def append_ego_chunk(request: Request, session_id: str, offset: int):
    chunk = bytearray()
    async for part in request.stream():
        chunk.extend(part)
        if len(chunk) > ego_session.MAX_CHUNK_BYTES:
            raise HTTPException(413, "EGO 分块超过 16 MiB。")
    try:
        return ego_session.append(settings.data_root, session_id, offset, bytes(chunk))
    except ego_session.UploadValidationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/api/ego-sessions/{session_id}/complete", response_class=JSONResponse)
async def complete_ego_session(session_id: str):
    try:
        outcome = await ego_session.finish(settings.data_root, session_id)
    except ego_session.UploadValidationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return JSONResponse(status_code=outcome.status_code, content=outcome.payload)


@router.post("/api/uploads/batch", response_class=JSONResponse)
async def upload_datasets_batch(
    files: Annotated[list[UploadFile], File()],
    dataset_family: Annotated[str, Form()] = "vla",
    robot_type: Annotated[str, Form()] = "",
) -> JSONResponse:
    """批量调用同一套严格上传流程，并为每个成功数据集登记 DS 编号。"""

    if not 1 <= len(files) <= 20:
        return JSONResponse(
            status_code=400,
            content={"uploaded": [], "errors": [{"message": "每批须上传 1–20 个 ZIP。"}]},
        )
    uploaded: list[dict] = []
    errors: list[dict] = []
    for upload in files:
        filename = upload.filename or "未命名文件"
        stem = Path(filename).stem.strip() or "上传数据集"
        display_name = stem if len(stem) >= 2 else f"数据集 {stem}"
        outcome = await receive_dataset_upload(
            data_root=settings.data_root,
            upload=upload,
            dataset_name=display_name[:100],
            dataset_family=dataset_family,
            description="批量上传",
            robot_type=robot_type,
        )
        if outcome.payload.get("ok"):
            uploaded.append({
                "filename": filename,
                "dataset_id": outcome.payload["dataset_id"],
                "folder": outcome.payload["storage_name"],
                "raw_relative_path": outcome.payload["raw_relative_path"],
            })
        else:
            errors.append({"filename": filename, "message": outcome.payload["message"]})
    return JSONResponse(content={"uploaded": uploaded, "errors": errors})
