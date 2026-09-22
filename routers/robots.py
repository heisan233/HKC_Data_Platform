from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

ROBOTS = [
    {"id": 1, "name": "人形机器人-A1", "type": "轮式人形", "status": "在线", "ip": "192.168.1.101", "sensors": "RGB-D + 灵巧手", "scene": "H4展厅"},
    {"id": 2, "name": "人形机器人-A2", "type": "轮式人形", "status": "在线", "ip": "192.168.1.102", "sensors": "RGB-D + 灵巧手", "scene": "H4展厅"},
    {"id": 3, "name": "AMR-巡检者", "type": "AMR复合机器人", "status": "离线", "ip": "192.168.1.202", "sensors": "RGB-D", "scene": "电梯间"},
]

@router.get("/")
async def robots_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="robots.html",
        context={"robots": ROBOTS},
    )
