from fastapi import APIRouter, Request

from app.web import templates

router = APIRouter()

LOGS = [
    {"id": 1, "time": "2026-09-21 10:30:12", "level": "INFO", "module": "数据采集", "msg": "H4展厅导览采集任务完成，新增 320 episodes"},
    {"id": 2, "time": "2026-09-21 10:15:03", "level": "WARNING", "module": "数据质检", "msg": "按电梯按钮数据集丢帧率 2.1%，超过阈值"},
    {"id": 3, "time": "2026-09-21 09:50:44", "level": "ERROR", "module": "格式转换", "msg": "MCAP 转换失败：Topic /camera/depth 时间戳异常"},
    {"id": 4, "time": "2026-09-21 09:30:20", "level": "INFO", "module": "模型训练", "msg": "FAB VLA v1 训练任务启动"},
]

@router.get("/")
async def system_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="system.html",
        context={"logs": LOGS},
    )
