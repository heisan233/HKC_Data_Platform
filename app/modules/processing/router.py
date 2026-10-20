"""数据处理页面与任务 API。"""

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.modules.processing.schemas import ProcessingJobRequest
from app.modules.processing.service import (
    DatasetNotFoundError,
    ProcessingError,
    create_job,
    get_dataset_workspace,
    get_job,
    list_datasets,
    list_jobs,
    run_job,
    scan_duplicates,
)
from app.web import templates

router = APIRouter()


def _error_response(error: ProcessingError) -> JSONResponse:
    status_code = 404 if isinstance(error, DatasetNotFoundError) else 400
    return JSONResponse(status_code=status_code, content={"ok": False, "message": str(error)})


@router.get("/", response_class=HTMLResponse)
async def preprocess_page(request: Request) -> HTMLResponse:
    """展示真实 LeRobot v3 数据处理工作台。"""

    return templates.TemplateResponse(request=request, name="preprocess.html", context={})


@router.get("/api/datasets", response_class=JSONResponse)
async def processing_datasets() -> JSONResponse:
    try:
        datasets = await run_in_threadpool(list_datasets, settings.data_root)
        return JSONResponse({"ok": True, "datasets": datasets})
    except ProcessingError as error:
        return _error_response(error)


@router.get("/api/datasets/{dataset_id}", response_class=JSONResponse)
async def processing_dataset(dataset_id: str) -> JSONResponse:
    try:
        workspace = await run_in_threadpool(
            get_dataset_workspace,
            settings.data_root,
            dataset_id,
        )
        return JSONResponse({"ok": True, **workspace})
    except ProcessingError as error:
        return _error_response(error)


@router.post("/api/datasets/{dataset_id}/duplicates", response_class=JSONResponse)
async def duplicate_report(dataset_id: str) -> JSONResponse:
    try:
        report = await run_in_threadpool(scan_duplicates, settings.data_root, dataset_id)
        return JSONResponse({"ok": True, **report})
    except ProcessingError as error:
        return _error_response(error)


@router.get("/api/jobs", response_class=JSONResponse)
async def processing_jobs() -> JSONResponse:
    try:
        jobs = await run_in_threadpool(list_jobs, settings.data_root)
        return JSONResponse({"ok": True, "jobs": jobs})
    except ProcessingError as error:
        return _error_response(error)


@router.get("/api/jobs/{job_id}", response_class=JSONResponse)
async def processing_job(job_id: str) -> JSONResponse:
    try:
        job = await run_in_threadpool(get_job, settings.data_root, job_id)
        return JSONResponse({"ok": True, "job": job})
    except ProcessingError as error:
        return _error_response(error)


@router.post("/api/jobs", response_class=JSONResponse)
async def start_processing_job(
    payload: ProcessingJobRequest,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    try:
        job = await run_in_threadpool(create_job, settings.data_root, payload)
        background_tasks.add_task(run_job, settings.data_root, job["job_id"])
        return JSONResponse(status_code=202, content={"ok": True, "job": job})
    except ProcessingError as error:
        return _error_response(error)
