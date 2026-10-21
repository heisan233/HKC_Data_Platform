"""Discover datasets, run conversions, and stream downloadable archives."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Full, Queue
from threading import Event, Lock, Thread

from app.db.exports import ConversionRepository
from app.modules.export import converter

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hkc-conversion")
_startup_lock = Lock()
_started_roots: set[Path] = set()
_logger = logging.getLogger(__name__)
_END = object()


def direct_child(root: Path, area: str, name: str) -> Path | None:
    if area not in {"derived", "converted"} or not name or name.startswith("."):
        return None
    if Path(name).name != name or name in {".", ".."}:
        return None
    parent = root / area
    path = parent / name
    if parent.is_symlink() or path.is_symlink() or not path.is_dir():
        return None
    if path.resolve().parent != parent.resolve():
        return None
    return path


def inventory(folder: Path) -> list[tuple[Path, str, os.stat_result]]:
    """Collect regular files and reject all symlinks before reading or exporting."""
    rows = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("数据集含符号链接，不能转换或导出")
        if path.is_file():
            if not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError("数据集文件超出来源目录")
            rows.append((path, path.relative_to(folder).as_posix(), path.stat()))
        elif not path.is_dir():
            raise ValueError("数据集含不支持的文件类型")
    if not rows:
        raise ValueError("数据集目录为空")
    return rows


def fingerprint(folder: Path) -> str:
    digest = hashlib.sha256()
    for _, relative, stat in inventory(folder):
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def _converted_manifest(folder: Path) -> dict:
    path = folder / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("转换结果缺少 manifest.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get("format") != "HDF5" or not isinstance(files, list) or not files:
        raise ValueError("转换结果清单无效")
    for filename in files:
        if not isinstance(filename, str) or re.fullmatch(r"chunk_[0-9]{3,}\.hdf5", filename) is None:
            raise ValueError("转换结果清单包含无效文件名")
        path = folder / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError("转换结果缺少 HDF5 文件")
    return manifest


def list_datasets(root: Path, area: str) -> list[dict]:
    if area not in {"derived", "converted"}:
        raise ValueError("不支持的数据集区域")
    parent = root / area
    if not parent.is_dir() or parent.is_symlink():
        return []
    result = []
    for entry in sorted(parent.iterdir(), key=lambda item: item.name):
        folder = direct_child(root, area, entry.name)
        if folder is None:
            continue
        try:
            if area == "derived":
                info = converter.source_info(folder)
                if not (folder / "meta" / "episodes").is_dir():
                    raise ValueError("缺少 episode 元数据")
                result.append({"name": entry.name, "area": area, "format": "LeRobot v3.0",
                               "episodes": info["total_episodes"], "frames": info.get("total_frames")})
            else:
                manifest = _converted_manifest(folder)
                result.append({"name": entry.name, "area": area, "format": "HDF5",
                               "episodes": manifest.get("total_episodes"),
                               "frames": manifest.get("total_frames")})
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return result


def initialize(root: Path) -> ConversionRepository:
    repo = ConversionRepository(root)
    with _startup_lock:
        if root not in _started_roots:
            repo.interrupt_running()
            _started_roots.add(root)
            for task_id in repo.queued_ids():
                _submit(root, task_id)
        else:
            repo.initialize()
    return repo


def create_task(root: Path, source_name: str) -> dict:
    folder = direct_child(root, "derived", source_name)
    if folder is None:
        raise ValueError("derived 来源数据集不存在")
    info = converter.source_info(folder)
    converter.episodes(folder, info)
    source_fingerprint = fingerprint(folder)
    repo = initialize(root)
    task = repo.create(source_name, source_fingerprint, info["total_episodes"])
    _submit(root, task["id"])
    return task


def _submit(root: Path, task_id: str) -> None:
    try:
        _executor.submit(_run_task, root, task_id)
    except RuntimeError as exc:
        _logger.exception("无法启动转换任务 %s", task_id)
        repo = ConversionRepository(root)
        if repo.start(task_id):
            repo.finish(task_id, f"无法启动转换执行器：{exc}")


def _run_task(root: Path, task_id: str) -> None:
    repo = ConversionRepository(root)
    task = repo.get(task_id)
    if task is None or not repo.start(task_id):
        return
    staging = root / "converted" / f".{task_id}.tmp"
    output = root / "converted" / task_id
    try:
        source = direct_child(root, "derived", task["source_name"])
        if source is None or fingerprint(source) != task["source_fingerprint"]:
            raise ValueError("来源数据集已变化，请重新创建转换任务")
        if output.parent.is_symlink():
            raise ValueError("converted 目录不能是符号链接")
        output.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists() or staging.is_symlink() or output.exists() or output.is_symlink():
            raise ValueError("转换任务的输出目录已存在")
        converter.convert(source, staging, lambda count: repo.progress(task_id, count))
        if fingerprint(source) != task["source_fingerprint"]:
            raise ValueError("转换期间来源数据集发生变化")
        _converted_manifest(staging)
        os.replace(staging, output)
        repo.finish(task_id)
    except Exception as exc:
        _logger.exception("转换任务失败：%s", task_id)
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        repo.finish(task_id, str(exc)[:1000])


class _QueueWriter:
    def __init__(self, queue: Queue, stopped: Event):
        self.queue = queue
        self.stopped = stopped

    def write(self, data: bytes) -> int:
        while not self.stopped.is_set():
            try:
                self.queue.put(data, timeout=0.2)
                return len(data)
            except Full:
                continue
        raise BrokenPipeError("客户端下载已中断")

    def flush(self) -> None:
        pass


def archive_stream(folder: Path) -> Iterator[bytes]:
    """Stream a tar.gz archive without keeping an exports/ copy on disk."""
    files = inventory(folder)
    queue: Queue = Queue(maxsize=8)
    stopped = Event()

    def put_last(item: object) -> None:
        while not stopped.is_set():
            try:
                queue.put(item, timeout=0.2)
                return
            except Full:
                continue

    def produce() -> None:
        try:
            with tarfile.open(fileobj=_QueueWriter(queue, stopped), mode="w|gz") as archive:
                for path, relative, stat in files:
                    if path.is_symlink() or not path.is_file():
                        raise ValueError("打包期间数据集文件发生变化")
                    entry = tarfile.TarInfo(f"{folder.name}/{relative}")
                    entry.size = stat.st_size
                    entry.mtime = int(stat.st_mtime)
                    entry.mode = 0o644
                    with path.open("rb") as stream:
                        archive.addfile(entry, stream)
        except Exception as exc:  # noqa: BLE001 - pass streaming errors to the consumer
            put_last(exc)
        finally:
            put_last(_END)

    thread = Thread(target=produce, name="hkc-export-archive", daemon=True)
    thread.start()
    try:
        while True:
            item = queue.get()
            if item is _END:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stopped.set()
        thread.join(timeout=1)


def export_folder(root: Path, area: str, name: str) -> Path | None:
    folder = direct_child(root, area, name)
    if folder is None:
        return None
    if area == "derived":
        converter.source_info(folder)
    else:
        _converted_manifest(folder)
    inventory(folder)
    return folder
