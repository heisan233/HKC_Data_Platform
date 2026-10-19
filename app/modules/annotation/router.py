"""数据标注页面、真实 LeRobot v3 数据 API 与视频流。"""

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.modules.annotation.schemas import PublishAnnotationRequest
from app.modules.annotation.service import (
    AnnotationError,
    DatasetNotFoundError,
    get_workspace,
    list_datasets,
    publish_annotation,
    resolve_episode_video,
)
from app.web import templates

router = APIRouter()


def _error_response(error: AnnotationError) -> JSONResponse:
    status_code = 404 if isinstance(error, DatasetNotFoundError) else 400
    return JSONResponse(status_code=status_code, content={"ok": False, "message": str(error)})


@router.get("/", response_class=HTMLResponse)
async def annotation_page(request: Request) -> HTMLResponse:
    """展示 Episode/task 标注工作台。"""

    return templates.TemplateResponse(
        request=request,
        name="annotation.html",
        context={},
    )


@router.get("/api/datasets", response_class=JSONResponse)
async def annotation_datasets() -> JSONResponse:
    """列出可标注的原始和派生 LeRobot v3 数据集。"""

    try:
        datasets = await run_in_threadpool(list_datasets, settings.data_root)
        return JSONResponse({"ok": True, "datasets": datasets})
    except AnnotationError as error:
        return _error_response(error)


@router.get("/api/datasets/{dataset_id}", response_class=JSONResponse)
async def annotation_workspace(dataset_id: str) -> JSONResponse:
    """读取任务表、Episode 元数据和视频片段定位信息。"""

    try:
        workspace = await run_in_threadpool(get_workspace, settings.data_root, dataset_id)
        return JSONResponse({"ok": True, **workspace})
    except AnnotationError as error:
        return _error_response(error)


@router.get("/api/datasets/{dataset_id}/episodes/{episode_index}/video")
async def episode_video(
    dataset_id: str,
    episode_index: int,
    camera: str = Query(min_length=1, max_length=200),
) -> Response:
    """返回 Episode 对应相机的视频；FileResponse 支持浏览器 Range 请求。"""

    try:
        path = await run_in_threadpool(
            resolve_episode_video,
            settings.data_root,
            dataset_id,
            episode_index,
            camera,
        )
        return FileResponse(path, media_type="video/mp4")
    except AnnotationError as error:
        return _error_response(error)


@router.post("/api/publish", response_class=JSONResponse)
async def publish_annotation_version(payload: PublishAnnotationRequest) -> JSONResponse:
    """把本次 task 修改发布为一个新的完整派生数据集。"""

    try:
        result = await run_in_threadpool(
            publish_annotation,
            data_root=settings.data_root,
            dataset_id=payload.dataset_id,
            version_name=payload.version_name,
            description=payload.description,
            changes=payload.changes,
        )
        return JSONResponse(status_code=201, content=result)
    except AnnotationError as error:
        return _error_response(error)
