import random
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request

from app.web import templates

router = APIRouter()

# ===== 模拟日志数据 =====
LOG_LEVELS = ["INFO", "WARNING", "ERROR", "DEBUG"]
LOG_MODULES = [
    "推理服务",
    "数据管理",
    "训练引擎",
    "模型加载",
    "接口调用",
    "认证系统",
    "标注服务",
    "调度中心",
]
LOG_ACTIONS = [
    "模型推理完成，检测到 3 个目标",
    "数据集上传成功：生产线零件数据集",
    "训练任务启动：v2-零件抓取优化",
    "YOLOv8n 模型加载成功，耗时 1.2s",
    "接口 /api/v1/detect 调用成功，耗时 28ms",
    "用户 admin 登录成功",
    "标注任务已分配给 user_001",
    "训练任务 v1-货架识别 已完成，mAP=0.94",
    "GPU 显存使用率 78%，状态正常",
    "新机器人注册：AMR-巡检者",
    "模型发布：YOLOv9 已上线",
    "数据集标注完成率 100%",
    "训练任务异常退出，错误码 0x0001",
    "接口 /api/v1/scene/config 调用超时",
    "用户 admin 修改密码成功",
    "难例挖掘完成：新增 156 张待标注图片",
]


def generate_mock_logs(count=50):
    """生成模拟日志数据"""
    logs = []
    now = datetime.now(UTC).astimezone()
    for i in range(count):
        # 随机时间：往前推 0-7 天
        delta_seconds = random.randint(0, 7 * 24 * 3600)
        log_time = now - timedelta(seconds=delta_seconds)

        level = random.choice(LOG_LEVELS)
        module = random.choice(LOG_MODULES)
        action = random.choice(LOG_ACTIONS)

        # 根据级别调整颜色标记
        if level == "ERROR":
            action = random.choice(
                [
                    "训练任务异常退出，错误码 0x0001",
                    "接口 /api/v1/scene/config 调用超时",
                    "模型加载失败：文件不存在",
                    "数据库连接超时，重试中...",
                ]
            )
        elif level == "WARNING":
            action = random.choice(
                [
                    "GPU 显存使用率 92%，接近上限",
                    "数据集标注进度较慢，建议增加人力",
                    "模型推理帧率低于预期，建议优化",
                    "网络延迟偏高，平均 150ms",
                ]
            )

        logs.append(
            {
                "id": i + 1,
                "time": log_time.strftime("%Y-%m-%d %H:%M:%S"),
                "level": level,
                "module": module,
                "action": action,
            }
        )

    # 按时间倒序排列（最新的在前）
    logs.sort(key=lambda x: x["time"], reverse=True)
    return logs


# 统计信息
def get_stats(logs):
    total = len(logs)
    error_count = sum(1 for l in logs if l["level"] == "ERROR")
    warning_count = sum(1 for l in logs if l["level"] == "WARNING")
    info_count = sum(1 for l in logs if l["level"] == "INFO")
    return {
        "total": total,
        "error": error_count,
        "warning": warning_count,
        "info": info_count,
        "today": sum(
            1
            for log in logs
            if log["time"].startswith(datetime.now(UTC).astimezone().strftime("%Y-%m-%d"))
        ),
    }


@router.get("/")
async def log_page(request: Request):
    logs = generate_mock_logs(50)
    stats = get_stats(logs)
    return templates.TemplateResponse(
        request=request,
        name="log.html",
        context={
            "logs": logs,
            "stats": stats,
        },
    )


@router.get("/api/logs")
async def get_logs_api(
    request: Request,
    level: str | None = None,
    module: str | None = None,
    limit: int = 50,
):
    """API接口：获取日志数据（供前端筛选/刷新）"""
    logs = generate_mock_logs(100)

    # 按级别筛选
    if level and level != "ALL":
        logs = [l for l in logs if l["level"] == level]

    # 按模块筛选
    if module and module != "ALL":
        logs = [l for l in logs if l["module"] == module]

    # 限制数量
    logs = logs[:limit]

    return {"code": 0, "data": logs, "total": len(logs)}
