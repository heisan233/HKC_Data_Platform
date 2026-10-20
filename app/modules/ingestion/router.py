"""Browser upload endpoint for complete LeRobot v3 dataset ZIP files."""

from typing import Annotated
from zipfile import BadZipFile

from fastapi import APIRouter, File, UploadFile

from app.core.config import settings
from app.modules.ingestion.service import ingest

router = APIRouter()


@router.post("/api/uploads")
async def upload_datasets(files: Annotated[list[UploadFile], File()]):
    if not 1 <= len(files) <= 20:
        return {"uploaded": [], "errors": [{"filename": "", "message": "每批须上传 1–20 个 ZIP"}]}
    uploaded = []
    errors = []
    for file in files:
        try:
            uploaded.append(await ingest(settings.data_root, file))
        except (ValueError, OSError, RuntimeError, BadZipFile) as exc:
            errors.append({"filename": file.filename or "", "message": str(exc)[:500]})
    return {"uploaded": uploaded, "errors": errors}
