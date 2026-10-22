"""Convert a local LeRobotDataset v3 Parquet/MP4 directory to episode-based HDF5."""

from __future__ import annotations

import json
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import av
import h5py
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

EPISODES_PER_FILE = 10
DATA_PATH = "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
VIDEO_PATH = "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"


def safe_input(root: Path, relative: str) -> Path:
    """Resolve a source file without accepting absolute paths or symlink escapes."""
    part = Path(relative)
    if part.is_absolute() or not part.parts or any(p in {".", ".."} for p in part.parts):
        raise ValueError(f"无效的来源文件路径：{relative}")
    current = root
    for name in part.parts:
        current = current / name
        if current.is_symlink():
            raise ValueError(f"来源数据包含符号链接：{relative}")
    path = current.resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"来源文件不存在：{relative}")
    return path


def source_info(root: Path) -> dict:
    info = json.loads(safe_input(root, "meta/info.json").read_text(encoding="utf-8"))
    if not isinstance(info, dict) or info.get("codebase_version") != "v3.0":
        raise ValueError("仅支持 LeRobotDataset v3.0")
    if info.get("storage_format") not in (None, "lerobot"):
        raise ValueError("仅支持 Parquet/MP4 存储的 LeRobot v3.0")
    if not isinstance(info.get("features"), dict) or not info["features"]:
        raise ValueError("meta/info.json 缺少 features")
    if not isinstance(info.get("total_episodes"), int) or info["total_episodes"] < 1:
        raise ValueError("meta/info.json 中没有 episode")
    if not isinstance(info.get("total_frames"), int) or info["total_frames"] < 1:
        raise ValueError("meta/info.json 中没有有效的帧数")
    return info


def episodes(root: Path, info: dict) -> list[dict]:
    base = root / "meta" / "episodes"
    if not base.is_dir() or base.is_symlink():
        raise ValueError("缺少 meta/episodes 目录")
    rows: list[dict] = []
    for path in sorted(base.rglob("*.parquet")):
        relative = path.relative_to(root).as_posix()
        try:
            rows.extend(pq.read_table(safe_input(root, relative)).to_pylist())
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法读取 episode 元数据：{relative}") from exc
    rows.sort(key=lambda row: row.get("episode_index", -1))
    if len(rows) != info["total_episodes"]:
        raise ValueError("episode 元数据数量与 meta/info.json 不一致")
    if len({row.get("episode_index") for row in rows}) != len(rows):
        raise ValueError("episode_index 重复")
    required = {"episode_index", "dataset_from_index", "dataset_to_index"}
    for row in rows:
        if not required.issubset(row):
            raise ValueError("episode 元数据缺少定位字段")
        length = _episode_length(row)
        if not isinstance(length, int) or length < 1:
            raise ValueError("episode 长度无效")
        if row["dataset_to_index"] - row["dataset_from_index"] not in (length, length - 1):
            raise ValueError("episode 行范围与长度不一致")
        if ("data/chunk_index" in row) != ("data/file_index" in row):
            raise ValueError("episode 数据文件定位字段不完整")
    if sum(_episode_length(row) for row in rows) != info.get("total_frames"):
        raise ValueError("episode 总帧数与 meta/info.json 不一致")
    return rows


def _episode_length(row: dict) -> int:
    return row.get("length", row.get("num_frames"))


def _data_file_ranges(root: Path, total_frames: int) -> list[tuple[str, int, int]]:
    """Map global frame indices to ordered Parquet files when metadata omits file indices."""
    paths = sorted((root / "data").rglob("*.parquet"))
    ranges = []
    offset = 0
    for path in paths:
        relative = path.relative_to(root).as_posix()
        count = pq.ParquetFile(safe_input(root, relative)).metadata.num_rows
        ranges.append((relative, offset, offset + count))
        offset += count
    if not ranges or offset != total_frames:
        raise ValueError("Parquet 文件行数与 meta/info.json 不一致")
    return ranges


