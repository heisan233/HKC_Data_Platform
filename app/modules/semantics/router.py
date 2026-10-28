"""Dataset schema editor page and API."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.modules.semantics import service
from app.modules.semantics.schemas import SchemaFieldCreateRequest, SchemaFieldDeleteRequest
from app.web import templates

router = APIRouter()
MAX_IMPORT_BYTES = 512 * 1024 * 1024


def _error(error: Exception, status_code: int = 400) -> JSONResponse:
    message = str(error) if isinstance(error, service.SchemaEditorError) else "数据定义操作失败。"
    return JSONResponse(status_code=status_code, content={"ok": False, "message": message})


@router.get("/", response_class=HTMLResponse)
async def semantics_page(request: Request) -> HTMLResponse:
    """Render the real dataset schema editor."""

    return templates.TemplateResponse(request=request, name="semantics.html", context={})


@router.get("/api/datasets", response_class=JSONResponse)
async def datasets_api() -> JSONResponse:
    try:
        rows = await run_in_threadpool(service.list_datasets, settings.data_root)
        return JSONResponse({"ok": True, "datasets": rows})
    except service.SchemaEditorError as error:
        return _error(error)


@router.get("/api/datasets/{dataset_id}/schema", response_class=JSONResponse)
async def schema_api(dataset_id: str) -> JSONResponse:
    try:
        schema = await run_in_threadpool(service.inspect_schema, settings.data_root, dataset_id)
        return JSONResponse({"ok": True, **schema})
    except service.SchemaEditorError as error:
        return _error(error, 404 if "未登记" in str(error) else 400)


@router.post("/api/datasets/{dataset_id}/fields", response_class=JSONResponse)
async def create_field_api(
    dataset_id: str,
    payload: SchemaFieldCreateRequest,
) -> JSONResponse:
    try:
        result = await run_in_threadpool(
            service.create_field,
            settings.data_root,
            dataset_id,
            payload,
        )
        return JSONResponse(status_code=201, content={"ok": True, "result": result})
    except service.SchemaEditorError as error:
        return _error(error)


@router.post("/api/datasets/{dataset_id}/fields/delete", response_class=JSONResponse)
async def delete_field_api(
    dataset_id: str,
    payload: SchemaFieldDeleteRequest,
) -> JSONResponse:
    try:
        result = await run_in_threadpool(
            service.delete_field,
            settings.data_root,
            dataset_id,
            payload,
        )
        return JSONResponse({"ok": True, "result": result})
    except service.SchemaEditorError as error:
        return _error(error)


def _parse_json_list(value: str, label: str) -> list:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise service.SchemaEditorError(f"{label} 必须是有效 JSON 数组。") from exc
    if not isinstance(parsed, list):
        raise service.SchemaEditorError(f"{label} 必须是 JSON 数组。")
    return parsed


async def _save_import(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with destination.open("wb") as stream:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise service.SchemaEditorError("导入文件不能超过 512 MB。")
                stream.write(chunk)
    finally:
        await upload.close()


@router.post("/api/datasets/{dataset_id}/frame-import", response_class=JSONResponse)
async def import_frame_feature_api(
    dataset_id: str,
    field_name: Annotated[str, Form()],
    dtype: Annotated[str, Form()],
    shape: Annotated[str, Form()],
    source_columns: Annotated[str, Form()],
    version_name: Annotated[str, Form()],
    import_file: Annotated[UploadFile, File()],
    names: Annotated[str, Form()] = "[]",
    description: Annotated[str, Form()] = "",
    version_description: Annotated[str, Form()] = "",
) -> JSONResponse:
    """Import one numeric frame feature aligned by Episode and frame indexes."""

    temporary_root = settings.data_root / "staging" / "schema-imports" / uuid4().hex
    suffix = Path(import_file.filename or "").suffix.lower()
    temporary = temporary_root / f"import{suffix}"
    try:
        shape_values = _parse_json_list(shape, "shape")
        name_values = _parse_json_list(names, "names")
        source_values = _parse_json_list(source_columns, "source_columns")
        payload = SchemaFieldCreateRequest(
            scope="frame",
            name=field_name,
            dtype=dtype,
            shape=shape_values,
            names=name_values or None,
            description=description,
            version_name=version_name,
            version_description=version_description,
        )
        if not source_values or any(not isinstance(value, str) or not value.strip() for value in source_values):
            raise service.SchemaEditorError("source_columns 必须包含非空列名。")
        await _save_import(import_file, temporary)
        result = await run_in_threadpool(
            service.import_frame_feature,
            settings.data_root,
            dataset_id,
            field_name=payload.name,
            dtype=payload.dtype,
            shape=payload.shape,
            names=payload.names,
            description=payload.description,
            version_name=payload.version_name,
            version_description=payload.version_description,
            import_path=temporary,
            source_columns=[value.strip() for value in source_values],
        )
        return JSONResponse(status_code=201, content={"ok": True, "result": result})
    except (service.SchemaEditorError, ValueError) as error:
        if isinstance(error, service.SchemaEditorError):
            return _error(error)
        return JSONResponse(status_code=422, content={"ok": False, "message": str(error)})
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
