"""Persistent, privacy-conscious application request logs."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOG_RELATIVE_PATH = Path("system/logs/platform.jsonl")
MAX_SCAN_BYTES = 4 * 1024 * 1024
MAX_SCAN_LINES = 5_000
_write_lock = threading.Lock()

MODULE_LABELS = {
    "dashboard": "工作台",
    "robots": "机器人管理",
    "semantics": "数据定义",
    "data": "数据管理",
    "ingestion": "数据集上传",
    "qc": "数据质检",
    "annotation": "数据标注",
    "preprocess": "数据处理",
    "export": "转换与导出",
    "training": "模型训练",
    "system": "系统管理",
    "logout": "用户认证",
}


def module_label(path: str) -> str:
    segment = path.strip("/").split("/", 1)[0]
    return MODULE_LABELS.get(segment, "平台服务")


def _log_file(root: Path, *, create: bool) -> Path | None:
    try:
        system = root / "system"
        logs = system / "logs"
        file = root / LOG_RELATIVE_PATH
        if system.is_symlink() or logs.is_symlink() or file.is_symlink():
            return None
        if create:
            system.mkdir(exist_ok=True)
            logs.mkdir(exist_ok=True)
        if not logs.is_dir() or logs.resolve().parent != system.resolve():
            return None
        return file
    except OSError:
        return None


def write_request_log(
    root: Path,
    *,
    username: str,
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
) -> None:
    """Append one request event without query strings, bodies, cookies or headers."""

    file = _log_file(root, create=True)
    if file is None:
        return
    level = "ERROR" if status_code >= 500 else "WARNING" if status_code >= 400 else "INFO"
    event = {
        "time": datetime.now(UTC).isoformat(),
        "level": level,
        "module": module_label(path),
        "username": username[:64],
        "method": method[:10],
        "path": path[:500],
        "status_code": int(status_code),
        "duration_ms": round(max(0.0, duration_ms), 2),
    }
    try:
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _write_lock, file.open("a", encoding="utf-8") as stream:
            stream.write(line)
    except OSError:
        # Observability must never break a request when storage is unavailable.
        return


def _tail_lines(file: Path) -> list[str]:
    try:
        size = file.stat().st_size
        with file.open("rb") as stream:
            stream.seek(max(0, size - MAX_SCAN_BYTES))
            data = stream.read(MAX_SCAN_BYTES)
        if size > MAX_SCAN_BYTES:
            data = data.split(b"\n", 1)[-1]
        return data.decode("utf-8", errors="replace").splitlines()[-MAX_SCAN_LINES:]
    except OSError:
        return []


def _read_events(root: Path) -> list[dict[str, Any]]:
    file = _log_file(root, create=False)
    if file is None or not file.is_file():
        return []
    events = []
    for line in _tail_lines(file):
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict) and isinstance(event.get("time"), str):
            events.append(event)
    events.reverse()
    return events


def log_snapshot(
    root: Path,
    *,
    level: str = "ALL",
    module: str = "ALL",
    query: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Return filtered recent events plus statistics for the scanned window."""

    events = _read_events(root)
    today = datetime.now(UTC).astimezone().date()
    stats = {
        "total": len(events),
        "error": sum(event.get("level") == "ERROR" for event in events),
        "warning": sum(event.get("level") == "WARNING" for event in events),
        "today": 0,
    }
    for event in events:
        try:
            if datetime.fromisoformat(event["time"]).astimezone().date() == today:
                stats["today"] += 1
        except (TypeError, ValueError):
            pass

    normalized_level = level.upper()
    normalized_module = module.strip()
    term = query.strip().casefold()
    filtered = []
    for event in events:
        if normalized_level != "ALL" and event.get("level") != normalized_level:
            continue
        if normalized_module != "ALL" and event.get("module") != normalized_module:
            continue
        searchable = " ".join(
            str(event.get(key) or "")
            for key in ("module", "username", "method", "path", "status_code")
        ).casefold()
        if term and term not in searchable:
            continue
        filtered.append(event)

    modules = sorted({str(event.get("module")) for event in events if event.get("module")})
    return {
        "logs": filtered[:max(1, min(int(limit), 500))],
        "stats": stats,
        "modules": modules,
        "matched": len(filtered),
    }
