from fastapi import APIRouter, Request

from app.core.config import settings
from app.modules.dashboard.service import dashboard_snapshot
from app.web import templates

router = APIRouter()


@router.get("/")
async def dashboard_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=dashboard_snapshot(settings.data_root),
    )
