"""LeRobot v3 数据处理与不可变派生数据集生成。

第一版在单应用容器内执行任务，但任务状态和结果写入 ``storage/system``，以后可
把执行函数迁移到独立 Worker。所有源数据集只读；输出先写入 staging，校验通过
后再移动到 derived。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path, PurePosixPath
from string import Formatter
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pyarrow import ipc

from app.db.datasets import DatasetRepository
from app.modules.datasets import service as datasets_service
from app.modules.processing.schemas import ProcessingJobRequest

DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SPLIT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
DATASET_KINDS = ("raw", "derived")
DEFAULT_DATA_PATH = "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
DEFAULT_VIDEO_PATH = "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"


class ProcessingError(Exception):
    """可以安全展示给使用者的数据处理错误。"""


class DatasetNotFoundError(ProcessingError):
    """请求的数据集不存在。"""


@dataclass(frozen=True, slots=True)
class DatasetRef:
    dataset_id: str
    path: Path
    kind: str


@dataclass(slots=True)
class DatasetState:
    ref: DatasetRef
    info: dict[str, Any]
    task_names: dict[int, str]
    task_indexes: dict[str, int]
    episode_rows: list[dict[str, Any]]
    episode_schema: pa.Schema
    episode_by_index: dict[int, dict[str, Any]]
    data_schema: pa.Schema
    episode_annotations: dict[int, dict[str, Any]]
    subtask_annotations: dict[int, list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class EpisodeSelection:
    state: DatasetState
    episode_index: int


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProcessingError(f"无法读取 {path.name}。") from exc
    if not isinstance(value, dict):
        raise ProcessingError(f"{path.name} 的内容不是 JSON 对象。")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_dataset_id(dataset_id: str) -> None:
    if not DATASET_ID_PATTERN.fullmatch(dataset_id):
        raise DatasetNotFoundError("数据集编号不合法。")


def resolve_dataset(data_root: Path, dataset_id: str) -> DatasetRef:
    """通过统一 DS 编号解析真实存储目录。"""

    _validate_dataset_id(dataset_id)
    try:
        record, candidate = datasets_service.resolve_registered_dataset(data_root, dataset_id)
    except ValueError as exc:
        raise DatasetNotFoundError(f"未找到数据集 {dataset_id}。") from exc
    return DatasetRef(dataset_id=dataset_id, path=candidate, kind=record["storage_area"])


def _parquet_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.parquet") if path.is_file())


def _record_names(data_root: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for directory in (
        data_root / "system" / "upload-records",
        data_root / "system" / "dataset-versions",
    ):
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict):
                continue
            dataset_id = record.get("dataset_id")
            name = record.get("dataset_name") or record.get("version_name")
            if isinstance(dataset_id, str) and isinstance(name, str) and name.strip():
                names[dataset_id] = name.strip()
    return names


def _load_tasks(path: Path) -> tuple[dict[int, str], dict[str, int]]:
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError, pa.ArrowException) as exc:
        raise ProcessingError("无法读取 meta/tasks.parquet。") from exc
    if "task_index" not in frame.columns:
        raise ProcessingError("tasks.parquet 缺少 task_index 列。")
    if isinstance(frame.index, pd.RangeIndex):
        if "task" not in frame.columns:
            raise ProcessingError("tasks.parquet 缺少 task 索引或兼容 task 列。")
        frame = frame.set_index("task")
    if frame.index.hasnans or not frame.index.is_unique:
        raise ProcessingError("tasks.parquet 的 task 索引为空或重复。")
    if frame["task_index"].isna().any() or not frame["task_index"].is_unique:
        raise ProcessingError("tasks.parquet 的 task_index 为空或重复。")

    by_index: dict[int, str] = {}
    by_text: dict[str, int] = {}
    for task_value, index_value in zip(frame.index, frame["task_index"], strict=True):
        text = str(task_value).strip()
        index = int(index_value)
        if not text:
            raise ProcessingError("tasks.parquet 包含空 task。")
        by_index[index] = text
        by_text[text] = index
    return by_index, by_text


def _load_annotations(
    dataset_path: Path,
) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    annotation_root = dataset_path / "meta" / "annotations"
    episodes: dict[int, dict[str, Any]] = {}
    subtasks: dict[int, list[dict[str, Any]]] = {}
    episode_path = annotation_root / "episodes.parquet"
    subtask_path = annotation_root / "subtasks.parquet"
    try:
        if episode_path.is_file():
            for row in pq.read_table(episode_path).to_pylist():
                episodes[int(row["episode_index"])] = row
        if subtask_path.is_file():
            for row in pq.read_table(subtask_path).to_pylist():
                subtasks.setdefault(int(row["episode_index"]), []).append(row)
    except (OSError, KeyError, TypeError, ValueError, pa.ArrowException) as exc:
        raise ProcessingError("无法读取平台扩展标注。") from exc
    return episodes, subtasks


def _episode_schema_with_compatibility_fields(schema: pa.Schema) -> pa.Schema:
    """为早期 v3 元数据补上当前处理流程需要的标准字段。"""

    fields = list(schema)
    existing = set(schema.names)
    compatibility_fields = (
        pa.field("data/chunk_index", pa.int64()),
        pa.field("data/file_index", pa.int64()),
        pa.field("tasks", pa.list_(pa.string())),
        pa.field("length", pa.int64()),
        pa.field("meta/episodes/chunk_index", pa.int64()),
        pa.field("meta/episodes/file_index", pa.int64()),
    )
    fields.extend(field for field in compatibility_fields if field.name not in existing)
    return pa.schema(fields, metadata=schema.metadata)


def _data_path_pattern(template: str) -> re.Pattern[str]:
    """把 info.json 的 data_path 模板转换为可读取分片编号的正则。"""

    parts = ["^"]
    fields: set[str] = set()
    try:
        parsed = Formatter().parse(template)
        for literal, field_name, _format_spec, _conversion in parsed:
            parts.append(re.escape(literal))
            if field_name is None:
                continue
            if field_name not in {"chunk_index", "file_index"}:
                raise ProcessingError("data_path 包含不支持的模板字段。")
            if field_name in fields:
                raise ProcessingError("data_path 中的模板字段重复。")
            fields.add(field_name)
            parts.append(fr"(?P<{field_name}>\d+)")
    except ValueError as exc:
        raise ProcessingError("info.json 中的 data_path 模板无效。") from exc
    if fields != {"chunk_index", "file_index"}:
        raise ProcessingError("data_path 必须包含 chunk_index 和 file_index。")
    parts.append("$")
    return re.compile("".join(parts))


def _discover_episode_data_locations(
    ref: DatasetRef,
    info: dict[str, Any],
    data_files: list[Path],
    episode_indexes: set[int],
) -> dict[int, tuple[int, int]]:
    """从 frame Parquet 反查早期元数据缺失的 data 分片索引。"""

    template = str(info.get("data_path") or DEFAULT_DATA_PATH)
    pattern = _data_path_pattern(template)
    locations: dict[int, tuple[int, int]] = {}
    try:
        for path in data_files:
            relative = path.relative_to(ref.path).as_posix()
            match = pattern.fullmatch(relative)
            if match is None:
                raise ProcessingError(f"frame 文件路径不符合 data_path：{relative}")
            location = (int(match.group("chunk_index")), int(match.group("file_index")))
            table = pq.read_table(path, columns=["episode_index"])
            contained = {int(value) for value in pc.unique(table["episode_index"]).to_pylist()}
            unknown = contained - episode_indexes
            if unknown:
                raise ProcessingError(f"frame 数据引用了不存在的 Episode {min(unknown)}。")
            for episode_index in contained:
                previous = locations.get(episode_index)
                if previous is not None and previous != location:
                    raise ProcessingError(
                        f"Episode {episode_index} 跨越多个 frame Parquet，暂不支持自动定位。"
                    )
                locations[episode_index] = location
    except ProcessingError:
        raise
    except (OSError, KeyError, TypeError, ValueError, pa.ArrowException) as exc:
        raise ProcessingError("无法从 frame Parquet 自动定位 Episode。") from exc

    missing = episode_indexes - set(locations)
    if missing:
        raise ProcessingError(f"没有找到 Episode {min(missing)} 对应的 frame 数据。")
    return locations


def _load_state(ref: DatasetRef) -> DatasetState:
    info = _read_json(ref.path / "meta" / "info.json")
    if info.get("codebase_version") != "v3.0":
        raise ProcessingError(f"{ref.dataset_id} 不是 LeRobot v3.0 数据集。")

    task_path = ref.path / "meta" / "tasks.parquet"
    if not task_path.is_file():
        raise ProcessingError(f"{ref.dataset_id} 缺少 meta/tasks.parquet。")
    task_names, task_indexes = _load_tasks(task_path)

    episode_files = _parquet_files(ref.path / "meta" / "episodes")
    data_files = _parquet_files(ref.path / "data")
    if not episode_files or not data_files:
        raise ProcessingError(f"{ref.dataset_id} 缺少 Episode 或 frame Parquet。")
    try:
        episode_tables = [pq.read_table(path) for path in episode_files]
        episode_schema = episode_tables[0].schema
        if any(table.schema != episode_schema for table in episode_tables[1:]):
            raise ProcessingError("Episode 元数据分片结构不一致。")
        episode_table = pa.concat_tables(episode_tables)
        data_schema = pq.read_schema(data_files[0])
        if any(pq.read_schema(path) != data_schema for path in data_files[1:]):
            raise ProcessingError("frame 数据分片结构不一致。")
    except ProcessingError:
        raise
    except (OSError, pa.ArrowException) as exc:
        raise ProcessingError(f"无法读取 {ref.dataset_id} 的 Parquet。") from exc

    required_episode = {"episode_index"}
    required_data = {"episode_index", "frame_index", "index", "task_index"}
    if not required_episode.issubset(episode_table.column_names):
        raise ProcessingError("Episode 元数据缺少必要索引字段。")
    if not required_data.issubset(data_schema.names):
        raise ProcessingError("frame 数据缺少必要索引字段。")

    rows = episode_table.to_pylist()
    by_index = {int(row["episode_index"]): row for row in rows}
    if len(by_index) != len(rows):
        raise ProcessingError("Episode 编号重复。")
    missing_data_locations = any(
        row.get("data/chunk_index") is None or row.get("data/file_index") is None
        for row in rows
    )
    discovered_locations: dict[int, tuple[int, int]] = {}
    if missing_data_locations:
        discovered_locations = _discover_episode_data_locations(
            ref,
            info,
            data_files,
            set(by_index),
        )
    for row in rows:
        episode_index = int(row["episode_index"])
        if row.get("data/chunk_index") is None or row.get("data/file_index") is None:
            chunk_index, file_index = discovered_locations[episode_index]
            row["data/chunk_index"] = chunk_index
            row["data/file_index"] = file_index
        row.setdefault("length", _episode_length(row))
        if "tasks" not in row:
            task = str(row.get("task") or "").strip()
            row["tasks"] = [task] if task else []
        row.setdefault("meta/episodes/chunk_index", 0)
        row.setdefault("meta/episodes/file_index", 0)
    episode_schema = _episode_schema_with_compatibility_fields(episode_schema)
    rows.sort(key=lambda row: int(row["episode_index"]))
    annotations, subtasks = _load_annotations(ref.path)
    return DatasetState(
        ref=ref,
        info=info,
        task_names=task_names,
        task_indexes=task_indexes,
        episode_rows=rows,
        episode_schema=episode_schema,
        episode_by_index=by_index,
        data_schema=data_schema,
        episode_annotations=annotations,
        subtask_annotations=subtasks,
    )


def _episode_length(row: dict[str, Any]) -> int:
    value = row.get("length")
    if value is None:
        value = row.get("num_frames")
    if value is None:
        start = row.get("dataset_from_index")
        end = row.get("dataset_to_index")
        if start is not None and end is not None:
            value = int(end) - int(start)
    try:
        length = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ProcessingError("Episode length 无效。") from exc
    if length <= 0:
        raise ProcessingError("Episode length 必须大于 0。")
    return length


def _safe_relative_path(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts:
        raise ProcessingError("数据集内部路径不安全。")
    candidate = root.joinpath(*posix.parts).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ProcessingError("数据集内部路径越界。")
    return candidate


def _format_path(pattern: str, **values: Any) -> str:
    try:
        return pattern.format(**values)
    except (KeyError, ValueError) as exc:
        raise ProcessingError("info.json 中的数据路径模板无效。") from exc


def _data_file_for_episode(state: DatasetState, row: dict[str, Any]) -> Path:
    relative = _format_path(
        str(state.info.get("data_path") or DEFAULT_DATA_PATH),
        chunk_index=int(row["data/chunk_index"]),
        file_index=int(row["data/file_index"]),
    )
    path = _safe_relative_path(state.ref.path, relative)
    if not path.is_file():
        raise ProcessingError(f"找不到 frame 文件：{relative}")
    return path


def _read_episode_data(state: DatasetState, episode_index: int) -> pa.Table:
    row = state.episode_by_index.get(episode_index)
    if row is None:
        raise ProcessingError(f"不存在 Episode {episode_index}。")
    try:
        table = pq.read_table(_data_file_for_episode(state, row))
        mask = pc.equal(table["episode_index"], pa.scalar(episode_index))
        episode = table.filter(mask)
    except (OSError, pa.ArrowException) as exc:
        raise ProcessingError(f"无法读取 Episode {episode_index} 的 frame 数据。") from exc
    expected = _episode_length(row)
    if episode.num_rows != expected:
        raise ProcessingError(
            f"Episode {episode_index} 的 length={expected}，但实际读取到 {episode.num_rows} 帧。"
        )
    return episode


def _episode_task(row: dict[str, Any], state: DatasetState) -> str:
    tasks = row.get("tasks")
    if isinstance(tasks, (list, np.ndarray)) and len(tasks):
        return str(tasks[0])
    data = _read_episode_data(state, int(row["episode_index"]))
    indexes = data["task_index"].to_pylist()
    return state.task_names.get(int(indexes[0]), "") if indexes else ""


def list_datasets(data_root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in DatasetRepository(data_root).list():
        try:
            dataset = resolve_dataset(data_root, record["id"])
            info = _read_json(dataset.path / "meta" / "info.json")
        except ProcessingError:
            continue
        if info.get("codebase_version") != "v3.0":
            continue
        result.append(
            {
                "dataset_id": record["id"],
                "dataset_name": record["name"],
                "kind": record["storage_area"],
                "total_episodes": int(info.get("total_episodes") or 0),
                "total_frames": int(info.get("total_frames") or 0),
                "fps": info.get("fps"),
                "robot_type": info.get("robot_type") or "未声明",
            }
        )
    result.sort(key=lambda item: (item["kind"], item["dataset_name"]))
    return result


def get_dataset_workspace(data_root: Path, dataset_id: str) -> dict[str, Any]:
    state = _load_state(resolve_dataset(data_root, dataset_id))
    fps = float(state.info.get("fps") or 0)
    episodes = []
    for row in state.episode_rows:
        length = _episode_length(row)
        episodes.append(
            {
                "episode_index": int(row["episode_index"]),
                "length": length,
                "duration_seconds": round(length / fps, 3) if fps else None,
                "task": _episode_task(row, state),
            }
        )
    return {
        "dataset": {
            "dataset_id": state.ref.dataset_id,
            "kind": state.ref.kind,
            "total_episodes": len(episodes),
            "total_frames": sum(item["length"] for item in episodes),
            "fps": state.info.get("fps"),
            "robot_type": state.info.get("robot_type") or "未声明",
            "video_keys": _video_keys(state.info),
        },
        "episodes": episodes,
    }


def _normalized_episode_hash(state: DatasetState, episode_index: int) -> str:
    table = _read_episode_data(state, episode_index)
    ignored = {"index", "episode_index"}
    columns = [name for name in table.column_names if name not in ignored]
    normalized = table.select(columns).replace_schema_metadata(None).combine_chunks()
    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, normalized.schema) as writer:
        writer.write_table(normalized)
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def scan_duplicates(data_root: Path, dataset_id: str) -> dict[str, Any]:
    state = _load_state(resolve_dataset(data_root, dataset_id))
    groups: dict[str, list[int]] = {}
    for row in state.episode_rows:
        episode_index = int(row["episode_index"])
        fingerprint = _normalized_episode_hash(state, episode_index)
        groups.setdefault(fingerprint, []).append(episode_index)
    duplicates = [
        {"fingerprint": fingerprint[:16], "episodes": indexes, "keep": indexes[0]}
        for fingerprint, indexes in groups.items()
        if len(indexes) > 1
    ]
    duplicates.sort(key=lambda item: item["episodes"][0])
    return {
        "dataset_id": dataset_id,
        "duplicate_groups": duplicates,
        "duplicate_episode_count": sum(len(item["episodes"]) - 1 for item in duplicates),
        "method": "frame_parquet_sha256",
    }


def _video_keys(info: dict[str, Any]) -> list[str]:
    features = info.get("features")
    if not isinstance(features, dict):
        return []
    return [
        key
        for key, value in features.items()
        if isinstance(value, dict) and value.get("dtype") == "video"
    ]


def _replace_column(table: pa.Table, name: str, values: Any) -> pa.Table:
    index = table.schema.get_field_index(name)
    if index < 0:
        raise ProcessingError(f"frame 数据缺少 {name}。")
    column = pa.array(values, type=table.schema.field(index).type)
    return table.set_column(index, name, column)


def _remap_episode_data(
    table: pa.Table,
    *,
    state: DatasetState,
    new_episode_index: int,
    global_start: int,
    output_task_indexes: dict[str, int],
) -> tuple[pa.Table, list[str]]:
    old_task_indexes = [int(value) for value in table["task_index"].to_pylist()]
    task_texts: list[str] = []
    new_task_indexes: list[int] = []
    for old_index in old_task_indexes:
        text = state.task_names.get(old_index)
        if text is None:
            raise ProcessingError(f"frame 引用了不存在的 task_index={old_index}。")
        task_texts.append(text)
        new_task_indexes.append(output_task_indexes[text])

    count = table.num_rows
    table = _replace_column(table, "episode_index", [new_episode_index] * count)
    table = _replace_column(table, "frame_index", range(count))
    table = _replace_column(table, "index", range(global_start, global_start + count))
    table = _replace_column(table, "task_index", new_task_indexes)
    return table, list(dict.fromkeys(task_texts))


def _stat_value_like(original: Any, values: np.ndarray) -> Any:
    array = np.asarray(original)
    result = np.asarray(values)
    if array.shape:
        result = result.reshape(array.shape)
    if array.dtype != object:
        result = result.astype(array.dtype, copy=False)
    return result


def _update_index_stats(row: dict[str, Any], table: pa.Table) -> None:
    for name in ("index", "episode_index", "frame_index", "task_index"):
        if name not in table.column_names:
            continue
        raw_values = table[name].to_pylist()
        values = np.asarray(raw_values, dtype=np.float64)
        replacements = {
            "min": np.asarray([values.min()]),
            "max": np.asarray([values.max()]),
            "mean": np.asarray([values.mean()]),
            "std": np.asarray([values.std()]),
            "count": np.asarray([len(values)]),
        }
        for stat, replacement in replacements.items():
            key = f"stats/{name}/{stat}"
            if key in row:
                row[key] = _stat_value_like(row[key], replacement)


def _video_source_path(
    state: DatasetState,
    row: dict[str, Any],
    video_key: str,
) -> tuple[Path, float, float]:
    chunk = row.get(f"videos/{video_key}/chunk_index")
    file_index = row.get(f"videos/{video_key}/file_index")
    if chunk is None or file_index is None:
        raise ProcessingError(f"Episode 缺少 {video_key} 的视频索引。")
    relative = _format_path(
        str(state.info.get("video_path") or DEFAULT_VIDEO_PATH),
        video_key=video_key,
        chunk_index=int(chunk),
        file_index=int(file_index),
    )
    source = _safe_relative_path(state.ref.path, relative)
    if not source.is_file():
        raise ProcessingError(f"找不到视频文件：{relative}")
    start = float(row.get(f"videos/{video_key}/from_timestamp") or 0)
    end_value = row.get(f"videos/{video_key}/to_timestamp")
    fps = float(state.info.get("fps") or 0)
    end = float(end_value) if end_value is not None else start + _episode_length(row) / fps
    return source, start, end


def _slice_video(
    source: Path,
    destination: Path,
    *,
    start: float,
    end: float,
    fps: float,
    expected_frames: int,
) -> None:
    """把共享视频中的一个 Episode 精确重编码为独立 MP4。"""

    try:
        import av
    except ImportError as exc:  # pragma: no cover - 镜像依赖缺失时的部署提示
        raise ProcessingError("平台缺少视频处理依赖 av，请重新构建镜像。") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".mp4.tmp")
    rate = Fraction(str(fps)).limit_denominator(100_000)
    try:
        with av.open(str(source)) as input_container:
            input_stream = input_container.streams.video[0]
            width = int(input_stream.codec_context.width)
            height = int(input_stream.codec_context.height)
            if input_stream.time_base is not None:
                seek_offset = max(0, int(start / float(input_stream.time_base)))
                input_container.seek(
                    seek_offset,
                    stream=input_stream,
                    backward=True,
                    any_frame=False,
                )

            with av.open(str(temporary), mode="w", format="mp4") as output_container:
                output_stream = output_container.add_stream("libx264", rate=rate)
                output_stream.width = width
                output_stream.height = height
                output_stream.pix_fmt = "yuv420p"
                written = 0
                tolerance = 0.5 / fps
                for frame in input_container.decode(input_stream):
                    frame_time = (
                        float(frame.pts * frame.time_base)
                        if frame.pts is not None and frame.time_base is not None
                        else None
                    )
                    if frame_time is not None and frame_time < start - tolerance:
                        continue
                    if frame_time is not None and frame_time >= end - tolerance:
                        break
                    if written >= expected_frames:
                        break
                    output_frame = frame.reformat(width=width, height=height, format="yuv420p")
                    output_frame.pts = written
                    output_frame.time_base = Fraction(1, 1) / rate
                    for packet in output_stream.encode(output_frame):
                        output_container.mux(packet)
                    written += 1
                for packet in output_stream.encode():
                    output_container.mux(packet)
        if written != expected_frames:
            raise ProcessingError(
                f"视频片段应包含 {expected_frames} 帧，实际解码到 {written} 帧。"
            )
        temporary.replace(destination)
    except ProcessingError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise ProcessingError(f"无法处理视频片段 {source.name}。") from exc


def _compatibility_signature(state: DatasetState) -> tuple[Any, ...]:
    return (
        float(state.info.get("fps") or 0),
        state.info.get("robot_type"),
        json.dumps(state.info.get("features"), ensure_ascii=False, sort_keys=True),
        str(state.data_schema.remove_metadata()),
        str(state.episode_schema.remove_metadata()),
    )


def _validate_compatible(states: list[DatasetState]) -> None:
    if not states:
        raise ProcessingError("没有可处理的源数据集。")
    expected = _compatibility_signature(states[0])
    for state in states[1:]:
        if _compatibility_signature(state) != expected:
            raise ProcessingError(
                f"{state.ref.dataset_id} 与 {states[0].ref.dataset_id} 的 FPS、机器人、"
                "feature 或 Parquet 结构不一致，不能合并。"
            )


def _task_catalog(selections: list[EpisodeSelection]) -> tuple[list[str], dict[str, int]]:
    ordered: list[str] = []
    seen: set[str] = set()
    for selection in selections:
        for _, text in sorted(selection.state.task_names.items()):
            if text not in seen:
                seen.add(text)
                ordered.append(text)
    return ordered, {text: index for index, text in enumerate(ordered)}


def _write_tasks(path: Path, tasks: list[str]) -> None:
    frame = pd.DataFrame(
        {"task_index": list(range(len(tasks)))},
        index=pd.Index(tasks, name="task"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)


def _aggregate_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    features: set[str] = set()
    for key in rows[0] if rows else []:
        if key.startswith("stats/") and key.count("/") >= 2:
            features.add(key.split("/", 2)[1])

    output: dict[str, Any] = {}
    for feature in sorted(features):
        required = [f"stats/{feature}/{name}" for name in ("min", "max", "mean", "std", "count")]
        if any(any(key not in row for key in required) for row in rows):
            continue
        counts = [float(np.asarray(row[required[4]]).reshape(-1)[0]) for row in rows]
        total = sum(counts)
        if total <= 0:
            continue
        minimum = np.minimum.reduce([np.asarray(row[required[0]], dtype=np.float64) for row in rows])
        maximum = np.maximum.reduce([np.asarray(row[required[1]], dtype=np.float64) for row in rows])
        means = [np.asarray(row[required[2]], dtype=np.float64) for row in rows]
        stds = [np.asarray(row[required[3]], dtype=np.float64) for row in rows]
        mean = sum(value * count for value, count in zip(means, counts, strict=True)) / total
        second = sum(
            (std**2 + value**2) * count
            for value, std, count in zip(means, stds, counts, strict=True)
        ) / total
        std = np.sqrt(np.maximum(0, second - mean**2))
        output[feature] = {
            "min": minimum.tolist(),
            "max": maximum.tolist(),
            "mean": mean.tolist(),
            "std": std.tolist(),
            "count": [int(total)],
        }
    return output


def _write_annotations(
    destination: Path,
    selections: list[EpisodeSelection],
) -> None:
    episode_rows: list[dict[str, Any]] = []
    subtask_rows: list[dict[str, Any]] = []
    for new_index, selection in enumerate(selections):
        episode = selection.state.episode_annotations.get(selection.episode_index)
        if episode is not None:
            episode_rows.append({**episode, "episode_index": new_index})
        for subtask in selection.state.subtask_annotations.get(selection.episode_index, []):
            subtask_rows.append({**subtask, "episode_index": new_index})
    if not episode_rows and not subtask_rows:
        return

    root = destination / "meta" / "annotations"
    root.mkdir(parents=True, exist_ok=True)
    episode_schema = pa.schema(
        [
            ("episode_index", pa.int64()),
            ("outcome", pa.string()),
            ("failure_reason", pa.string()),
        ]
    )
    subtask_schema = pa.schema(
        [
            ("episode_index", pa.int64()),
            ("segment_index", pa.int64()),
            ("start_time", pa.float64()),
            ("end_time", pa.float64()),
            ("start_frame", pa.int64()),
            ("end_frame_exclusive", pa.int64()),
            ("description", pa.string()),
            ("action_type", pa.string()),
            ("object_name", pa.string()),
            ("outcome", pa.string()),
        ]
    )
    pq.write_table(pa.Table.from_pylist(episode_rows, schema=episode_schema), root / "episodes.parquet")
    pq.write_table(pa.Table.from_pylist(subtask_rows, schema=subtask_schema), root / "subtasks.parquet")
    _write_json_atomic(
        root / "schema.json",
        {
            "schema_version": "hkc.annotations.v1",
            "time_unit": "seconds_relative_to_episode_start",
            "frame_interval": "[start_frame, end_frame_exclusive)",
            "outcome_values": ["failure", "partial", "success"],
            "updated_at": _utc_now(),
        },
    )


def _build_dataset(
    *,
    destination: Path,
    selections: list[EpisodeSelection],
    dataset_id: str,
) -> dict[str, Any]:
    if not selections:
        raise ProcessingError("处理结果不能是空数据集。")
    unique_states = []
    seen_states: set[int] = set()
    for selection in selections:
        state_identity = id(selection.state)
        if state_identity not in seen_states:
            seen_states.add(state_identity)
            unique_states.append(selection.state)
    _validate_compatible(unique_states)

    base = unique_states[0]
    tasks, task_indexes = _task_catalog(selections)
    if not tasks:
        raise ProcessingError("处理结果没有可用 task。")

    (destination / "data" / "chunk-000").mkdir(parents=True, exist_ok=False)
    (destination / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    _write_tasks(destination / "meta" / "tasks.parquet", tasks)

    data_path = destination / "data" / "chunk-000" / "file-000.parquet"
    episode_rows: list[dict[str, Any]] = []
    global_index = 0
    fps = float(base.info.get("fps") or 0)
    if fps <= 0:
        raise ProcessingError("数据集 FPS 必须大于 0。")
    video_keys = _video_keys(base.info)

    writer = pq.ParquetWriter(data_path, base.data_schema, compression="snappy")
    try:
        for new_index, selection in enumerate(selections):
            source_row = selection.state.episode_by_index[selection.episode_index]
            table = _read_episode_data(selection.state, selection.episode_index)
            table, episode_tasks = _remap_episode_data(
                table,
                state=selection.state,
                new_episode_index=new_index,
                global_start=global_index,
                output_task_indexes=task_indexes,
            )
            writer.write_table(table, row_group_size=table.num_rows)

            length = table.num_rows
            row = deepcopy(source_row)
            row["episode_index"] = new_index
            row["length"] = length
            if "num_frames" in row:
                row["num_frames"] = length
            row["data/chunk_index"] = 0
            row["data/file_index"] = 0
            row["dataset_from_index"] = global_index
            row["dataset_to_index"] = global_index + length
            row["tasks"] = episode_tasks
            if "task" in row:
                row["task"] = episode_tasks[0] if episode_tasks else ""
            row["meta/episodes/chunk_index"] = 0
            row["meta/episodes/file_index"] = 0
            _update_index_stats(row, table)

            for video_key in video_keys:
                source, start, end = _video_source_path(
                    selection.state,
                    source_row,
                    video_key,
                )
                output_video = (
                    destination
                    / "videos"
                    / video_key
                    / "chunk-000"
                    / f"file-{new_index:03d}.mp4"
                )
                _slice_video(
                    source,
                    output_video,
                    start=start,
                    end=end,
                    fps=fps,
                    expected_frames=length,
                )
                row[f"videos/{video_key}/chunk_index"] = 0
                row[f"videos/{video_key}/file_index"] = new_index
                row[f"videos/{video_key}/from_timestamp"] = 0.0
                row[f"videos/{video_key}/to_timestamp"] = length / fps

            episode_rows.append(row)
            global_index += length
    finally:
        writer.close()

    episode_table = pa.Table.from_pylist(episode_rows, schema=base.episode_schema)
    pq.write_table(
        episode_table,
        destination / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
        compression="snappy",
    )
    _write_json_atomic(destination / "meta" / "stats.json", _aggregate_stats(episode_rows))
    _write_annotations(destination, selections)

    info = deepcopy(base.info)
    info["total_episodes"] = len(selections)
    info["total_frames"] = global_index
    info["total_tasks"] = len(tasks)
    info["splits"] = {"train": f"0:{len(selections)}"}
    info["data_path"] = DEFAULT_DATA_PATH
    info["video_path"] = DEFAULT_VIDEO_PATH
    features = info.get("features")
    if isinstance(features, dict):
        for video_key in video_keys:
            feature = features.get(video_key)
            if not isinstance(feature, dict):
                continue
            video_info = feature.setdefault("info", {})
            if isinstance(video_info, dict):
                video_info["video.codec"] = "h264"
                video_info["video.pix_fmt"] = "yuv420p"
                video_info["video.fps"] = fps
                video_info["has_audio"] = False
    _write_json_atomic(destination / "meta" / "info.json", info)
    _validate_output(destination)
    return {
        "dataset_id": dataset_id,
        "total_episodes": len(selections),
        "total_frames": global_index,
        "total_tasks": len(tasks),
    }


def _validate_output(path: Path) -> None:
    info = _read_json(path / "meta" / "info.json")
    task_names, _ = _load_tasks(path / "meta" / "tasks.parquet")
    episode_files = _parquet_files(path / "meta" / "episodes")
    data_files = _parquet_files(path / "data")
    if not episode_files or not data_files:
        raise ProcessingError("派生结果缺少 Episode 或 frame 文件。")
    episodes = pa.concat_tables([pq.read_table(file) for file in episode_files])
    frames = sum(pq.read_metadata(file).num_rows for file in data_files)
    if episodes.num_rows != int(info.get("total_episodes") or -1):
        raise ProcessingError("派生结果的 Episode 数量校验失败。")
    if frames != int(info.get("total_frames") or -1):
        raise ProcessingError("派生结果的 frame 数量校验失败。")
    valid_task_indexes = set(task_names)
    for file in data_files:
        values = {
            int(value)
            for value in pq.read_table(file, columns=["task_index"])["task_index"].to_pylist()
        }
        if not values.issubset(valid_task_indexes):
            raise ProcessingError("派生结果包含无效 task_index。")
    for row in episodes.to_pylist():
        for video_key in _video_keys(info):
            chunk = row.get(f"videos/{video_key}/chunk_index")
            file_index = row.get(f"videos/{video_key}/file_index")
            relative = _format_path(
                str(info.get("video_path") or DEFAULT_VIDEO_PATH),
                video_key=video_key,
                chunk_index=int(chunk),
                file_index=int(file_index),
            )
            if not _safe_relative_path(path, relative).is_file():
                raise ProcessingError("派生结果缺少视频文件。")


def _fraction_splits(total: int, splits: dict[str, float]) -> dict[str, list[int]]:
    if any(value < 0 for value in splits.values()):
        raise ProcessingError("拆分比例不能为负数。")
    if sum(splits.values()) > 1.0 + 1e-9:
        raise ProcessingError("拆分比例总和不能超过 1。")
    positive = [name for name, value in splits.items() if value > 0]
    if not positive:
        raise ProcessingError("至少需要一个大于 0 的拆分比例。")
    if total < len(positive):
        raise ProcessingError("Episode 数量少于非空拆分组数量。")
    counts = {name: int(total * value) for name, value in splits.items()}
    counts[positive[-1]] += total - sum(counts.values())
    for name in positive:
        if counts[name] == 0:
            donor = max(positive, key=lambda item: counts[item])
            if counts[donor] <= 1:
                raise ProcessingError("Episode 数量不足以满足拆分比例。")
            counts[donor] -= 1
            counts[name] = 1
    indexes = list(range(total))
    result: dict[str, list[int]] = {}
    start = 0
    for name in positive:
        end = start + counts[name]
        result[name] = indexes[start:end]
        start = end
    return result


def _selection(state: DatasetState, indexes: list[int]) -> list[EpisodeSelection]:
    available = set(state.episode_by_index)
    invalid = set(indexes) - available
    if invalid:
        raise ProcessingError(f"不存在 Episode {min(invalid)}。")
    return [EpisodeSelection(state, index) for index in indexes]


def _operation_outputs(
    request: ProcessingJobRequest,
    states: list[DatasetState],
) -> list[tuple[str, list[EpisodeSelection]]]:
    operation = request.operation
    if operation == "merge":
        _validate_compatible(states)
        selections = [
            EpisodeSelection(state, int(row["episode_index"]))
            for state in states
            for row in state.episode_rows
        ]
        return [("merged", selections)]

    state = states[0]
    all_indexes = [int(row["episode_index"]) for row in state.episode_rows]
    if operation == "filter":
        removed = set(request.episode_indices)
        invalid = removed - set(all_indexes)
        if invalid:
            raise ProcessingError(f"不存在 Episode {min(invalid)}。")
        kept = [index for index in all_indexes if index not in removed]
        if not kept:
            raise ProcessingError("不能筛除全部 Episode。")
        return [("filtered", _selection(state, kept))]

    if operation == "reorder":
        if set(request.episode_indices) != set(all_indexes) or len(request.episode_indices) != len(
            all_indexes
        ):
            raise ProcessingError("重排必须恰好包含每一个 Episode，且不能重复。")
        return [("reordered", _selection(state, request.episode_indices))]

    if operation == "deduplicate":
        report = scan_duplicates(state.ref.path.parents[1], state.ref.dataset_id)
        removed = {
            index
            for group in report["duplicate_groups"]
            for index in group["episodes"][1:]
        }
        if not removed:
            return []
        kept = [index for index in all_indexes if index not in removed]
        return [("deduplicated", _selection(state, kept))]

    if operation == "split":
        split_values = list(request.splits.values())
        are_ratios = all(isinstance(value, float) for value in split_values)
        are_indexes = all(isinstance(value, list) for value in split_values)
        if not are_ratios and not are_indexes:
            raise ProcessingError("拆分分组必须全部使用比例，或全部使用 Episode 编号。")
        for name in request.splits:
            if not SPLIT_NAME_PATTERN.fullmatch(name):
                raise ProcessingError(f"拆分名称 {name!r} 不合法。")
        if are_ratios:
            groups = _fraction_splits(
                len(all_indexes),
                {name: float(value) for name, value in request.splits.items()},
            )
        else:
            groups = {
                name: [int(index) for index in value]
                for name, value in request.splits.items()
                if isinstance(value, list)
            }
            used: set[int] = set()
            for name, indexes in groups.items():
                if not indexes:
                    raise ProcessingError(f"拆分组 {name} 不能为空。")
                overlap = used & set(indexes)
                if overlap:
                    raise ProcessingError(f"Episode {min(overlap)} 出现在多个拆分组。")
                used.update(indexes)
        return [(name, _selection(state, indexes)) for name, indexes in groups.items()]

    raise ProcessingError("未知的数据处理操作。")


def _new_dataset_id(label: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")[:24] or "result"
    return f"proc-{timestamp}-{safe_label}-{uuid4().hex[:8]}"


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _job_path(data_root: Path, job_id: str) -> Path:
    return data_root / "system" / "processing-jobs" / f"{job_id}.json"


def _read_job(data_root: Path, job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"job-[a-f0-9]{20}", job_id):
        raise ProcessingError("处理任务编号不合法。")
    path = _job_path(data_root, job_id)
    if not path.is_file():
        raise ProcessingError("处理任务不存在。")
    return _read_json(path)


def create_job(data_root: Path, request: ProcessingJobRequest) -> dict[str, Any]:
    for dataset_id in request.source_dataset_ids:
        resolve_dataset(data_root, dataset_id)
    job_id = f"job-{uuid4().hex[:20]}"
    now = _utc_now()
    record = {
        "job_id": job_id,
        "operation": request.operation,
        "status": "queued",
        "progress": 0,
        "message": "任务已进入队列。",
        "version_name": request.version_name.strip(),
        "description": request.description.strip(),
        "source_dataset_ids": request.source_dataset_ids,
        "request": request.model_dump(mode="json"),
        "outputs": [],
        "created_at": now,
        "updated_at": now,
    }
    _write_json_atomic(_job_path(data_root, job_id), record)
    return record


def get_job(data_root: Path, job_id: str) -> dict[str, Any]:
    return _read_job(data_root, job_id)


def list_jobs(data_root: Path, limit: int = 30) -> list[dict[str, Any]]:
    directory = data_root / "system" / "processing-jobs"
    if not directory.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in directory.glob("job-*.json"):
        try:
            records.append(_read_json(path))
        except ProcessingError:
            continue
    records.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return records[:limit]


def _update_job(data_root: Path, record: dict[str, Any], **changes: Any) -> None:
    record.update(changes)
    record["updated_at"] = _utc_now()
    _write_json_atomic(_job_path(data_root, str(record["job_id"])), record)


def _write_version_record(
    data_root: Path,
    *,
    job: dict[str, Any],
    output: dict[str, Any],
    label: str,
) -> None:
    dataset_id = str(output["dataset_id"])
    version_name = str(job["version_name"])
    if label not in {"filtered", "merged", "reordered", "deduplicated"}:
        version_name = f"{version_name} / {label}"
    _write_json_atomic(
        data_root / "system" / "dataset-versions" / f"{dataset_id}.json",
        {
            "dataset_id": dataset_id,
            "version_name": version_name,
            "description": job.get("description", ""),
            "operation": job["operation"],
            "source_dataset_ids": job["source_dataset_ids"],
            "processing_job_id": job["job_id"],
            "created_at": _utc_now(),
            "total_episodes": output["total_episodes"],
            "total_frames": output["total_frames"],
        },
    )


def run_job(data_root: Path, job_id: str) -> None:
    """执行一个已登记任务；供当前容器后台任务或未来 Worker 调用。"""

    record = _read_job(data_root, job_id)
    staging_root = data_root / "staging" / "processing" / job_id
    moved_paths: list[Path] = []
    registered_ids: list[str] = []
    try:
        _update_job(data_root, record, status="running", progress=5, message="正在读取源数据集。")
        request = ProcessingJobRequest.model_validate(record["request"])
        states = [
            _load_state(resolve_dataset(data_root, dataset_id))
            for dataset_id in request.source_dataset_ids
        ]
        outputs = _operation_outputs(request, states)
        if not outputs:
            _update_job(
                data_root,
                record,
                status="completed",
                progress=100,
                message="未发现重复 Episode，没有生成新的派生数据集。",
                outputs=[],
            )
            return

        staging_root.mkdir(parents=True, exist_ok=False)
        staged: list[tuple[str, Path, dict[str, Any]]] = []
        for position, (label, selections) in enumerate(outputs, start=1):
            storage_name = _new_dataset_id(label)
            destination = staging_root / storage_name
            progress = 10 + int((position - 1) / len(outputs) * 75)
            _update_job(
                data_root,
                record,
                progress=progress,
                message=f"正在生成 {label} 派生数据集。",
            )
            result = _build_dataset(
                destination=destination,
                selections=selections,
                dataset_id=storage_name,
            )
            result["storage_name"] = storage_name
            result["label"] = label
            result["size_bytes"] = _directory_size(destination)
            staged.append((label, destination, result))

        derived_root = data_root / "derived"
        derived_root.mkdir(parents=True, exist_ok=True)
        for label, source, result in staged:
            storage_name = str(result["storage_name"])
            final = derived_root / storage_name
            if final.exists():
                raise ProcessingError("派生数据集编号冲突，请重新执行。")
            source.replace(final)
            moved_paths.append(final)
            display_name = str(record["version_name"])
            if label not in {"filtered", "merged", "reordered", "deduplicated"}:
                display_name = f"{display_name} / {label}"
            registered = datasets_service.register_dataset(
                data_root,
                "derived",
                storage_name,
                display_name,
                list(record["source_dataset_ids"]),
            )
            registered_ids.append(registered["id"])
            result["dataset_id"] = registered["id"]
            result["relative_path"] = f"derived/{storage_name}"
            _write_version_record(data_root, job=record, output=result, label=label)

        _update_job(
            data_root,
            record,
            status="completed",
            progress=100,
            message=f"处理完成，共生成 {len(staged)} 个派生数据集。",
            outputs=[item[2] for item in staged],
        )
    # 任务边界必须把所有意外转换为 failed 状态，否则 JSON 会永久停留在 running。
    except Exception as exc:  # noqa: BLE001
        for dataset_id in registered_ids:
            DatasetRepository(data_root).unregister(dataset_id)
            (data_root / "system" / "dataset-versions" / f"{dataset_id}.json").unlink(
                missing_ok=True
            )
        for path in moved_paths:
            shutil.rmtree(path, ignore_errors=True)
        message = str(exc) if isinstance(exc, ProcessingError) else "处理过程中发生内部错误。"
        _update_job(
            data_root,
            record,
            status="failed",
            progress=100,
            message=message,
            outputs=[],
        )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
