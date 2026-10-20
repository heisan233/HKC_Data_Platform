"""Safely ingest one or more complete LeRobot v3 ZIP datasets into raw/."""

from __future__ import annotations

import os
import shutil
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import UploadFile

from app.modules.datasets import service as datasets

MAX_ARCHIVE_BYTES = 20 * 1024 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024 * 1024
MAX_FILES = 200000


def _members(archive: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, PurePosixPath]], str | None]:
    entries = []
    total = 0
    seen = set()
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        path = PurePosixPath(name)
        mode = info.external_attr >> 16
        if (not name or "\\" in name or path.is_absolute() or
                any(part in {"", ".", ".."} for part in path.parts) or
                len(path.parts) > 24 or stat.S_ISLNK(mode) or
                info.flag_bits & 1):
            raise ValueError(f"ZIP 包含不安全路径或加密文件：{name[:100]}")
        if name in seen:
            raise ValueError("ZIP 包含重复文件路径")
        seen.add(name)
        total += info.file_size
        if len(entries) >= MAX_FILES or total > MAX_EXPANDED_BYTES:
            raise ValueError("ZIP 文件数或解压体积超过限制")
        if info.file_size > 100 * 1024 * 1024 and info.compress_size and \
                info.file_size / info.compress_size > 1000:
            raise ValueError("ZIP 压缩比异常")
        entries.append((info, path))
    if not entries:
        raise ValueError("ZIP 中没有文件")
    roots = {path.parts[0] for _, path in entries}
    strip = next(iter(roots)) if len(roots) == 1 and all(len(path.parts) > 1 for _, path in entries) else None
    return entries, strip


async def ingest(root: Path, upload: UploadFile) -> dict:
    if not upload.filename or not upload.filename.lower().endswith(".zip"):
        raise ValueError("请选择完整数据集 ZIP")
    staging = root / "staging"
    raw = root / "raw"
    staging.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    temp_id = uuid4().hex
    archive_path = staging / f"upload-{temp_id}.zip"
    extract_root = staging / f"extract-{temp_id}"
    target = None
    try:
        size = 0
        with archive_path.open("xb") as stream:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise ValueError("ZIP 超过 20 GB 上传限制")
                stream.write(chunk)
        with zipfile.ZipFile(archive_path) as archive:
            entries, strip = _members(archive)
            extract_root.mkdir()
            for info, path in entries:
                parts = path.parts[1:] if strip else path.parts
                if not parts:
                    continue
                destination = extract_root.joinpath(*parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("xb") as sink:
                    shutil.copyfileobj(source, sink, 1024 * 1024)
        info, error = datasets.dataset_info(extract_root)
        if error:
            raise ValueError(error)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        name = f"ds-{stamp}-{temp_id[:8]}"
        target = raw / name
        os.replace(extract_root, target)
        result = datasets.register_selected(root, [name], {name: Path(upload.filename).stem[:100]})
        if result["errors"]:
            raise ValueError(result["errors"][0]["message"])
        record = result["registered"][0]
        return {"filename": upload.filename, "folder": name, "dataset_id": record["id"],
                "robot_type": info["robot_type"], "size_bytes": size}
    except Exception:
        if target is not None and target.exists():
            shutil.rmtree(target)
        raise
    finally:
        await upload.close()
        archive_path.unlink(missing_ok=True)
        if extract_root.exists():
            shutil.rmtree(extract_root)
