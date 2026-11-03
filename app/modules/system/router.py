from fastapi import APIRouter, Query, Request

from app.core.config import settings
from app.modules.system import service
from app.web import templates

router = APIRouter()


@router.get("/")
async def system_page(
    request: Request,
    level: str = "ALL",
    module: str = "ALL",
    q: str = "",
    limit: int = Query(default=100, ge=20, le=500),
):
    if level.upper() not in {"ALL", "INFO", "WARNING", "ERROR"}:
        level = "ALL"
    snapshot = service.log_snapshot(
        settings.data_root,
        level=level,
        module=module,
        query=q,
        limit=limit,
    )
    return templates.TemplateResponse(
        request=request,
        name="system.html",
        context={
            **snapshot,
            "level_filter": level.upper(),
            "module_filter": module,
            "query": q.strip(),
            "limit": limit,
        },
    )
