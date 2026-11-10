"""Find immediate LeRobot dataset candidates in one collection directory."""

from __future__ import annotations

from pathlib import Path


def scan_directory(parent: Path) -> list[Path]:
    if not parent.is_dir():
        raise ValueError(f"扫描目录不存在或不是目录：{parent}")
    candidates = []
    for child in parent.iterdir():
        if child.name.startswith(".") or child.is_symlink():
            continue
        if child.is_dir() or (child.is_file() and child.suffix.casefold() == ".zip"):
            candidates.append(child.resolve())
    return sorted(candidates, key=lambda path: path.name.casefold())


def normalize_sources(sources: list[Path]) -> list[Path]:
    normalized = []
    for source in sources:
        path = source.expanduser().resolve()
        if path.is_symlink():
            raise ValueError(f"不接受符号链接：{path}")
        if path.is_dir() or (path.is_file() and path.suffix.casefold() == ".zip"):
            normalized.append(path)
            continue
        raise ValueError(f"只接受 LeRobot v3 文件夹或 .zip：{path}")
    return normalized
