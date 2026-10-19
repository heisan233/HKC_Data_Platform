from fastapi import APIRouter, Request

from app.web import templates

router = APIRouter()

SCENES = [
    {"id": 1, "name": "H4展厅导览", "robot": "人形-A1", "skills": ["导航", "讲解", "语音交互"], "status": "采集中", "progress": 72},
    {"id": 2, "name": "灵巧手抓取", "robot": "人形-A2", "skills": ["抓取", "递物"], "status": "质检中", "progress": 100},
    {"id": 3, "name": "按电梯按钮", "robot": "AMR-巡检者", "skills": ["按按钮", "状态判断"], "status": "标注中", "progress": 45},
    {"id": 4, "name": "触摸屏操作", "robot": "人形-A1", "skills": ["点击", "滑动"], "status": "已完成", "progress": 100},
]

@router.get("/")
async def scenes_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="scenes.html",
        context={"scenes": SCENES},
    )
