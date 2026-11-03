"""原始数据集上传页面与 HTTP API。"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.config import settings
from app.modules.ingestion.service import (
    format_bytes,
    get_upload_limits,
    list_upload_records,
    receive_dataset_upload,
)
from app.web import templates

router = APIRouter()


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
    robot_type: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> JSONResponse:
    """接收一个完整 LeRobot v3 ZIP 数据集。"""

    outcome = await receive_dataset_upload(
        data_root=settings.data_root,
        upload=archive,
        dataset_name=dataset_name,
        description=description,
        robot_type=robot_type,
    )
    return JSONResponse(status_code=outcome.status_code, content=outcome.payload)


@router.post("/api/uploads/batch", response_class=JSONResponse)
async def upload_datasets_batch(
    files: Annotated[list[UploadFile], File()],
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
