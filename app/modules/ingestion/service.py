"""LeRobot v3 ZIP 上传、校验和入库业务。

HTTP 层只把表单文件交给这里。该模块负责分块写入临时目录、校验压缩包、
安全解压，并把通过校验的数据集移动到 ``storage/raw``。原始数据入库后不再
原地修改；上传记录则以 JSON 旁路文件保存在 ``storage/system/upload-records``。
"""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4
from zipfile import BadZipFile, ZipFile, ZipInfo, is_zipfile

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.db.datasets import DatasetRepository
from app.modules.datasets import service as datasets

MIB = 1024 * 1024
GIB = 1024 * MIB
DEFAULT_MAX_UPLOAD_BYTES = 10 * GIB
DEFAULT_MAX_EXPANDED_BYTES = 50 * GIB
DEFAULT_MAX_ARCHIVE_FILES = 200_000
STREAM_CHUNK_SIZE = 8 * MIB
MAX_INFO_JSON_BYTES = 2 * MIB
MAX_COMPRESSION_RATIO = 200


class UploadValidationError(ValueError):
    """上传内容不符合平台接收规则。"""


@dataclass(frozen=True, slots=True)
class UploadLimits:
    """上传和解压限制，可通过环境变量调整。"""

    max_upload_bytes: int
    max_expanded_bytes: int
    max_archive_files: int


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    """业务层返回给 HTTP 层的结果。"""

    status_code: int
    payload: dict[str, Any]


def _positive_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def get_upload_limits() -> UploadLimits:
    """读取上传限制；无效环境变量会安全回退到默认值。"""

    return UploadLimits(
        max_upload_bytes=_positive_env_int(
            "HKC_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES
        ),
        max_expanded_bytes=_positive_env_int(
            "HKC_MAX_EXPANDED_BYTES", DEFAULT_MAX_EXPANDED_BYTES
        ),
        max_archive_files=_positive_env_int(
            "HKC_MAX_ARCHIVE_FILES", DEFAULT_MAX_ARCHIVE_FILES
        ),
    )


def format_bytes(size: int | None) -> str:
    """把字节数转成页面可读文本。"""

    if size is None:
        return "-"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _display_filename(raw_name: str | None) -> str:
    if not raw_name:
        raise UploadValidationError("没有收到 ZIP 文件。")
    filename = PurePosixPath(raw_name.replace("\\", "/")).name.strip()
    if not filename.lower().endswith(".zip"):
        raise UploadValidationError("第一版只接受 .zip 格式的 LeRobot v3 数据集。")
    return filename


def _validate_form_text(dataset_name: str, description: str) -> tuple[str, str]:
    clean_name = dataset_name.strip()
    clean_description = description.strip()
    if not 2 <= len(clean_name) <= 100:
        raise UploadValidationError("数据集名称长度必须为 2 到 100 个字符。")
    if len(clean_description) > 1000:
        raise UploadValidationError("备注不能超过 1000 个字符。")
    return clean_name, clean_description


async def _stream_upload(
    upload: UploadFile,
    destination: Path,
    max_upload_bytes: int,
) -> int:
    """分块保存上传内容，避免把完整数据集读进内存。"""

    received = 0
    with destination.open("xb") as target:
        while chunk := await upload.read(STREAM_CHUNK_SIZE):
            received += len(chunk)
            if received > max_upload_bytes:
                raise UploadValidationError(
                    f"压缩包超过上传上限 {format_bytes(max_upload_bytes)}。"
                )
            target.write(chunk)

    if received == 0:
        raise UploadValidationError("上传的 ZIP 文件为空。")
    return received


def _safe_archive_path(info: ZipInfo) -> PurePosixPath:
    """把 ZIP 成员名规范化，同时拒绝路径穿越、绝对路径和符号链接。"""

    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if not name or name.startswith("/") or path.is_absolute():
        raise UploadValidationError("压缩包中存在绝对路径或空路径。")
    if any(part == ".." for part in path.parts):
        raise UploadValidationError("压缩包中存在不安全的 ../ 路径。")
    if path.parts and path.parts[0].endswith(":"):
        raise UploadValidationError("压缩包中存在 Windows 绝对路径。")
    if info.flag_bits & 0x1:
        raise UploadValidationError("不接受包含加密文件的 ZIP 压缩包。")

    file_type = (info.external_attr >> 16) & 0o170000
    if file_type == stat.S_IFLNK:
        raise UploadValidationError("不接受包含符号链接的 ZIP 压缩包。")
    return path


def _is_archive_noise(path: PurePosixPath) -> bool:
    return "__MACOSX" in path.parts or path.name in {".DS_Store", "Thumbs.db"}


def _find_dataset_root(paths: list[PurePosixPath]) -> tuple[str, ...]:
    info_suffix = ("meta", "info.json")
    candidates = {
        path.parts[: -len(info_suffix)]
        for path in paths
        if path.parts[-len(info_suffix) :] == info_suffix
    }
    if not candidates:
        raise UploadValidationError("缺少 LeRobot 必需文件 meta/info.json。")
    if len(candidates) != 1:
        raise UploadValidationError("一个 ZIP 中只能包含一个 LeRobot 数据集。")
    return next(iter(candidates))


