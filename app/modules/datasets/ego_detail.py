"""Read-only LeRobot v3 EGO episode browsing and vector previews."""

from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import quote

import pyarrow as pa
import pyarrow.parquet as pq

from app.modules.datasets import service

PAGE_SIZE = 20
MAX_POINTS = 500
VECTOR_FIELDS = ("observation.state", "action")


def _folder(root: Path, record: dict) -> Path:
    folder = service.directory(root, record["storage_area"], record["storage_relpath"])
    if folder is None or record["dataset_family"] != "ego" or record.get("manifest_sha256"):
        raise ValueError("EGO LeRobot v3 数据集不存在或目录不可访问。")
    return folder


def overview(root: Path, record: dict, info: dict, camera_count: int) -> dict:
    folder = _folder(root, record)
    tasks_path = service.safe_file(folder, "meta/tasks.parquet")
    try:
        task_count = pq.ParquetFile(tasks_path).metadata.num_rows if tasks_path else None
    except (OSError, pa.ArrowException):
        task_count = None
    fps = info.get("fps")
    frames = info.get("total_frames")
    duration = round(frames / fps, 1) if isinstance(frames, int) and isinstance(fps, (int, float)) and fps > 0 else None
    return {"task_count": task_count, "duration_seconds": duration, "camera_count": camera_count}


def _metadata_rows(folder: Path):
    meta = folder / "meta" / "episodes"
    if not meta.is_dir() or meta.is_symlink():
        raise ValueError("Episode 元数据目录缺失。")
    for path in sorted(meta.rglob("*.parquet")):
        relative = path.relative_to(folder).as_posix()
        if service.safe_file(folder, relative) is None:
            continue
        try:
            schema = pq.read_schema(path)
            columns = [name for name in schema.names if name in {
                "episode_index", "length", "tasks", "data/chunk_index", "data/file_index"
            } or name.startswith("videos/") and name.endswith((
                "/chunk_index", "/file_index", "/from_timestamp", "/to_timestamp"
            ))]
            if "episode_index" not in columns:
                raise ValueError("Episode 元数据缺少 episode_index。")
            yield from pq.read_table(path, columns=columns).to_pylist()
        except (OSError, pa.ArrowException) as exc:
            raise ValueError("无法读取 Episode 元数据。") from exc


def _row_for(folder: Path, episode_index: int) -> dict:
    for row in _metadata_rows(folder):
        if row.get("episode_index") == episode_index:
            return row
    raise ValueError(f"Episode {episode_index} 不存在。")


def _video_for(folder: Path, record: dict, row: dict, camera_keys: list[str]) -> dict | None:
    for key in camera_keys:
        prefix = f"videos/{key}"
        chunk, file = row.get(f"{prefix}/chunk_index"), row.get(f"{prefix}/file_index")
        if not isinstance(chunk, int) or not isinstance(file, int) or chunk < 0 or file < 0:
            continue
        relative = f"{key}/chunk-{chunk:03d}/file-{file:03d}.mp4"
        if service.safe_file(folder / "videos", relative) is None:
            continue
        start = row.get(f"{prefix}/from_timestamp")
        end = row.get(f"{prefix}/to_timestamp")
        return {
            "url": f"/data/ego/{quote(record['id'])}/episodes/{row['episode_index']}/preview",
            "start": float(start) if isinstance(start, (int, float)) and math.isfinite(start) else 0.0,
            "end": float(end) if isinstance(end, (int, float)) and math.isfinite(end) else None,
            "camera": key,
            "path": relative,
        }
    return None


def episode_video(root: Path, record: dict, episode_index: int) -> tuple[Path, float, float]:
    folder = _folder(root, record)
    info, error = service.dataset_info(folder)
    if error:
        raise ValueError(error)
    camera_keys = [key for key, value in info.get("features", {}).items()
                   if isinstance(value, dict) and value.get("dtype") == "video"]
    row = _row_for(folder, episode_index)
    video = _video_for(folder, record, row, camera_keys)
    if video is None:
        raise ValueError("Episode 没有可预览的视频。")
    source = service.safe_file(folder / "videos", video["path"])
    if source is None:
        raise ValueError("Episode 视频不存在。")
    start = max(0.0, video["start"])
    duration = min(120.0, max(0.1, (video["end"] or start + 120) - start))
    return source, start, duration


