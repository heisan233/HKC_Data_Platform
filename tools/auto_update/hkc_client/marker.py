"""Read and atomically write successful-upload markers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

MARKER_NAME = ".hkc-uploaded.json"
SCHEMA_VERSION = 1


def marker_path(source: Path) -> Path:
    if source.is_dir():
        return source / MARKER_NAME
    return source.with_name(source.name + MARKER_NAME)


def read_marker(source: Path) -> dict | None:
    path = marker_path(source)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def matches(marker: dict | None, server: str, robot_id: str) -> bool:
    if not marker:
        return False
    return (
        marker.get("schema_version") == SCHEMA_VERSION
        and str(marker.get("server") or "").rstrip("/") == server.rstrip("/")
        and marker.get("robot_id") == robot_id
        and bool(marker.get("dataset_id"))
    )


def write_marker(source: Path, dataset_id: str, robot_id: str, server: str) -> Path:
    path = marker_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "robot_id": robot_id,
        "server": server.rstrip("/"),
        # datetime.UTC only exists in Python 3.11; collectors target Ubuntu 22.04/Python 3.10.
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),  # noqa: UP017
    }
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path