def _inspect_archive(
    archive_path: Path,
    limits: UploadLimits,
) -> tuple[list[tuple[ZipInfo, PurePosixPath]], dict[str, Any], int]:
    """校验 ZIP 安全性和 LeRobot v3 最低目录结构。"""

    if not is_zipfile(archive_path):
        raise UploadValidationError("文件不是有效的 ZIP 压缩包。")

    try:
        archive = ZipFile(archive_path)
    except BadZipFile as exc:
        raise UploadValidationError("ZIP 压缩包已损坏，无法读取。") from exc

    with archive:
        inspected: list[tuple[ZipInfo, PurePosixPath]] = []
        expanded_bytes = 0

        for info in archive.infolist():
            path = _safe_archive_path(info)
            if info.is_dir() or _is_archive_noise(path):
                continue

            inspected.append((info, path))
            if len(inspected) > limits.max_archive_files:
                raise UploadValidationError(
                    f"压缩包文件数量超过上限 {limits.max_archive_files}。"
                )

            expanded_bytes += info.file_size
            if expanded_bytes > limits.max_expanded_bytes:
                raise UploadValidationError(
                    "解压后的总大小超过平台限制 "
                    f"{format_bytes(limits.max_expanded_bytes)}。"
                )
            if info.file_size > 100 * MIB:
                if info.compress_size == 0:
                    raise UploadValidationError("压缩包中存在异常高压缩率文件。")
                if info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                    raise UploadValidationError("压缩包中存在异常高压缩率文件。")

        if not inspected:
            raise UploadValidationError("ZIP 压缩包中没有可用文件。")

        prefix = _find_dataset_root([path for _, path in inspected])
        relative_members: list[tuple[ZipInfo, PurePosixPath]] = []
        seen_paths: set[PurePosixPath] = set()
        for info, path in inspected:
            if path.parts[: len(prefix)] != prefix:
                raise UploadValidationError(
                    "ZIP 根目录存在 LeRobot 数据集以外的文件，请只打包一个完整数据集。"
                )
            relative_path = PurePosixPath(*path.parts[len(prefix) :])
            if not relative_path.parts:
                continue
            if relative_path in seen_paths:
                raise UploadValidationError("ZIP 中存在重名文件，无法安全解压。")
            seen_paths.add(relative_path)
            relative_members.append((info, relative_path))

        relative_names = {path.as_posix() for _, path in relative_members}
        required_files = {
            "meta/info.json",
            "meta/stats.json",
            "meta/tasks.parquet",
        }
        missing = sorted(required_files - relative_names)
        if missing:
            raise UploadValidationError(
                "缺少 LeRobot v3 必需文件：" + "、".join(missing)
            )
        if not any(
            name.startswith("meta/episodes/") and name.endswith(".parquet")
            for name in relative_names
        ):
            raise UploadValidationError("缺少 meta/episodes/ 下的 Parquet 文件。")
        if not any(
            name.startswith("data/") and name.endswith(".parquet")
            for name in relative_names
        ):
            raise UploadValidationError("缺少 data/ 下的 Parquet 数据文件。")

        info_member = next(
            info
            for info, path in relative_members
            if path.as_posix() == "meta/info.json"
        )
        if info_member.file_size > MAX_INFO_JSON_BYTES:
            raise UploadValidationError("meta/info.json 文件异常过大。")
        try:
            info_data = json.loads(archive.read(info_member).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UploadValidationError("meta/info.json 不是有效的 UTF-8 JSON。") from exc
        if not isinstance(info_data, dict):
            raise UploadValidationError("meta/info.json 顶层必须是 JSON 对象。")

        version = str(info_data.get("codebase_version", "")).strip().lower()
        if version != "v3.0":
            shown_version = version or "未填写"
            raise UploadValidationError(
                f"当前只接受 LeRobot v3.0，压缩包版本为 {shown_version}。"
            )
        for integer_field in ("total_episodes", "total_frames"):
            value = info_data.get(integer_field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise UploadValidationError(
                    f"meta/info.json 中 {integer_field} 必须是正整数。"
                )
        fps = info_data.get("fps")
        if (
            isinstance(fps, bool)
            or not isinstance(fps, (int, float))
            or not math.isfinite(float(fps))
            or fps <= 0
        ):
            raise UploadValidationError("meta/info.json 中 fps 必须是正数。")
        if not isinstance(info_data.get("features"), dict) or not info_data["features"]:
            raise UploadValidationError("meta/info.json 中缺少有效的 features。")

        return relative_members, info_data, expanded_bytes


def _extract_validated_archive(
    archive_path: Path,
    destination: Path,
    limits: UploadLimits,
) -> dict[str, Any]:
    """完成整包校验后，逐文件安全解压到指定目录。"""

    members, info_data, expanded_bytes = _inspect_archive(archive_path, limits)
    free_bytes = shutil.disk_usage(destination.parent).free
    reserve_bytes = 512 * MIB
    if expanded_bytes > max(0, free_bytes - reserve_bytes):
        raise UploadValidationError("服务器剩余空间不足，无法安全解压该数据集。")

    destination.mkdir(parents=True, exist_ok=False)
    with ZipFile(archive_path) as archive:
        for info, relative_path in members:
            target = destination.joinpath(*relative_path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=STREAM_CHUNK_SIZE)

    return {
        "codebase_version": info_data["codebase_version"],
        "robot_type": info_data.get("robot_type") or "未声明",
        "fps": info_data["fps"],
        "total_episodes": info_data["total_episodes"],
        "total_frames": info_data["total_frames"],
        "expanded_bytes": expanded_bytes,
    }


def _record_path(data_root: Path, upload_id: str) -> Path:
    return data_root / "system" / "upload-records" / f"{upload_id}.json"


def _write_record(data_root: Path, record: dict[str, Any]) -> None:
    destination = _record_path(data_root, record["upload_id"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(destination)


def _failure_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "upload_id": record["upload_id"],
        "status": record["status"],
        "message": record["message"],
    }


async def receive_dataset_upload(
    *,
    data_root: Path,
    upload: UploadFile,
    dataset_name: str,
    description: str,
) -> UploadOutcome:
    """接收一个 ZIP，并在成功后将内容原子移动到 ``raw`` 目录。"""

    limits = get_upload_limits()
    upload_id = f"up-{uuid4().hex}"
    storage_name = f"ds-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    staging_dir = data_root / "staging" / "uploads" / upload_id
    archive_path = staging_dir / "archive.zip"
    unpacked_path = staging_dir / "unpacked"
    accepted_destination: Path | None = None
    registered_dataset_id: str | None = None
    record: dict[str, Any] = {
        "upload_id": upload_id,
        "dataset_id": None,
        "storage_name": None,
        "dataset_name": dataset_name.strip(),
        "description": description.strip(),
        "original_filename": upload.filename or "",
        "status": "receiving",
        "message": "正在接收文件",
        "received_bytes": 0,
        "created_at": _utc_now(),
        "completed_at": None,
        "raw_relative_path": None,
        "summary": None,
    }

    try:
        clean_name, clean_description = _validate_form_text(dataset_name, description)
        filename = _display_filename(upload.filename)
        record.update(
            dataset_name=clean_name,
            description=clean_description,
            original_filename=filename,
        )

        staging_dir.mkdir(parents=True, exist_ok=False)
        _write_record(data_root, record)
        received_bytes = await _stream_upload(
            upload,
            archive_path,
            limits.max_upload_bytes,
        )
        record.update(
            received_bytes=received_bytes,
            status="validating",
            message="上传完成，正在校验 LeRobot v3 结构",
        )
        _write_record(data_root, record)

        summary = await run_in_threadpool(
            _extract_validated_archive,
            archive_path,
            unpacked_path,
            limits,
        )
        raw_root = data_root / "raw"
        raw_root.mkdir(parents=True, exist_ok=True)
        raw_destination = raw_root / storage_name
        if raw_destination.exists():
            raise OSError("生成的数据集目录发生冲突")
        unpacked_path.replace(raw_destination)
        accepted_destination = raw_destination

        try:
            registered = await run_in_threadpool(
                datasets.register_dataset,
                data_root,
                "raw",
                storage_name,
                clean_name,
            )
        except ValueError as exc:
            shutil.rmtree(raw_destination, ignore_errors=True)
            accepted_destination = None
            raise OSError("数据集登记失败") from exc
        registered_dataset_id = registered["id"]

        record.update(
            dataset_id=registered["id"],
            storage_name=storage_name,
            status="accepted",
            message=f"LeRobot v3 数据集已入库并登记为 {registered['id']}",
            completed_at=_utc_now(),
            raw_relative_path=f"raw/{storage_name}",
            summary=summary,
        )
        _write_record(data_root, record)
        return UploadOutcome(
            status_code=201,
            payload={"ok": True, **record},
        )
    except UploadValidationError as exc:
        record.update(
            status="rejected",
            message=str(exc),
            completed_at=_utc_now(),
        )
        _write_record(data_root, record)
        return UploadOutcome(status_code=400, payload=_failure_payload(record))
    except (BadZipFile, OSError):
        if registered_dataset_id:
            DatasetRepository(data_root).unregister(registered_dataset_id)
        if accepted_destination:
            shutil.rmtree(accepted_destination, ignore_errors=True)
        record.update(
            status="failed",
            message="服务器保存或解压文件失败，请检查存储空间和目录权限。",
            completed_at=_utc_now(),
        )
        _write_record(data_root, record)
        return UploadOutcome(status_code=500, payload=_failure_payload(record))
    finally:
        await upload.close()
        shutil.rmtree(staging_dir, ignore_errors=True)


def list_upload_records(data_root: Path, limit: int = 20) -> list[dict[str, Any]]:
    """读取最近上传记录；损坏的旁路记录不会阻断页面。"""

    records_dir = data_root / "system" / "upload-records"
    if not records_dir.exists():
        return []

    records: list[dict[str, Any]] = []
    for path in records_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        record["received_size"] = format_bytes(record.get("received_bytes"))
        records.append(record)

    records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return records[:limit]