def _format_path(template: str, **values: object) -> str:
    try:
        path = template.format(**values)
    except (KeyError, ValueError, AttributeError) as exc:
        raise ValueError("LeRobot 文件路径模板无效") from exc
    if "{" in path or "}" in path:
        raise ValueError("LeRobot 文件路径模板包含未知占位符")
    return path


def _task_names(root: Path) -> dict[int, str]:
    path = root / "meta" / "tasks.parquet"
    if not path.exists():
        return {}
    table = pd.read_parquet(safe_input(root, "meta/tasks.parquet"))
    if "task_index" not in table.columns:
        return {}
    if "task" in table.columns:
        names = table["task"].tolist()
    elif table.index.name == "task":
        names = table.index.tolist()
    else:
        return {}
    return dict(zip((int(value) for value in table["task_index"]),
                    (str(value) for value in names), strict=True))


def _jpeg(image: Image.Image) -> np.ndarray:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    return np.frombuffer(buffer.getvalue(), dtype=np.uint8)


def _write_embedded_images(group: h5py.Group, key: str, values: list, root: Path) -> None:
    output = group.create_dataset(key, shape=(len(values),), dtype=h5py.vlen_dtype(np.uint8))
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise TypeError(f"图像字段 {key} 的值无效")
        payload = value.get("bytes")
        if payload is None and value.get("path"):
            payload = safe_input(root, str(value["path"])).read_bytes()
        if not payload:
            raise ValueError(f"图像字段 {key} 的第 {index} 帧为空")
        with Image.open(BytesIO(payload)) as image:
            output[index] = _jpeg(image)


def _frame_time(frame: av.VideoFrame, stream: av.video.stream.VideoStream) -> float:
    if frame.time is not None:
        return float(frame.time)
    if frame.pts is None:
        raise ValueError("视频帧缺少时间戳")
    return float(frame.pts * stream.time_base)


def _write_video_images(group: h5py.Group, key: str, path: Path,
                        timestamps: list[float], fps: float) -> None:
    output = group.create_dataset(key, shape=(len(timestamps),), dtype=h5py.vlen_dtype(np.uint8))
    tolerance = max(0.55 / fps, 0.01)
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError(f"视频文件没有视频流：{path.name}")
        stream = container.streams.video[0]
        seek_to = max(0.0, timestamps[0] - 2 / fps)
        container.seek(int(seek_to / float(stream.time_base)), stream=stream, backward=True)
        frames = iter(container.decode(stream))
        previous: tuple[float, av.VideoFrame] | None = None
        current: tuple[float, av.VideoFrame] | None = None

        def advance() -> tuple[float, av.VideoFrame] | None:
            frame = next(frames, None)
            return (_frame_time(frame, stream), frame) if frame is not None else None

        current = advance()
        for index, target in enumerate(timestamps):
            while current is not None and current[0] < target:
                previous, current = current, advance()
            candidates = [item for item in (previous, current) if item is not None]
            if not candidates:
                raise ValueError(f"视频 {path.name} 缺少第 {index} 帧")
            closest = min(candidates, key=lambda item: abs(item[0] - target))
            if abs(closest[0] - target) > tolerance:
                raise ValueError(f"视频 {path.name} 第 {index} 帧与时间戳不匹配")
            output[index] = _jpeg(Image.fromarray(closest[1].to_ndarray(format="rgb24")))


def _write_values(group: h5py.Group, key: str, values: list, feature: dict | None) -> None:
    if any(value is None for value in values):
        raise ValueError(f"字段 {key} 含空值")
    if all(isinstance(value, str) for value in values):
        group.create_dataset(key, data=np.asarray(values, dtype=object),
                             dtype=h5py.string_dtype("utf-8"))
        return
    dtype_name = (feature or {}).get("dtype")
    try:
        dtype = np.dtype(dtype_name) if dtype_name else None
    except TypeError as exc:
        raise ValueError(f"字段 {key} 的 dtype 无效：{dtype_name}") from exc
    data = np.asarray(values, dtype=dtype)
    if key in {"action", "observation.state"} and data.ndim == 1:
        data = data.reshape(len(values), 1)
    if data.dtype.kind not in "biuf":
        raise ValueError(f"字段 {key} 不是定长数值数组，无法写入 HDF5")
    if data.ndim < 1 or data.shape[0] != len(values):
        raise ValueError(f"字段 {key} 的帧形状不一致")
    expected = (feature or {}).get("shape")
    if expected and key in {"action", "observation.state"} and tuple(data.shape[1:]) != tuple(expected):
        raise ValueError(f"字段 {key} 的形状与 meta/info.json 不一致")
    compression = {"compression": "gzip", "compression_opts": 4, "shuffle": True} if data.size > 1024 else {}
    group.create_dataset(key, data=data, **compression)


