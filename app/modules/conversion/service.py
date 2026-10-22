"""Single-worker conversion jobs with SQLite metadata and file-based results."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

import h5py

from app.db.conversions import ConversionRepository
from app.db.datasets import DatasetRepository
from app.modules.conversion import converter
from app.modules.datasets.service import resolve_registered_dataset

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hkc-conversion")
_lock = RLock()
_initialized: set[Path] = set()
_logger = logging.getLogger(__name__)
_ID = re.compile(r"CV-[A-F0-9]{12}")
_CHUNK = re.compile(r"chunk_[0-9]{3,}\.hdf5")
PROFILE_ID = "lerobot-v3-hdf5"
PROFILES = ({
    "id": PROFILE_ID,
    "source_format": "LeRobot v3.0",
    "target_format": "HDF5",
    "title": "LeRobot v3.0 → HDF5",
    "description": "适用于已登记的派生数据集；每个 HDF5 文件最多包含 10 个 Episode。",
    "available": True,
},)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _job_path(root: Path, task_id: str) -> Path:
    if not _ID.fullmatch(task_id):
        raise ValueError("转换任务编号不合法")
    return root / "system" / "conversion-jobs" / f"{task_id}.json"


def _read(root: Path, task_id: str) -> dict | None:
    path = _job_path(root, task_id)
    if path.is_symlink() or not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    return record if isinstance(record, dict) and record.get("id") == task_id else None


def _inventory(folder: Path) -> list[tuple[str, os.stat_result]]:
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("数据集目录不存在或不安全")
    files = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("数据集包含符号链接或越界路径")
        if path.is_file():
            files.append((path.relative_to(folder).as_posix(), path.stat()))
        elif not path.is_dir():
            raise ValueError("数据集包含不支持的文件类型")
    if not files:
        raise ValueError("数据集目录为空")
    return files


def _fingerprint(folder: Path) -> str:
    digest = hashlib.sha256()
    for relative, stat in _inventory(folder):
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def _source(root: Path, dataset_id: str) -> tuple[dict, Path]:
    if not re.fullmatch(r"DS[0-9]{6,}", dataset_id):
        raise ValueError("数据集编号不合法")
    return resolve_registered_dataset(root, dataset_id, allowed_areas=("derived",))


def _manifest(folder: Path) -> dict:
    path = folder / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("转换结果缺少 manifest.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get("format") != "HDF5" or not isinstance(files, list) or not files:
        raise ValueError("转换结果清单无效")
    if not isinstance(manifest.get("total_episodes"), int) or not isinstance(manifest.get("total_frames"), int):
        raise TypeError("转换结果清单缺少规模信息")
    for name in files:
        if not isinstance(name, str) or not _CHUNK.fullmatch(name):
            raise ValueError("转换结果清单包含无效文件名")
        file = folder / name
        if file.is_symlink() or not file.is_file():
            raise ValueError("转换结果缺少 HDF5 文件")
        with h5py.File(file, "r") as output:
            if "data" not in output or "meta" not in output:
                raise ValueError("转换结果 HDF5 内容不完整")
    if len(files) != len(set(files)):
        raise ValueError("转换结果清单包含重复文件")
    return manifest


def list_sources(root: Path) -> list[dict]:
    result = []
    for record in DatasetRepository(root).list("derived"):
        try:
            _, folder = _source(root, record["id"])
            info = converter.source_info(folder)
            if not (folder / "meta" / "episodes").is_dir():
                continue
            result.append({
                "dataset_id": record["id"], "name": record["name"],
                "total_episodes": info["total_episodes"], "total_frames": info["total_frames"],
            })
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return result


def initialize(root: Path) -> None:
    with _lock:
        if root in _initialized:
            return
        repo = ConversionRepository(root)
        repo.initialize()
        directory = root / "system" / "conversion-jobs"
        if directory.is_dir() and not directory.is_symlink():
            for path in sorted(directory.glob("CV-*.json")):
                try:
                    task = _read(root, path.stem)
                    if task is None or task.get("status") not in {"QUEUED", "RUNNING", "COMPLETED", "FAILED"}:
                        continue
                    repo.import_legacy(task)
                    stored = repo.get(task["id"])
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                    _logger.warning("跳过无法迁移的转换任务：%s", path.name)
        repo.interrupted(_now())
        _initialized.add(root)
        for task in repo.list(limit=10000):
            if task["status"] == "QUEUED":
                _executor.submit(_run, root, task["id"])


def get_task(root: Path, task_id: str) -> dict | None:
    if not _ID.fullmatch(task_id):
        raise ValueError("转换任务编号不合法")
    initialize(root)
    return ConversionRepository(root).get(task_id)


def list_tasks(root: Path, limit: int = 50) -> list[dict]:
    initialize(root)
    return ConversionRepository(root).list(limit)


def list_results(root: Path) -> list[dict]:
    """Present completed CV outputs separately from the DS registration list."""
    initialize(root)
    results = []
    converted_root = root / "converted"
    for task in ConversionRepository(root).list_completed():
        if not _ID.fullmatch(task["id"]):
            continue
        folder = converted_root / task["id"]
        available = not converted_root.is_symlink() and folder.is_dir() and not folder.is_symlink()
        if available:
            manifest_file = folder / "manifest.json"
            available = manifest_file.is_file() and not manifest_file.is_symlink()
            available = available and all(
                isinstance(name, str) and _CHUNK.fullmatch(name) and (folder / name).is_file()
                and not (folder / name).is_symlink() for name in task["files"]
            ) and bool(task["files"])
        results.append({
            "id": task["id"], "name": f"{task['source_name']} · HDF5",
            "source_dataset_id": task["source_dataset_id"],
            "format": "HDF5", "episodes": task["total_episodes"],
            "finished_at": task["finished_at"], "available": available,
        })
    return results


def create_task(root: Path, dataset_id: str, profile_id: str = PROFILE_ID) -> dict:
    if profile_id != PROFILE_ID:
        raise ValueError("暂不支持所选转换方案")
    record, source = _source(root, dataset_id)
    info = converter.source_info(source)
    converter.episodes(source, info)
    digest = _fingerprint(source)
    initialize(root)
    with _lock:
        task_id = f"CV-{uuid4().hex[:12].upper()}"
        task = {
            "id": task_id, "profile_id": profile_id,
            "source_dataset_id": dataset_id, "source_name": record["name"],
            "source_fingerprint": digest, "status": "QUEUED", "completed_episodes": 0,
            "total_episodes": info["total_episodes"], "files": [],
            "error": None,
            "created_at": _now(), "started_at": None, "finished_at": None,
        }
        ConversionRepository(root).create(task)
        _executor.submit(_run, root, task_id)
        return task.copy()


def _update(root: Path, task_id: str, **changes: object) -> None:
    ConversionRepository(root).update(task_id, **changes)


def _run(root: Path, task_id: str) -> None:
    with _lock:
        repo = ConversionRepository(root)
        task = repo.get(task_id)
        if task is None or task["status"] != "QUEUED":
            return
        repo.update(task_id, status="RUNNING", started_at=_now())
    parent = root / "converted"
    staging_parent = root / "staging" / "conversion"
    staging = staging_parent / task_id
    output = parent / task_id
    try:
        _, source = _source(root, task["source_dataset_id"])
        if _fingerprint(source) != task["source_fingerprint"]:
            raise ValueError("来源数据集已变化，请重新创建转换任务")
        if parent.is_symlink() or (root / "staging").is_symlink() or staging_parent.is_symlink():
            raise ValueError("转换存储目录不能是符号链接")
        parent.mkdir(parents=True, exist_ok=True)
        staging_parent.mkdir(parents=True, exist_ok=True)
        if staging.exists() or staging.is_symlink() or output.exists() or output.is_symlink():
            raise ValueError("转换任务输出目录已存在")
        manifest = converter.convert(
            source, staging, lambda count: _update(root, task_id, completed_episodes=count)
        )
        if _fingerprint(source) != task["source_fingerprint"]:
            raise ValueError("转换期间来源数据集发生变化")
        manifest["source_dataset_id"] = task["source_dataset_id"]
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _manifest(staging)
        os.replace(staging, output)
        repo.complete(task, manifest, _now())
    except Exception as exc:
        _logger.exception("转换任务失败：%s", task_id)
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        _update(root, task_id, status="FAILED", error=str(exc)[:1000], finished_at=_now())


def result_file(root: Path, task_id: str, filename: str) -> Path:
    task = get_task(root, task_id)
    if task is None or task.get("status") != "COMPLETED":
        raise ValueError("转换结果不存在")
    folder = root / "converted" / task_id
    if (root / "converted").is_symlink() or folder.is_symlink() or not folder.is_dir():
        raise ValueError("转换结果目录不安全")
    manifest = _manifest(folder)
    if filename != "manifest.json" and filename not in manifest["files"]:
        raise ValueError("转换结果文件不存在")
    file = folder / filename
    if file.is_symlink() or not file.is_file():
        raise ValueError("转换结果文件不存在")
    return file
