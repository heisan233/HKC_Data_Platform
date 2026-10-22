"""Robot registry business operations."""

from pathlib import Path

from app.db.datasets import DatasetRepository
from app.db.robots import RobotRepository
from app.modules.robots.schemas import RobotWrite

STATUS_LABELS = {
    "active": "在用",
    "maintenance": "维护中",
    "inactive": "已停用",
}


def list_robots(root: Path, status: str | None = None) -> list[dict]:
    robots = RobotRepository(root).list(status)
    dataset_counts: dict[str, int] = {}
    for dataset in DatasetRepository(root).list():
        robot_type = dataset["robot_type"]
        dataset_counts[robot_type] = dataset_counts.get(robot_type, 0) + 1
    return [
        {
            **robot,
            "status_label": STATUS_LABELS[robot["status"]],
            "matching_dataset_count": dataset_counts.get(robot["robot_type"], 0),
        }
        for robot in robots
    ]


def create_robot(root: Path, payload: RobotWrite) -> dict:
    return RobotRepository(root).create(**payload.model_dump())


def update_robot(root: Path, robot_id: str, payload: RobotWrite) -> dict | None:
    return RobotRepository(root).update(robot_id, **payload.model_dump())


def set_robot_status(root: Path, robot_id: str, status: str) -> dict | None:
    return RobotRepository(root).set_status(robot_id, status)
