"""Read external raw datasets and join them with platform registrations."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from app.db.datasets import DatasetRepository


MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_LIST_FILES = 500


def directory(root: Path, area: str, name: str) -> Path | None:
    """Resolve one direct child without following links outside its storage area."""
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
        # Reject any symlink in the original path, including parent components.
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


def summary(root: Path, name: str, registered: dict | None = None) -> dict:
    folder = directory(root, "raw", name)
    info, error = dataset_info(folder) if folder else (None, "原始数据目录缺失")
    detected = directory(root, "detected", registered["id"]) if registered else None
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
        "robot_type": registered["robot_type"] if registered else (info or {}).get("robot_type", "—"),
        "episodes": (info or {}).get("total_episodes"),
        "frames": (info or {}).get("total_frames"),
        "fps": (info or {}).get("fps"),
        "version": (info or {}).get("codebase_version", "—"),
        "state": state,
        "error": error,
        "detected": bool(detected),
        "report_count": report_count,
        "registered_at": registered["registered_at"] if registered else None,
        "description": registered["description"] if registered else "",
        "tags": registered["tags"] if registered else [],
    }


def catalog(root: Path) -> tuple[list[dict], list[dict]]:
    repo = DatasetRepository(root)
    repo.initialize()
    records = repo.list()
    registered_paths = {row["raw_relpath"] for row in records}
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    pending = [summary(root, path.name) for path in sorted(raw.iterdir(), key=lambda p: p.name)
               if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
               and path.name not in registered_paths]
    managed = [summary(root, row["raw_relpath"], row) for row in records]
    return pending, managed


def register_selected(root: Path, folders: list[str], names: dict[str, str] | None = None) -> dict:
    repo = DatasetRepository(root)
    repo.initialize()
    results = []
    errors = []
    for name in dict.fromkeys(folders):
        display_name = (names or {}).get(name, name).strip()
        if not 1 <= len(display_name) <= 100:
            errors.append({"folder": name, "message": "显示名称须为 1–100 个字符"})
            continue
        folder = directory(root, "raw", name)
        if folder is None:
            errors.append({"folder": name, "message": "目录不存在或路径不安全"})
            continue
        info, error = dataset_info(folder)
        if error:
            errors.append({"folder": name, "message": error})
            continue
        try:
            record = repo.register(name, display_name, info["robot_type"].strip(), info_digest(folder))
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


def detail(root: Path, record: dict, area: str = "raw") -> dict:
    folder_name = record["raw_relpath"] if area == "raw" else record["id"]
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
                    videos.append({"name": file.name, "url": f"/data/{area}/{quote(record['id'])}/video/{quote(relative, safe='/')}"})
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
