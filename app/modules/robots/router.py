from fastapi import APIRouter, HTTPException, Query, Request

from app.core.config import settings
from app.modules.robots import service
from app.modules.robots.schemas import RobotWrite
from app.web import templates

router = APIRouter()


@router.get("/")
async def robots_page(
    request: Request,
    q: str = "",
    status: str = Query(default="all", pattern="^(all|active|maintenance|inactive)$"),
):
    selected_status = None if status == "all" else status
    robots = service.list_robots(settings.data_root, selected_status)
    term = q.strip().casefold()
    if term:
        robots = [
            robot
            for robot in robots
            if term
            in " ".join(
                str(robot.get(key) or "")
                for key in (
                    "id",
                    "name",
                    "robot_type",
                    "model",
                    "serial_number",
                    "network_address",
                )
            ).casefold()
        ]
    all_robots = service.list_robots(settings.data_root)
    counts = {
        "all": len(all_robots),
        "active": sum(robot["status"] == "active" for robot in all_robots),
        "maintenance": sum(robot["status"] == "maintenance" for robot in all_robots),
        "inactive": sum(robot["status"] == "inactive" for robot in all_robots),
    }
    return templates.TemplateResponse(
        request=request,
        name="robots.html",
        context={
            "robots": robots,
            "counts": counts,
            "query": q.strip(),
            "status_filter": status,
            "status_labels": service.STATUS_LABELS,
        },
    )


@router.get("/api/robots")
def robots_api(status: str | None = None) -> list[dict]:
    try:
        return service.list_robots(settings.data_root, status)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/robots", status_code=201)
def create_robot(payload: RobotWrite) -> dict:
    try:
        return service.create_robot(settings.data_root, payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/api/robots/{robot_id}")
def update_robot(robot_id: str, payload: RobotWrite) -> dict:
    try:
        robot = service.update_robot(settings.data_root, robot_id, payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if robot is None:
        raise HTTPException(404, "机器人不存在")
    return robot


@router.post("/api/robots/{robot_id}/deactivate")
def deactivate_robot(robot_id: str) -> dict:
    robot = service.set_robot_status(settings.data_root, robot_id, "inactive")
    if robot is None:
        raise HTTPException(404, "机器人不存在")
    return robot


@router.post("/api/robots/{robot_id}/activate")
def activate_robot(robot_id: str) -> dict:
    robot = service.set_robot_status(settings.data_root, robot_id, "active")
    if robot is None:
        raise HTTPException(404, "机器人不存在")
    return robot
