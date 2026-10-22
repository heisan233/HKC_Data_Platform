"""Package derived datasets or CV conversion results without changing sources."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.db.datasets import DatasetRepository
from app.modules.conversion import service as conversion_service
from app.modules.datasets.service import dataset_info, directory

_DS_ID = re.compile(r"DS[0-9]{6,}")
_CV_ID = re.compile(r"CV-[A-F0-9]{12}")
_CHUNK = re.compile(r"chunk_[0-9]{3,}\.hdf5")
_lock = RLock()


def list_exportable(root: Path) -> list[dict]:
    """Expose registered Derived datasets and completed CV outputs."""
    result = []
    for record in DatasetRepository(root).list("derived"):
        folder = directory(root, "derived", record["storage_relpath"])
        if folder is None:
            continue
        result.append({
            "dataset_id": record["id"], "name": record["name"],
            "storage_area": "derived", "format": "LeRobot v3.0",
            "kind_label": "派生数据集",
        })
    for item in conversion_service.list_results(root):
        if item["available"]:
            result.append({
                "dataset_id": item["id"], "name": item["name"],
                "storage_area": "converted", "format": item["format"],
                "kind_label": "转换结果",
            })
    return result


def _inventory(folder: Path) -> list[tuple[str, Path, os.stat_result]]:
    """Reject links and unusual file types before adding anything to an archive."""
    files = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("数据集包含符号链接或越界路径")
        if path.is_file():
            files.append((path.relative_to(folder).as_posix(), path, path.stat()))
        elif not path.is_dir():
            raise ValueError("数据集包含不支持的文件类型")
    if not files:
        raise ValueError("数据集目录为空")
    return files


def _validate_converted(folder: Path, record: dict, files: list[tuple]) -> None:
    manifest_path = folder / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("转换结果清单缺失或无效")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("转换结果清单缺失或无效") from exc
    names = manifest.get("files") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("format") != record["format"]
            or not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not _CHUNK.fullmatch(name) for name in names)
            or len(names) != len(set(names))):
        raise ValueError("转换结果清单无效")
    if {relative for relative, _, _ in files} != {"manifest.json", *names}:
        raise ValueError("转换结果文件与清单不一致")


def _fingerprint(files: list[tuple[str, Path, os.stat_result]]) -> str:
    digest = hashlib.sha256()
    for relative, _, stat in files:
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def archive_dataset(root: Path, dataset_id: str) -> Path:
    """Create or reuse a tar.gz under exports/, keyed by the source snapshot."""
    if not _DS_ID.fullmatch(dataset_id) and not _CV_ID.fullmatch(dataset_id):
        raise ValueError("数据集编号不合法")
    if _CV_ID.fullmatch(dataset_id):
        task = conversion_service.get_task(root, dataset_id)
        if task is None or task["status"] != "COMPLETED":
            raise ValueError("可导出的转换结果不存在")
        record = {"storage_area": "converted", "storage_relpath": dataset_id, "format": "HDF5"}
    else:
        record = DatasetRepository(root).get(dataset_id)
        if record is None or record["storage_area"] != "derived":
            raise ValueError("可导出的数据集不存在")
    folder = directory(root, record["storage_area"], record["storage_relpath"])
    if folder is None:
        raise ValueError("数据集目录不存在或不安全")
    if record["storage_area"] == "derived":
        _, error = dataset_info(folder)
        if error:
            raise ValueError(error)
    files = _inventory(folder)
    if record["storage_area"] == "converted":
        _validate_converted(folder, record, files)
    snapshot = _fingerprint(files)
    exports = root / "exports"
    staging_root = root / "staging"
    staging = staging_root / "export"
    if exports.is_symlink() or staging_root.is_symlink() or staging.is_symlink():
        raise ValueError("导出存储目录不安全")
    exports.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    destination = exports / f"{dataset_id}-{snapshot[:16]}.tar.gz"
    if destination.is_symlink():
        raise ValueError("导出文件路径不安全")
    with _lock:
        if destination.is_file():
            return destination
        temporary = staging / f".{dataset_id}-{uuid4().hex}.tar.gz"
        try:
            with tarfile.open(temporary, mode="w:gz", compresslevel=1) as archive:
                for relative, path, _ in files:
                    if path.is_symlink() or not path.is_file():
                        raise ValueError("导出期间数据集文件发生变化")
                    archive.add(path, arcname=f"{dataset_id}/{relative}", recursive=False)
            if _fingerprint(_inventory(folder)) != snapshot:
                raise ValueError("导出期间数据集发生变化，请重试")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination
