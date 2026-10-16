import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates


router = APIRouter()
templates = Jinja2Templates(directory="templates")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.getenv("HKC_DATA_ROOT", PROJECT_ROOT / "datas")).expanduser().resolve()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 的顶层内容必须是 JSON 对象")
    return data


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _format_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}小时 {minutes}分 {seconds}秒"
    if minutes:
        return f"{minutes}分 {seconds}秒"
    return f"{seconds}秒"


def _files(root: Path, suffix: str) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob(f"*{suffix}") if path.is_file())


def _dataset_directory(dataset_name: str) -> Path:
    candidate = (DATA_ROOT / dataset_name).resolve()
    if candidate.parent != DATA_ROOT or not candidate.is_dir():
        raise HTTPException(status_code=404, detail="数据集不存在")
    return candidate


def _load_dataset(dataset_dir: Path, include_details: bool = False) -> dict[str, Any]:
    info_path = dataset_dir / "meta" / "info.json"
    stats_path = dataset_dir / "meta" / "stats.json"

    try:
        info = _read_json(info_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "name": dataset_dir.name,
            "path_name": dataset_dir.name,
            "load_error": f"无法读取 meta/info.json：{exc}",
            "status": "元数据异常",
            "status_class": "status-error",
            "total_size": "-",
            "total_size_bytes": 0,
            "episodes": 0,
            "frames": 0,
            "fps": 0,
            "duration": "-",
            "duration_seconds": 0,
            "robot_type": "-",
            "camera_count": 0,
            "video_file_count": 0,
            "data_file_count": 0,
        }

    all_files = [path for path in dataset_dir.rglob("*") if path.is_file()]
    parquet_files = [path for path in all_files if path.suffix.lower() == ".parquet"]
    video_files = [path for path in all_files if path.suffix.lower() == ".mp4"]
    total_size_bytes = sum(path.stat().st_size for path in all_files)

    features = info.get("features", {})
    if not isinstance(features, dict):
        features = {}

    video_features = {
        key: value
        for key, value in features.items()
        if isinstance(value, dict) and value.get("dtype") == "video"
    }
    episodes = int(info.get("total_episodes") or 0)
    frames = int(info.get("total_frames") or 0)
    fps = float(info.get("fps") or 0)
    duration_seconds = frames / fps if fps else 0

    dataset: dict[str, Any] = {
        "name": dataset_dir.name.replace("_", " "),
        "path_name": dataset_dir.name,
        "load_error": None,
        "status": "结构可读取" if parquet_files and video_files else "需要检查",
        "status_class": "status-done" if parquet_files and video_files else "status-paused",
        "robot_type": info.get("robot_type", "未知"),
        "version": info.get("codebase_version", "未知"),
        "episodes": episodes,
        "frames": frames,
        "tasks": int(info.get("total_tasks") or 0),
        "fps": fps,
        "duration_seconds": duration_seconds,
        "duration": _format_duration(duration_seconds),
        "camera_count": len(video_features),
        "data_file_count": len(parquet_files),
        "video_file_count": len(video_files),
        "total_size_bytes": total_size_bytes,
        "total_size": _human_size(total_size_bytes),
    }

    if not include_details:
        return dataset

    try:
        stats = _read_json(stats_path)
    except (OSError, json.JSONDecodeError, ValueError):
        stats = {}

    cameras = []
    for key, feature in video_features.items():
        camera_root = dataset_dir / "videos" / key
        camera_files = _files(camera_root, ".mp4")
        video_info = feature.get("video_info", {})
        shape = feature.get("shape", [])
        resolution = " × ".join(str(value) for value in shape[:2]) if len(shape) >= 2 else "未知"
        cameras.append(
            {
                "key": key,
                "label": key.removeprefix("observation.images.").replace("_", " "),
                "resolution": resolution,
                "codec": video_info.get("video.codec", "未知"),
                "pixel_format": video_info.get("video.pix_fmt", "未知"),
                "fps": video_info.get("video.fps", fps),
                "files": [
                    {
                        "name": path.name,
                        "relative_path": str(path.relative_to(dataset_dir)),
                        "camera_path": str(path.relative_to(camera_root)),
                        "size": _human_size(path.stat().st_size),
                    }
                    for path in camera_files
                ],
            }
        )

    numeric_features = []
    for key, feature in features.items():
        if not isinstance(feature, dict) or feature.get("dtype") == "video":
            continue
        feature_stats = stats.get(key, {}) if isinstance(stats.get(key), dict) else {}
        shape = feature.get("shape", [])
        numeric_features.append(
            {
                "name": key,
                "dtype": feature.get("dtype", "未知"),
                "shape": " × ".join(str(value) for value in shape) if shape else "标量",
                "count": feature_stats.get("count", "-"),
                "min": feature_stats.get("min"),
                "max": feature_stats.get("max"),
                "mean": feature_stats.get("mean"),
                "std": feature_stats.get("std"),
            }
        )

    dataset.update(
        {
            "cameras": cameras,
            "numeric_features": numeric_features,
            "parquet_files": [
                {
                    "path": str(path.relative_to(dataset_dir)),
                    "size": _human_size(path.stat().st_size),
                }
                for path in parquet_files
            ],
            "metadata_files": [
                {
                    "path": str(path.relative_to(dataset_dir)),
                    "size": _human_size(path.stat().st_size),
                }
                for path in sorted((dataset_dir / "meta").rglob("*"))
                if path.is_file()
            ],
        }
    )
    return dataset


def _scan_datasets() -> list[dict[str, Any]]:
    if not DATA_ROOT.is_dir():
        return []
    return [
        _load_dataset(path)
        for path in sorted(DATA_ROOT.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]


@router.get("/", name="data_page")
async def data_page(request: Request):
    datasets = _scan_datasets()
    summary = {
        "datasets": len(datasets),
        "episodes": sum(item["episodes"] for item in datasets),
        "frames": sum(item["frames"] for item in datasets),
        "duration": _format_duration(sum(item.get("duration_seconds", 0) for item in datasets)),
        "size": _human_size(sum(item["total_size_bytes"] for item in datasets)),
    }
    return templates.TemplateResponse(
        request=request,
        name="data.html",
        context={"datasets": datasets, "summary": summary, "data_root_exists": DATA_ROOT.is_dir()},
    )


@router.get("/{dataset_name}", name="dataset_detail")
async def dataset_detail(request: Request, dataset_name: str):
    dataset_dir = _dataset_directory(dataset_name)
    dataset = _load_dataset(dataset_dir, include_details=True)
    if dataset.get("load_error"):
        raise HTTPException(status_code=422, detail=dataset["load_error"])
    return templates.TemplateResponse(
        request=request,
        name="data_detail.html",
        context={"dataset": dataset},
    )


@router.get(
    "/{dataset_name}/video/{camera_key}/{video_path:path}",
    name="dataset_video",
)
async def dataset_video(dataset_name: str, camera_key: str, video_path: str):
    dataset_dir = _dataset_directory(dataset_name)
    video_root = (dataset_dir / "videos" / camera_key).resolve()
    if video_root.parent != (dataset_dir / "videos").resolve() or not video_root.is_dir():
        raise HTTPException(status_code=404, detail="相机不存在")

    requested_video = (video_root / video_path).resolve()
    if (
        video_root not in requested_video.parents
        or not requested_video.is_file()
        or requested_video.suffix.lower() != ".mp4"
    ):
        raise HTTPException(status_code=404, detail="视频不存在")
    return FileResponse(requested_video, media_type="video/mp4")
