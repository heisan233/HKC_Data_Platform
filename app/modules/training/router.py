from fastapi import APIRouter, Request

from app.web import templates

router = APIRouter()

TASKS = [
    {"id": 1, "name": "FAB VLA v1", "model": "VLA-Base", "dataset": "H4展厅导览数据集", "status": "运行中", "metric": "-", "start": "2026-09-21", "end": "-"},
    {"id": 2, "name": "FAB VLA v0", "model": "VLA-Base", "dataset": "灵巧手抓取数据集", "status": "已完成", "metric": "0.82", "start": "2026-09-15", "end": "2026-09-19"},
    {"id": 3, "name": "FAB VLA v1-按电梯", "model": "VLA-Base", "dataset": "按电梯按钮数据集", "status": "排队中", "metric": "-", "start": "-", "end": "-"},
]

@router.get("/")
async def training_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="training.html",
        context={"tasks": TASKS},
    )