def convert(source: Path, destination: Path, progress: Callable[[int], None]) -> dict:
    """Write complete HDF5 chunks into a private staging directory."""
    info = source_info(source)
    episode_rows = episodes(source, info)
    tasks = _task_names(source)
    features = info["features"]
    video_keys = [key for key, feature in features.items() if feature.get("dtype") == "video"]
    image_keys = [key for key, feature in features.items() if feature.get("dtype") == "image"]
    data_template = info.get("data_path") or DATA_PATH
    video_template = info.get("video_path") or VIDEO_PATH
    fps = float(info.get("fps") or 0)
    if fps <= 0:
        raise ValueError("meta/info.json 中的 fps 无效")
    if not {"action", "observation.state"}.issubset(features):
        raise ValueError("来源缺少 action 或 observation.state")
    if not any(key.startswith("observation.images.") for key in video_keys + image_keys):
        raise ValueError("来源缺少 observation.images.* 相机字段")
    for key in features:
        if not isinstance(key, str) or not key or "/" in key:
            raise ValueError(f"不支持的字段名：{key}")

    destination.mkdir(parents=True, exist_ok=False)
    file_bases: dict[str, int] = {}
    needs_global_ranges = any("data/chunk_index" not in row for row in episode_rows)
    data_ranges = _data_file_ranges(source, info["total_frames"]) if needs_global_ranges else []
    for row in episode_rows:
        if "data/chunk_index" not in row:
            continue
        relative = _format_path(data_template, chunk_index=row["data/chunk_index"],
                                file_index=row["data/file_index"])
        file_bases[relative] = min(file_bases.get(relative, row["dataset_from_index"]),
                                   row["dataset_from_index"])

    current_file: h5py.File | None = None
    current_table = None
    current_data_path = None
    try:
        for position, row in enumerate(episode_rows):
            if position % EPISODES_PER_FILE == 0:
                if current_file is not None:
                    current_file.close()
                current_file = h5py.File(destination / f"chunk_{position // EPISODES_PER_FILE + 1:03d}.hdf5", "w")
                current_file.create_group("data")
                meta = current_file.create_group("meta")
                meta.attrs["source_format"] = "LeRobotDataset v3.0"
                meta.attrs["source_dataset"] = source.name
                meta.attrs["fps"] = fps
                meta.create_dataset("source_info_json", data=json.dumps(info, ensure_ascii=False),
                                    dtype=h5py.string_dtype("utf-8"))
                source_meta = meta.create_group("source_meta_files")
                for filename in ("stats.json", "tasks.parquet", "subtasks.parquet"):
                    file = source / "meta" / filename
                    if file.exists():
                        contents = safe_input(source, f"meta/{filename}").read_bytes()
                        source_meta.create_dataset(filename, data=np.frombuffer(contents, dtype=np.uint8))
                annotations = source / "meta" / "annotations"
                if annotations.exists():
                    if annotations.is_symlink() or not annotations.is_dir():
                        raise ValueError("meta/annotations 目录不安全")
                    for file in sorted(annotations.rglob("*")):
                        relative = file.relative_to(annotations)
                        if file.is_symlink():
                            raise ValueError("标注元数据包含符号链接")
                        if not file.is_file():
                            continue
                        target = source_meta.require_group("annotations")
                        for part in relative.parts[:-1]:
                            target = target.require_group(part)
                        contents = safe_input(source, file.relative_to(source).as_posix()).read_bytes()
                        target.create_dataset(relative.name, data=np.frombuffer(contents, dtype=np.uint8))
                meta.create_group("episodes")

            length = _episode_length(row)
            if "data/chunk_index" in row:
                relative = _format_path(data_template, chunk_index=row["data/chunk_index"],
                                        file_index=row["data/file_index"])
                if relative != current_data_path:
                    current_table = pq.read_table(safe_input(source, relative))
                    current_data_path = relative
                local_start = row["dataset_from_index"] - file_bases[relative]
                if local_start < 0 or local_start + length > current_table.num_rows:
                    raise ValueError("episode 的 Parquet 行范围越界")
                frame_table = current_table.slice(local_start, length)
            else:
                start = row["dataset_from_index"]
                end = start + length
                pieces = []
                for relative, file_start, file_end in data_ranges:
                    overlap_start, overlap_end = max(start, file_start), min(end, file_end)
                    if overlap_start >= overlap_end:
                        continue
                    if relative != current_data_path:
                        current_table = pq.read_table(safe_input(source, relative))
                        current_data_path = relative
                    pieces.append(current_table.slice(overlap_start - file_start,
                                                     overlap_end - overlap_start))
                if not pieces or sum(piece.num_rows for piece in pieces) != length:
                    raise ValueError("episode 的 Parquet 行范围越界")
                frame_table = pa.concat_tables(pieces) if len(pieces) > 1 else pieces[0]
            if not {"action", "observation.state"}.issubset(frame_table.column_names):
                raise ValueError("Parquet 文件缺少 action 或 observation.state")
            if video_keys and "timestamp" not in frame_table.column_names:
                raise ValueError("Parquet 文件缺少视频对齐所需的 timestamp")
            if "episode_index" in frame_table.column_names:
                indices = frame_table["episode_index"].to_pylist()
                if any(index != row["episode_index"] for index in indices):
                    raise ValueError("Parquet 行与 episode_index 不匹配")
            name = f"episode_{position + 1:03d}"
            group = current_file["data"].create_group(name)
            group.attrs["source_episode_index"] = row["episode_index"]
            task_names = row.get("tasks") or ([row["task"]] if row.get("task") else [])
            if not task_names and "task_index" in frame_table.column_names:
                task_names = list(dict.fromkeys(tasks.get(index, "") for index in
                                                frame_table["task_index"].to_pylist()))
                task_names = [task for task in task_names if task]
            group.attrs["task"] = str(task_names[0]) if task_names else ""
            group.attrs["tasks_json"] = json.dumps(task_names, ensure_ascii=False)
            group.attrs["task_zh"] = str(row.get("task_zh") or "")
            group.attrs["score"] = float(row.get("score") if row.get("score") is not None else -1)
            current_file["meta/episodes"].create_dataset(
                name, data=json.dumps(row, ensure_ascii=False, default=str),
                dtype=h5py.string_dtype("utf-8"),
            )
            for key in frame_table.column_names:
                values = frame_table[key].to_pylist()
                if key in image_keys:
                    _write_embedded_images(group, key, values, source)
                else:
                    _write_values(group, key, values, features.get(key))
            for key in video_keys:
                if key not in group:
                    video_relative = _format_path(
                        video_template, video_key=key,
                        chunk_index=row[f"videos/{key}/chunk_index"],
                        file_index=row[f"videos/{key}/file_index"],
                    )
                    offset = float(row[f"videos/{key}/from_timestamp"])
                    frame_times = frame_table["timestamp"].to_pylist()
                    if len(frame_times) != length:
                        raise ValueError("视频时间戳数量与 episode 长度不一致")
                    _write_video_images(group, key, safe_input(source, video_relative),
                                        [offset + float(value) for value in frame_times], fps)
            progress(position + 1)
    finally:
        if current_file is not None:
            current_file.close()

    manifest = {
        "format": "HDF5", "source_format": "LeRobotDataset v3.0",
        "total_episodes": len(episode_rows), "total_frames": info["total_frames"],
        "fps": fps, "source_dataset": source.name,
        "files": sorted(path.name for path in destination.glob("chunk_*.hdf5")),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
