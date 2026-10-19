"""原始数据集上传页面与 HTTP API。"""

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
    description: Annotated[str, Form()] = "",
) -> JSONResponse:
    """接收一个完整 LeRobot v3 ZIP 数据集。"""

    outcome = await receive_dataset_upload(
        data_root=settings.data_root,
        upload=archive,
        dataset_name=dataset_name,
        description=description,
    )
    return JSONResponse(status_code=outcome.status_code, content=outcome.payload)
