"""Dataset storage helpers and the shared DS identifier resolution layer."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from app.db.datasets import STORAGE_AREAS, DatasetRepository

MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_LIST_FILES = 500


def directory(root: Path, area: str, name: str) -> Path | None:
    """Resolve one direct child without following links outside its storage area."""
    if area not in STORAGE_AREAS and area != "qc":
        return None
    if not name or name in {".", ".."} or Path(name).name != name or name.startswith("."):
        return None
    try:
        parent = root / area
        child = parent / name
        if parent.is_symlink() or child.is_symlink() or not child.is_dir():
            return None
        if child.resolve().parent != parent.resolve():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return child


def safe_file(root: Path, relative: str) -> Path | None:
    part = Path(relative)
    if part.is_absolute() or not part.parts or any(p in {"", ".", ".."} for p in part.parts):
        return None
    try:
        base = root.resolve(strict=True)
        target = (root / part).resolve(strict=True)
        if not target.is_relative_to(base) or not target.is_file():
            return None
        current = root
        for name in part.parts:
            current = current / name
            if current.is_symlink():
                return None
        return target
    except (OSError, RuntimeError, ValueError):
        return None


def read_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


def size_label(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "—"


def dataset_info(folder: Path) -> tuple[dict | None, str | None]:
    path = safe_file(folder, "meta/info.json")
    if path is None:
        return None, "未找到可读取的 meta/info.json"
    data = read_json(path)
    if data is None:
        return None, "meta/info.json 不是有效的 JSON 对象或文件过大"
    if not str(data.get("codebase_version", "")).startswith("v3"):
        return None, "仅支持 LeRobot v3 数据集"
    if not isinstance(data.get("robot_type"), str) or not data["robot_type"].strip():
        return None, "meta/info.json 缺少 robot_type"
    return data, None


def info_digest(folder: Path) -> str:
    path = safe_file(folder, "meta/info.json")
    if path is None:
        raise ValueError("元数据文件不可读取")
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("元数据文件过大")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_registered_dataset(
    root: Path,
    dataset_id: str,
    allowed_areas: tuple[str, ...] = ("raw", "derived"),
) -> tuple[dict, Path]:
    """Resolve a public DS identifier to its safe on-disk folder."""
    record = DatasetRepository(root).get(dataset_id)
    if record is None:
        raise ValueError("数据集未登记")
    if record["storage_area"] not in allowed_areas:
        raise ValueError("数据集所在存储区域不允许执行此操作")
    folder = directory(root, record["storage_area"], record["storage_relpath"])
    if folder is None:
        raise ValueError("数据集目录缺失或路径不安全")
    return record, folder


def register_dataset(
    root: Path,
    storage_area: str,
    storage_relpath: str,
    display_name: str,
    source_dataset_ids: list[str] | None = None,
) -> dict:
    folder = directory(root, storage_area, storage_relpath)
    if folder is None:
        raise ValueError("目录不存在或路径不安全")
    info, error = dataset_info(folder)
    if error:
        raise ValueError(error)
    name = display_name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("显示名称须为 1–100 个字符")
    return DatasetRepository(root).register(
        storage_area,
        storage_relpath,
        name,
        info["robot_type"].strip(),
        info_digest(folder),
        source_dataset_ids,
    )


def folder_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                if path.resolve().is_relative_to(folder.resolve()):
                    total += path.stat().st_size
            except OSError:
                pass
    return total


def summary(root: Path, name: str, registered: dict | None = None, area: str = "raw") -> dict:
    if registered:
        area = registered["storage_area"]
        name = registered["storage_relpath"]
    folder = directory(root, area, name)
    info, error = dataset_info(folder) if folder else (None, "数据集目录缺失")
    report_count = len(reports_for(root, registered["id"])) if registered else 0
    try:
        changed = bool(registered and folder and not error
                       and registered["info_sha256"] != info_digest(folder))
    except (OSError, ValueError):
        changed = True
    state = "missing" if not folder else "invalid" if error else "changed" if changed else "ready"
    return {
        "id": registered["id"] if registered else None,
        "name": registered["name"] if registered else name,
        "folder": name,
        "storage_area": area,
        "robot_type": registered["robot_type"] if registered else (info or {}).get("robot_type", "—"),
        "episodes": (info or {}).get("total_episodes"),
        "frames": (info or {}).get("total_frames"),
        "fps": (info or {}).get("fps"),
        "version": (info or {}).get("codebase_version", "—"),
        "state": state,
        "error": error,
        "report_count": report_count,
        "registered_at": registered["registered_at"] if registered else None,
        "description": registered["description"] if registered else "",
        "tags": registered["tags"] if registered else [],
    }


def catalog(root: Path) -> tuple[list[dict], list[dict]]:
    """Return unregistered raw folders and all DS-registered datasets."""
    repo = DatasetRepository(root)
    records = repo.list()
    registered_raw = {
        row["storage_relpath"] for row in records if row["storage_area"] == "raw"
    }
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    pending = [summary(root, path.name) for path in sorted(raw.iterdir(), key=lambda p: p.name)
               if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
               and path.name not in registered_raw]
    managed = [summary(root, row["storage_relpath"], row, row["storage_area"]) for row in records]
    return pending, managed


def register_selected(root: Path, folders: list[str], names: dict[str, str] | None = None) -> dict:
    results = []
    errors = []
    for name in dict.fromkeys(folders):
        display_name = (names or {}).get(name, name).strip()
        try:
            record = register_dataset(root, "raw", name, display_name)
            results.append({"folder": name, "id": record["id"]})
        except ValueError as exc:
            errors.append({"folder": name, "message": str(exc)})
    return {"registered": results, "errors": errors}


def files_in(folder: Path, limit: int = MAX_LIST_FILES) -> list[dict]:
    rows = []
    for path in sorted(folder.rglob("*")):
        if len(rows) >= limit:
            break
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(folder).as_posix()
            if safe_file(folder, relative):
                rows.append({"path": relative, "size": size_label(path.stat().st_size)})
    return rows


def detail(root: Path, record: dict) -> dict:
    area = record["storage_area"]
    folder_name = record["storage_relpath"]
    folder = directory(root, area, folder_name)
    if folder is None:
        return {"folder": None, "info": {}, "stats": {}, "files": [], "cameras": [], "size": "—"}
    info = read_json(safe_file(folder, "meta/info.json")) or {}
    stats = read_json(safe_file(folder, "meta/stats.json")) or {}
    cameras = []
    features = info.get("features")
    features = features if isinstance(features, dict) else {}
    for key, feature in features.items():
        if not isinstance(feature, dict) or feature.get("dtype") != "video":
            continue
        if not isinstance(key, str) or "/" in key or "\\" in key or key in {".", ".."}:
            continue
        camera_dir = folder / "videos" / key
        videos = []
        if camera_dir.is_dir() and not camera_dir.is_symlink():
            for file in sorted(camera_dir.rglob("*.mp4"))[:100]:
                relative = file.relative_to(folder / "videos").as_posix()
                if safe_file(folder / "videos", relative):
                    videos.append({"name": file.name, "url": f"/data/datasets/{quote(record['id'])}/video/{quote(relative, safe='/')}"})
        cameras.append({"key": key, "videos": videos})
    return {"folder": folder_name, "info": info, "stats": stats, "features": features,
            "files": files_in(folder), "cameras": cameras, "size": size_label(folder_size(folder))}


def reports_for(root: Path, dataset_id: str) -> list[dict]:
    folder = directory(root / "reports", "qc", dataset_id)
    if folder is None:
        return []
    rows = []
    for file in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if file.is_symlink() or not file.is_file() or file.name.startswith("."):
            continue
        data = read_json(file) if file.suffix.lower() == ".json" else None
        rows.append({"name": file.name, "size": size_label(file.stat().st_size),
                     "created_at": datetime.fromtimestamp(file.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M"),
                     "decision": (data or {}).get("decision", "—"), "status": (data or {}).get("status", "—")})
    return rows
