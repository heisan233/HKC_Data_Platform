"""Robot registry business operations."""

from pathlib import Path

from app.db.dataset_robots import DatasetRobotRepository
from app.db.datasets import DatasetRepository
from app.db.robot_tokens import RobotTokenRepository
from app.db.robots import RobotRepository
from app.modules.robots.schemas import DatasetBindingWrite, RobotWrite

STATUS_LABELS = {
    "active": "在用",
    "maintenance": "维护中",
    "inactive": "已停用",
}

PUBLIC_ROBOT_FIELDS = (
    "id",
    "seq",
    "name",
    "robot_type",
    "serial_number",
    "status",
    "network_address",
    "notes",
    "created_at",
    "updated_at",
)


def _public_robot(robot: dict) -> dict:
    return {field: robot[field] for field in PUBLIC_ROBOT_FIELDS}


def _token_state(token: dict | None) -> dict:
    if token is None:
        return {
            "token_configured": False,
            "token_prefix": None,
            "token_created_at": None,
            "token_last_used_at": None,
        }
    return {
        "token_configured": True,
        "token_prefix": token["token_prefix"],
        "token_created_at": token["token_created_at"],
        "token_last_used_at": token["token_last_used_at"],
    }


def list_robots(root: Path, status: str | None = None) -> list[dict]:
    robots = RobotRepository(root).list(status)
    tokens = RobotTokenRepository(root).list_statuses()
    dataset_counts: dict[str, int] = {}
    for link in DatasetRobotRepository(root).list():
        robot_id = link["robot_id"]
        dataset_counts[robot_id] = dataset_counts.get(robot_id, 0) + 1
    return [
        {
            **_public_robot(robot),
            **_token_state(tokens.get(robot["id"])),
            "status_label": STATUS_LABELS[robot["status"]],
            "matching_dataset_count": dataset_counts.get(robot["id"], 0),
        }
        for robot in robots
    ]


def create_robot(root: Path, payload: RobotWrite) -> dict:
    robot = RobotRepository(root).create(**payload.model_dump())
    token = RobotTokenRepository(root).create(robot["id"])
    return {
        **_public_robot(robot),
        **_token_state(token),
        # Only the create response exposes the full value so the browser can
        # show it once. List/update/status APIs continue to return a prefix.
        "token": token["token"],
    }


def regenerate_token(root: Path, robot_id: str) -> dict:
    """Replace one robot token and return the new full value exactly once."""
    robot = RobotRepository(root).get(robot_id)
    if robot is None:
        raise ValueError("机器人不存在")
    token = RobotTokenRepository(root).regenerate(robot_id)
    return {
        **_public_robot(robot),
        **_token_state(token),
        "token": token["token"],
    }


def update_robot(root: Path, robot_id: str, payload: RobotWrite) -> dict | None:
    repo = RobotRepository(root)
    current = repo.get(robot_id)
    if current is None:
        return None
    updated = repo.update(
        robot_id,
        **payload.model_dump(),
        model=current["model"],
        status=current["status"],
        sensors=current["sensors"],
    )
    if updated is None:
        return None
    token = RobotTokenRepository(root).get(robot_id)
    return {**_public_robot(updated), **_token_state(token)}


def set_robot_status(root: Path, robot_id: str, status: str) -> dict | None:
    robot = RobotRepository(root).set_status(robot_id, status)
    if robot is None:
        return None
    token = RobotTokenRepository(root).get(robot_id)
    return {**_public_robot(robot), **_token_state(token)}


def dataset_bindings(root: Path, robot_id: str) -> dict | None:
    robot = RobotRepository(root).get(robot_id)
    if robot is None:
        return None
    links = {row["dataset_id"]: row for row in DatasetRobotRepository(root).list()}
    rows = []
    for dataset in DatasetRepository(root).list(deleted=None):
        link = links.get(dataset["id"])
        in_trash = dataset["deleted_at"] is not None
        rows.append({
            "id": dataset["id"],
            "name": dataset["name"],
            "storage_area": dataset["storage_area"],
            "availability": dataset["availability"],
            "in_trash": in_trash,
            "robot_type": dataset["robot_type"],
            "collector_robot_id": link["robot_id"] if link else None,
            "collector_robot_name": link["robot_name"] if link else None,
            "linked_to_selected": bool(link and link["robot_id"] == robot_id),
            "type_mismatch": dataset["robot_type"].strip().casefold()
            != robot["robot_type"].strip().casefold(),
        })
    rows.sort(key=lambda row: (not row["linked_to_selected"], row["id"]), reverse=False)
    return {
        "robot": {
            **_public_robot(robot),
            "status_label": STATUS_LABELS[robot["status"]],
        },
        "datasets": rows,
        "linked_count": sum(row["linked_to_selected"] for row in rows),
    }


def bind_datasets(
    root: Path,
    robot_id: str,
    payload: DatasetBindingWrite,
    username: str,
) -> dict | None:
    if RobotRepository(root).get(robot_id) is None:
        return None
    DatasetRobotRepository(root).bind(robot_id, payload.dataset_ids, username)
    return dataset_bindings(root, robot_id)


def unlink_dataset(root: Path, robot_id: str, dataset_id: str) -> bool | None:
    if RobotRepository(root).get(robot_id) is None:
        return None
    return DatasetRobotRepository(root).unlink(robot_id, dataset_id)
