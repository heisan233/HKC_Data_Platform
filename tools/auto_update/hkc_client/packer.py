"""Package one LeRobot folder without including local upload markers."""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

from .errors import CollectorError
from .marker import MARKER_NAME

CHUNK_SIZE = 8 * 1024 * 1024


def sha256(path: Path, *, show_progress: bool = True) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    completed = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            completed += len(chunk)
            if show_progress and total:
                print(f"\r  计算 SHA-256：{completed * 100 / total:5.1f}%", end="", flush=True)
    if show_progress:
        print()
    return digest.hexdigest()


def package_folder(source: Path, temporary_dir: Path) -> Path:
    temporary_dir.mkdir(parents=True, exist_ok=True)
    target = temporary_dir / f"{source.name}-{os.getpid()}.zip"
    if target.exists():
        target.unlink()
    files = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise CollectorError(f"{source.name}：不接受数据集内的符号链接：{path}")
        if not path.is_file():
            continue
        if path.name == MARKER_NAME or path.name.endswith(MARKER_NAME):
            continue
        files.append(path)
    if not files:
        raise CollectorError(f"{source.name}：文件夹为空")
    try:
        with zipfile.ZipFile(
            target,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            for path in sorted(files, key=lambda item: item.as_posix()):
                archive.write(path, (Path(source.name) / path.relative_to(source)).as_posix())
    except (OSError, zipfile.BadZipFile) as exc:
        target.unlink(missing_ok=True)
        raise CollectorError(f"{source.name}：本地打包失败：{exc}") from exc
    return target
