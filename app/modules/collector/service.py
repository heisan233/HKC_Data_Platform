"""Distributed collection rules and durable SHA-256 duplicate claims.

The claims are operational sidecar records under ``storage/system``. Dataset,
robot and relationship metadata remain in MySQL; no upload-task table is added.
"""

from __future__ import annotations

import fcntl
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.dataset_robots import DatasetRobotRepository
from app.db.datasets import DatasetRepository
from app.db.robots import RobotRepository
from app.modules.ingestion.service import get_upload_limits

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PROCESSING_TIMEOUT = timedelta(hours=6)


class CollectorRuleError(ValueError):
    """A machine upload cannot continue without operator action."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 409,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}

    def payload(self) -> dict:
        return {"ok": False, "code": self.code, "message": str(self), **self.details}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise CollectorRuleError(
            "archive_sha256 必须是 64 位十六进制 SHA-256。",
            code="invalid_sha256",
            status_code=422,
        )
    return normalized


def _same_type(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


class ClaimStore:
    """Persist one duplicate-detection record for each exact ZIP digest."""

    def __init__(self, root: Path):
        self.directory = root / "system" / "collector-claims"
        self.lock_path = root / "system" / "collector-claims.lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _path(self, digest: str) -> Path:
        return self.directory / f"{digest}.json"

    def read_unlocked(self, digest: str) -> dict | None:
        try:
            value = json.loads(self._path(digest).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def write_unlocked(self, digest: str, record: dict) -> None:
        destination = self._path(digest)
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)

    def get(self, digest: str) -> dict | None:
        with self.locked():
            return self.read_unlocked(digest)

    def begin(self, digest: str, robot: dict, filename: str, archive_size: int) -> dict:
        with self.locked():
            existing = self.read_unlocked(digest)
            if existing and existing.get("status") == "processing":
                try:
                    updated_at = datetime.fromisoformat(existing["updated_at"])
                except (KeyError, TypeError, ValueError):
                    updated_at = datetime.now(UTC)
                if datetime.now(UTC) - updated_at <= PROCESSING_TIMEOUT:
                    raise CollectorRuleError(
                        "相同文件正在上传，请勿重复提交。",
                        code="upload_in_progress",
                        details={"archive_sha256": digest},
                    )
            timestamp = utc_now()
            record = {
                "archive_sha256": digest,
                "status": "processing",
                "robot_id": robot["id"],
                "robot_type": robot["robot_type"],
                "dataset_id": None,
                "filename": filename,
                "archive_size": archive_size,
                "created_at": timestamp,
                "updated_at": timestamp,
                "message": "正在上传",
            }
            self.write_unlocked(digest, record)
            return record

    def accept(self, digest: str, robot: dict, dataset_id: str) -> dict:
        with self.locked():
            record = self.read_unlocked(digest) or {"archive_sha256": digest}
            record.update(
                status="accepted",
                robot_id=robot["id"],
                robot_type=robot["robot_type"],
                dataset_id=dataset_id,
                updated_at=utc_now(),
                message="数据集已入库",
            )
            self.write_unlocked(digest, record)
            return record

    def fail(self, digest: str, robot_id: str, message: str) -> None:
        with self.locked():
            record = self.read_unlocked(digest)
            if record is None or record.get("status") != "processing":
                return
            if record.get("robot_id") != robot_id:
                return
            record.update(status="failed", updated_at=utc_now(), message=message)
            self.write_unlocked(digest, record)

    def rebind(self, digest: str, robot: dict) -> dict:
        with self.locked():
            record = self.read_unlocked(digest)
            if record is None:
                raise CollectorRuleError(
                    "没有找到该文件的已入库记录。",
                    code="duplicate_record_missing",
                    status_code=404,
                )
            record.update(
                robot_id=robot["id"],
                robot_type=robot["robot_type"],
                updated_at=utc_now(),
                rebound_at=utc_now(),
                message="数据集采集机器人已改绑",
            )
            self.write_unlocked(digest, record)
            return record


def validate_robot_type(
    robot: dict,
    archive_robot_type: str | None,
    robot_type_confirmation: str | None,
) -> None:
    archive_type = (archive_robot_type or "").strip()
    expected_type = robot["robot_type"].strip()
    if archive_type and not _same_type(archive_type, expected_type):
        raise CollectorRuleError(
            "ZIP 中的 robot_type 与 Token 对应机器人类型不一致。",
            code="robot_type_mismatch",
            details={
                "archive_robot_type": archive_type,
                "expected_robot_type": expected_type,
            },
        )
    if not archive_type and not _same_type(robot_type_confirmation or "", expected_type):
        raise CollectorRuleError(
            "ZIP 缺少 robot_type，请确认是否按当前机器人类型上传。",
            code="robot_type_confirmation_required",
            details={"expected_robot_type": expected_type},
        )


def _accepted_duplicate(root: Path, digest: str) -> tuple[dict, dict, dict] | None:
    store = ClaimStore(root)
    claim = store.get(digest)
    if not claim or claim.get("status") != "accepted" or not claim.get("dataset_id"):
        return None
    dataset = DatasetRepository(root).get(claim["dataset_id"])
    if dataset is None:
        return None
    link = DatasetRobotRepository(root).get(dataset["id"])
    if link is None:
        return None
    robot = RobotRepository(root).get(link["robot_id"])
    if robot is None:
        return None
    return claim, dataset, robot


def preflight(
    root: Path,
    robot: dict,
    *,
    archive_sha256: str,
    archive_size: int,
    archive_robot_type: str | None,
    robot_type_confirmation: str | None,
) -> dict:
    digest = normalize_sha256(archive_sha256)
    limits = get_upload_limits()
    if archive_size <= 0 or archive_size > limits.max_upload_bytes:
        raise CollectorRuleError(
            "压缩包大小超过平台上传限制。",
            code="invalid_archive_size",
            status_code=422,
            details={"max_upload_bytes": limits.max_upload_bytes},
        )
    validate_robot_type(robot, archive_robot_type, robot_type_confirmation)
    duplicate = _accepted_duplicate(root, digest)
    if duplicate is None:
        return {
            "ok": True,
            "action": "upload",
            "archive_sha256": digest,
            "robot_id": robot["id"],
        }
    _claim, dataset, current_robot = duplicate
    if current_robot["id"] == robot["id"]:
        return {
            "ok": True,
            "action": "existing",
            "code": "duplicate_same_robot",
            "message": f"该数据已由 {robot['id']} 上传为 {dataset['id']}。",
            "dataset_id": dataset["id"],
            "robot_id": robot["id"],
        }
    if not _same_type(current_robot["robot_type"], robot["robot_type"]):
        raise CollectorRuleError(
            "相同 ZIP 已由另一台不同类型机器人上传，不能改绑。",
            code="duplicate_robot_type_mismatch",
            details={
                "dataset_id": dataset["id"],
                "existing_robot_id": current_robot["id"],
                "existing_robot_type": current_robot["robot_type"],
                "current_robot_id": robot["id"],
                "current_robot_type": robot["robot_type"],
            },
        )
    raise CollectorRuleError(
        "相同 ZIP 已由同类型的另一台机器人上传，请确认数据实际归属。",
        code="duplicate_rebind_confirmation_required",
        details={
            "dataset_id": dataset["id"],
            "existing_robot_id": current_robot["id"],
            "existing_robot_name": current_robot["name"],
            "current_robot_id": robot["id"],
            "robot_type": robot["robot_type"],
        },
    )


def begin_upload(
    root: Path,
    robot: dict,
    *,
    archive_sha256: str,
    filename: str,
    archive_size: int,
) -> dict:
    return ClaimStore(root).begin(
        normalize_sha256(archive_sha256), robot, filename, archive_size
    )


def finish_upload(root: Path, robot: dict, archive_sha256: str, dataset_id: str) -> dict:
    return ClaimStore(root).accept(
        normalize_sha256(archive_sha256), robot, dataset_id
    )


def fail_upload(root: Path, robot_id: str, archive_sha256: str, message: str) -> None:
    ClaimStore(root).fail(normalize_sha256(archive_sha256), robot_id, message)


def rebind_duplicate(
    root: Path,
    robot: dict,
    *,
    archive_sha256: str,
    dataset_id: str,
    confirm: bool,
) -> dict:
    if not confirm:
        raise CollectorRuleError(
            "未确认数据属于当前机器人，已取消操作。",
            code="rebind_cancelled",
            status_code=400,
        )
    digest = normalize_sha256(archive_sha256)
    duplicate = _accepted_duplicate(root, digest)
    if duplicate is None:
        raise CollectorRuleError(
            "没有找到该文件的已入库记录。",
            code="duplicate_record_missing",
            status_code=404,
        )
    _claim, dataset, current_robot = duplicate
    if dataset["id"] != dataset_id:
        raise CollectorRuleError(
            "数据集编号与重复文件记录不一致。",
            code="duplicate_dataset_mismatch",
            status_code=409,
        )
    if current_robot["id"] == robot["id"]:
        return {
            "ok": True,
            "action": "existing",
            "dataset_id": dataset_id,
            "robot_id": robot["id"],
        }
    if not _same_type(current_robot["robot_type"], robot["robot_type"]):
        raise CollectorRuleError(
            "两台机器人类型不一致，不能改绑重复数据集。",
            code="duplicate_robot_type_mismatch",
            details={
                "existing_robot_type": current_robot["robot_type"],
                "current_robot_type": robot["robot_type"],
            },
        )
    DatasetRobotRepository(root).bind(
        robot["id"], [dataset_id], f"collector-rebind:{robot['id']}"
    )
    ClaimStore(root).rebind(digest, robot)
    return {
        "ok": True,
        "action": "rebound",
        "message": f"{dataset_id} 已改绑到 {robot['id']}。",
        "dataset_id": dataset_id,
        "robot_id": robot["id"],
    }
