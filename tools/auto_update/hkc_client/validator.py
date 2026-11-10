"""Read the dataset metadata needed before a local upload."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from .errors import CollectorError


def _validate_info(info: object, source: Path) -> dict:
    if not isinstance(info, dict):
        raise CollectorError(f"{source.name}：meta/info.json 顶层必须是 JSON 对象")
    version = str(info.get("codebase_version") or "").strip().lower().lstrip("v")
    if not version.startswith("3"):
        raise CollectorError(
            f"{source.name}：仅支持 LeRobot v3，当前 codebase_version={version or '缺失'}"
        )
    return info


def dataset_info(source: Path) -> dict:
    if source.is_dir():
        info_path = source / "meta" / "info.json"
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CollectorError(f"{source.name}：缺少 meta/info.json") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectorError(f"{source.name}：meta/info.json 不是有效 UTF-8 JSON") from exc
        return _validate_info(info, source)

    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise CollectorError(f"{source.name}：无法读取 ZIP：{exc}") from exc
    with archive:
        candidates = []
        for name in archive.namelist():
            path = PurePosixPath(name.replace("\\", "/"))
            if path.parts[-2:] == ("meta", "info.json"):
                candidates.append(name)
        if len(candidates) != 1:
            raise CollectorError(f"{source.name}：ZIP 必须且只能包含一个 meta/info.json")
        try:
            info = json.loads(archive.read(candidates[0]).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectorError(f"{source.name}：meta/info.json 不是有效 UTF-8 JSON") from exc
    return _validate_info(info, source)


def require_matching_robot_type(info: dict, source: Path, expected: str) -> str:
    actual = str(info.get("robot_type") or "").strip()
    if not actual:
        raise CollectorError(
            f"{source.name}：meta/info.json 缺少 robot_type；自动模式不会代替人工确认"
        )
    if actual.casefold() != expected.strip().casefold():
        raise CollectorError(
            f"{source.name}：机器人类型不一致（数据集={actual}，Token={expected}）"
        )
    return actual
