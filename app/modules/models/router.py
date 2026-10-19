from fastapi import APIRouter, Request

from app.web import templates

router = APIRouter()

# 静态演示数据
MODELS = [
    {"id": 1, "name": "YOLOv8n", "version": "v8.0.1", "type": "目标检测", "size": "6.2MB", "fps": 120, "mAP": 0.85, "status": "已发布"},
    {"id": 2, "name": "YOLOv8m", "version": "v8.0.1", "type": "目标检测", "size": "25.8MB", "fps": 80, "mAP": 0.91, "status": "已发布"},
    {"id": 3, "name": "YOLOv8x", "version": "v8.0.1", "type": "目标检测", "size": "68.2MB", "fps": 45, "mAP": 0.94, "status": "已发布"},
    {"id": 4, "name": "YOLOv9", "version": "v9.0.0", "type": "目标检测", "size": "52.1MB", "fps": 55, "mAP": 0.93, "status": "测试中"},
]

@router.get("/")
async def models_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="models.html",
        context={"models": MODELS},
    )
