"""Dataset storage helpers and the shared DS identifier resolution layer."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from app.db.dataset_robots import DatasetRobotRepository
from app.db.datasets import STORAGE_AREAS, DatasetRepository

MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_LIST_FILES = 500
UNKNOWN_ROBOT_TYPE = "未声明"


def robot_type_label(info: dict) -> str:
    """Return a stable display/registry value without mutating dataset metadata."""

    value = info.get("robot_type")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return UNKNOWN_ROBOT_TYPE


def directory(root: Path, area: str, name: str) -> Path | None:
    """Resolve one direct child without following links outside its storage area."""
    if area not in STORAGE_AREAS and area not in {"converted", "qc"}:
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
    repo = DatasetRepository(root)
    record = repo.register(
        storage_area,
        storage_relpath,
        name,
        robot_type_label(info),
        info_digest(folder),
        source_dataset_ids,
    )
    try:
        DatasetRobotRepository(root).inherit(record["id"], source_dataset_ids or [])
    except Exception:
        # Keep dataset registration and relationship publication as one logical
        # operation for callers that will roll back their file transaction.
        repo.unregister(record["id"])
        raise
    return record


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
    collector = DatasetRobotRepository(root).get(registered["id"]) if registered else None
    return {
        "id": registered["id"] if registered else None,
        "name": registered["name"] if registered else name,
        "folder": name,
        "storage_area": area,
        "robot_type": registered["robot_type"] if registered else (
            robot_type_label(info) if info else "—"
        ),
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
        "collector_robot_id": collector["robot_id"] if collector else None,
        "collector_robot_name": collector["robot_name"] if collector else None,
        "collector_robot_status": collector["robot_status"] if collector else None,
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


def _report_storage_folder(
    root: Path,
    dataset_id: str,
    *,
    trashed: bool,
    create: bool = False,
) -> Path | None:
    """Resolve one report folder without following storage symlinks."""

    if (
        not dataset_id
        or Path(dataset_id).name != dataset_id
        or dataset_id.startswith(".")
    ):
        return None
    parts = ("trash", "reports") if trashed else ("reports", "qc")
    current = root
    try:
        for part in parts:
            current = current / part
            if current.is_symlink():
                return None
            if create:
                current.mkdir(exist_ok=True)
            if not current.is_dir():
                return None
        folder = current / dataset_id
        if folder.is_symlink():
            return None
        if create:
            folder.mkdir(exist_ok=True)
        if not folder.is_dir() or folder.resolve().parent != current.resolve():
            return None
        return folder
    except OSError:
        return None


def _safe_report_name(filename: str) -> bool:
    return bool(
        filename
        and Path(filename).name == filename
        and not filename.startswith(".")
        and not filename.endswith(".trash-meta.json")
    )


def trashed_report_file(root: Path, dataset_id: str, filename: str) -> Path | None:
    folder = _report_storage_folder(root, dataset_id, trashed=True)
    if folder is None or not _safe_report_name(filename):
        return None
    return safe_file(folder, filename)


def _trash_metadata_file(folder: Path, filename: str) -> Path:
    return folder / f".{filename}.trash-meta.json"


def trashed_reports_for(root: Path, dataset_id: str) -> list[dict]:
    folder = _report_storage_folder(root, dataset_id, trashed=True)
    if folder is None:
        return []
    rows = []
    for file in sorted(folder.iterdir(), key=lambda path: path.name):
        if file.is_symlink() or not file.is_file() or not _safe_report_name(file.name):
            continue
        metadata = read_json(safe_file(folder, _trash_metadata_file(folder, file.name).name)) or {}
        data = read_json(file) if file.suffix.lower() == ".json" else None
        rows.append({
            "name": file.name,
            "size": size_label(file.stat().st_size),
            "trashed_at": metadata.get("trashed_at", "—"),
            "original_path": metadata.get(
                "original_path", f"reports/qc/{dataset_id}/{file.name}"
            ),
            "decision": (data or {}).get("decision", "—"),
            "status": (data or {}).get("status", "—"),
        })
    rows.sort(key=lambda row: row["trashed_at"], reverse=True)
    return rows


def move_report_to_trash(root: Path, dataset_id: str, filename: str) -> dict:
    source_folder = _report_storage_folder(root, dataset_id, trashed=False)
    source = safe_file(source_folder, filename) if source_folder and _safe_report_name(filename) else None
    if source is None:
        raise ValueError("报告文件不存在")
    destination_folder = _report_storage_folder(root, dataset_id, trashed=True, create=True)
    if destination_folder is None:
        raise OSError("无法创建报告回收站目录")
    destination = destination_folder / filename
    metadata_file = _trash_metadata_file(destination_folder, filename)
    if destination.exists() or metadata_file.exists():
        raise FileExistsError("回收站中已有同名报告，请先恢复")

    metadata = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "filename": filename,
        "original_path": f"reports/qc/{dataset_id}/{filename}",
        "trash_path": f"trash/reports/{dataset_id}/{filename}",
        "trashed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    source.replace(destination)
    temporary = metadata_file.with_suffix(metadata_file.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(metadata_file)
    except OSError:
        temporary.unlink(missing_ok=True)
        if destination.exists() and not source.exists():
            destination.replace(source)
        raise
    return metadata


def restore_trashed_report(root: Path, dataset_id: str, filename: str) -> dict:
    trash_folder = _report_storage_folder(root, dataset_id, trashed=True)
    source = trashed_report_file(root, dataset_id, filename)
    if trash_folder is None or source is None:
        raise ValueError("回收站报告不存在")
    metadata_file = _trash_metadata_file(trash_folder, filename)
    metadata = read_json(safe_file(trash_folder, metadata_file.name))
    expected = f"reports/qc/{dataset_id}/{filename}"
    if metadata is None or metadata.get("original_path") != expected:
        raise ValueError("回收站元数据缺失或无效")
    destination_folder = _report_storage_folder(root, dataset_id, trashed=False, create=True)
    if destination_folder is None:
        raise OSError("无法创建报告目录")
    destination = destination_folder / filename
    if destination.exists():
        raise FileExistsError("报告目录中已有同名文件，不能覆盖")
    source.replace(destination)
    try:
        metadata_file.unlink()
    except OSError:
        if destination.exists() and not source.exists():
            destination.replace(source)
        raise
    return {
        "dataset_id": dataset_id,
        "filename": filename,
        "original_path": expected,
        "restored": True,
    }