def episodes(root: Path, record: dict, page: int) -> dict:
    if page < 1:
        raise ValueError("页码必须大于 0。")
    folder = _folder(root, record)
    info, error = service.dataset_info(folder)
    if error:
        raise ValueError(error)
    camera_keys = [key for key, value in info.get("features", {}).items()
                   if isinstance(value, dict) and value.get("dtype") == "video"]
    offset = (page - 1) * PAGE_SIZE
    result = []
    for number, row in enumerate(_metadata_rows(folder)):
        if number < offset:
            continue
        if len(result) >= PAGE_SIZE:
            break
        tasks = row.get("tasks") or []
        if isinstance(tasks, str):
            tasks = [tasks]
        length = row.get("length")
        fps = info.get("fps")
        result.append({
            "episode_index": row["episode_index"],
            "task": "；".join(str(task) for task in tasks if str(task).strip()) or "未提供任务描述",
            "frames": length if isinstance(length, int) else None,
            "duration_seconds": round(length / fps, 2) if isinstance(length, int) and isinstance(fps, (int, float)) and fps > 0 else None,
            "video": _video_for(folder, record, row, camera_keys),
        })
    return {"page": page, "page_size": PAGE_SIZE, "total": info.get("total_episodes", 0), "items": result}


def vector_series(root: Path, record: dict, episode_index: int, field_name: str) -> dict:
    if episode_index < 0 or field_name not in VECTOR_FIELDS:
        raise ValueError("Episode 或数值字段无效。")
    folder = _folder(root, record)
    info, error = service.dataset_info(folder)
    if error:
        raise ValueError(error)
    row = _row_for(folder, episode_index)
    chunk, file = row.get("data/chunk_index"), row.get("data/file_index")
    if not isinstance(chunk, int) or not isinstance(file, int) or chunk < 0 or file < 0:
        raise ValueError("Episode 缺少帧数据文件索引。")
    path = service.safe_file(folder, f"data/chunk-{chunk:03d}/file-{file:03d}.parquet")
    if path is None:
        raise ValueError("Episode 帧数据文件缺失。")
    try:
        schema = pq.read_schema(path)
        if not {"episode_index", field_name}.issubset(schema.names):
            raise ValueError("Episode 帧数据缺少请求的字段。")
        columns = ["episode_index", field_name]
        for extra in ("frame_index", "timestamp"):
            if extra in schema.names:
                columns.append(extra)
        compare = "action" if field_name == "observation.state" else "observation.state"
        if compare in schema.names:
            columns.append(compare)
        table = pq.read_table(path, columns=columns,
                              filters=[("episode_index", "=", episode_index)])
    except (OSError, pa.ArrowException) as exc:
        raise ValueError("无法读取 Episode 帧数据。") from exc
    rows = table.to_pylist()
    if not rows:
        raise ValueError("Episode 没有帧数据。")
    rows.sort(key=lambda value: value.get("frame_index", 0))
    feature = info.get("features", {}).get(field_name, {})
    shape = feature.get("shape") if isinstance(feature, dict) else None
    width = shape[0] if isinstance(shape, list) and len(shape) == 1 and isinstance(shape[0], int) else None
    if width is None or width < 1 or width > 256:
        raise ValueError("该字段没有有效的一维向量定义。")
    fps = info.get("fps")
    if not isinstance(fps, (int, float)) or fps <= 0:
        raise ValueError("数据集 FPS 无效。")
    first_frame = rows[0].get("frame_index") or 0
    first_time = rows[0].get("timestamp")
    equal = compare in table.column_names
    points = []
    step = max(1, math.ceil(len(rows) / MAX_POINTS))
    selected = set(range(0, len(rows), step)) | {len(rows) - 1}
    for index, frame in enumerate(rows):
        values = frame[field_name]
        other = frame.get(compare)
        if not isinstance(values, list) or len(values) != width:
            raise ValueError("Episode 中存在维度不一致的向量。")
        if equal and values != other:
            equal = False
        if index in selected:
            timestamp = frame.get("timestamp")
            seconds = (timestamp - first_time) if isinstance(timestamp, (int, float)) and isinstance(first_time, (int, float)) else ((frame.get("frame_index") or 0) - first_frame) / fps
            points.append({"seconds": round(seconds, 4), "values": [service._finite_number(value) for value in values]})
    return {"episode_index": episode_index, "field_name": field_name, "width": width,
            "total_points": len(rows), "sampled": len(points) < len(rows),
            "state_action_equal": equal if compare in table.column_names else None,
            "points": points}
