"""LeRobot v3 Episode/task 标注与完整派生版本发布。

本模块只操作 ``settings.data_root`` 下的数据。原始数据集和既有派生版本均只读；
正式发布时先复制到 staging，完成 Parquet/JSON 修改和校验后，再原子移动到
``storage/derived``。
"""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.db.datasets import DatasetRepository
from app.modules.annotation.schemas import EpisodeAnnotationChange, EpisodeTaskChange
from app.modules.datasets import service as datasets_service

DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ANNOTATION_OUTCOMES = {"success", "partial", "failure"}
ANNOTATION_SCHEMA_VERSION = "hkc.annotations.v1"


class AnnotationError(Exception):
    """可以安全展示给使用者的标注错误。"""


class DatasetNotFoundError(AnnotationError):
    """请求的数据集不存在。"""


@dataclass(frozen=True, slots=True)
class DatasetRef:
    dataset_id: str
    path: Path
    kind: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnotationError(f"无法读取 {path.name}。") from exc
    if not isinstance(value, dict):
        raise AnnotationError(f"{path.name} 的内容不是 JSON 对象。")
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
    """通过统一 DS 编号解析 raw/derived 中的真实目录。"""

    _validate_dataset_id(dataset_id)
    try:
        record, candidate = datasets_service.resolve_registered_dataset(data_root, dataset_id)
    except ValueError as exc:
        raise DatasetNotFoundError(f"未找到数据集 {dataset_id}。") from exc
    return DatasetRef(dataset_id=dataset_id, path=candidate, kind=record["storage_area"])


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


def _parquet_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.parquet") if path.is_file())


def _load_annotation_sidecars(
    dataset: DatasetRef,
) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    """读取平台扩展标注；这些文件不加入 LeRobot v3 核心 feature。"""

    annotation_root = dataset.path / "meta" / "annotations"
    episode_path = annotation_root / "episodes.parquet"
    subtask_path = annotation_root / "subtasks.parquet"
    outcomes: dict[int, dict[str, Any]] = {}
    subtasks: dict[int, list[dict[str, Any]]] = {}

    try:
        if episode_path.is_file():
            table = pq.read_table(episode_path)
            required = {"episode_index", "outcome", "failure_reason"}
            if not required.issubset(table.column_names):
                raise AnnotationError("Episode 扩展标注文件缺少必要字段。")
            for row in table.select(sorted(required)).to_pylist():
                episode_index = int(row["episode_index"])
                outcome = str(row["outcome"])
                if outcome not in ANNOTATION_OUTCOMES:
                    raise AnnotationError("Episode 扩展标注包含未知执行结果。")
                outcomes[episode_index] = {
                    "outcome": outcome,
                    "failure_reason": str(row["failure_reason"] or ""),
                }

        if subtask_path.is_file():
            table = pq.read_table(subtask_path)
            required = {
                "episode_index",
                "segment_index",
                "start_time",
                "end_time",
                "start_frame",
                "end_frame_exclusive",
                "description",
                "action_type",
                "object_name",
                "outcome",
            }
            if not required.issubset(table.column_names):
                raise AnnotationError("Subtask 扩展标注文件缺少必要字段。")
            for row in table.select(sorted(required)).to_pylist():
                episode_index = int(row["episode_index"])
                outcome = str(row["outcome"])
                if outcome not in ANNOTATION_OUTCOMES:
                    raise AnnotationError("Subtask 扩展标注包含未知执行结果。")
                subtasks.setdefault(episode_index, []).append(
                    {
                        "segment_index": int(row["segment_index"]),
                        "start_time": float(row["start_time"]),
                        "end_time": float(row["end_time"]),
                        "start_frame": int(row["start_frame"]),
                        "end_frame_exclusive": int(row["end_frame_exclusive"]),
                        "description": str(row["description"]),
                        "action_type": str(row["action_type"]),
                        "object_name": str(row["object_name"]),
                        "outcome": outcome,
                    }
                )
    except AnnotationError:
        raise
    except (OSError, pa.ArrowException, TypeError, ValueError) as exc:
        raise AnnotationError("无法读取数据集的扩展标注文件。") from exc

    for values in subtasks.values():
        values.sort(key=lambda item: item["segment_index"])
    return outcomes, subtasks


def _load_info(dataset: DatasetRef) -> dict[str, Any]:
    info = _read_json(dataset.path / "meta" / "info.json")
    if info.get("codebase_version") != "v3.0":
        raise AnnotationError("当前标注工作台只支持 LeRobot v3.0 数据集。")
    return info


def _load_tasks(dataset: DatasetRef) -> tuple[list[dict[str, Any]], dict[int, str]]:
    path = dataset.path / "meta" / "tasks.parquet"
    if not path.is_file():
        raise AnnotationError("数据集缺少 meta/tasks.parquet。")

    try:
        # 与 LeRobot 官方 load_tasks 保持相同语义：自然语言 task 是 DataFrame
        # 索引，task_index 才是普通列。旧版平台曾把 task 写成普通列，因此这里
        # 继续兼容该历史格式，并把它统一转换成官方内存结构。
        frame = pd.read_parquet(path)
    except (OSError, ValueError, pa.ArrowException) as exc:
        raise AnnotationError("无法读取 meta/tasks.parquet。") from exc
    if "task_index" not in frame.columns:
        raise AnnotationError("tasks.parquet 缺少 task_index 列。")
    if isinstance(frame.index, pd.RangeIndex):
        if "task" not in frame.columns:
            raise AnnotationError("tasks.parquet 缺少 task 索引或兼容 task 列。")
        frame = frame.set_index("task")
    frame.index.name = "task"
    if frame.index.hasnans or not frame.index.is_unique:
        raise AnnotationError("tasks.parquet 的 task 索引为空或重复。")
    if frame["task_index"].isna().any() or not frame["task_index"].is_unique:
        raise AnnotationError("tasks.parquet 的 task_index 为空或重复。")

    tasks: list[dict[str, Any]] = []
    mapping: dict[int, str] = {}
    for task_value, index_value in zip(frame.index, frame["task_index"], strict=True):
        index = int(index_value)
        task = str(task_value).strip()
        if not task:
            raise AnnotationError("tasks.parquet 包含空 task。")
        mapping[index] = task
        tasks.append({"task_index": index, "task": task})
    tasks.sort(key=lambda item: item["task_index"])
    return tasks, mapping


def _episode_task_from_data(dataset: DatasetRef) -> dict[int, int]:
    """从 frame 数据中补充 episode -> task_index 映射。"""

    result: dict[int, int] = {}
    for path in _parquet_files(dataset.path / "data"):
        table = pq.read_table(path, columns=["episode_index", "task_index"])
        for row in table.to_pylist():
            result.setdefault(int(row["episode_index"]), int(row["task_index"]))
    return result


def _video_keys(info: dict[str, Any]) -> list[str]:
    features = info.get("features")
    if not isinstance(features, dict):
        return []
    return [
        key
        for key, value in features.items()
        if isinstance(value, dict) and value.get("dtype") == "video"
    ]


def _safe_video_path(dataset: DatasetRef, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts:
        raise AnnotationError("数据集的视频路径不安全。")
    candidate = dataset.path.joinpath(*posix.parts).resolve()
    dataset_root = dataset.path.resolve()
    if candidate != dataset_root and dataset_root not in candidate.parents:
        raise AnnotationError("数据集的视频路径越界。")
    if not candidate.is_file():
        raise AnnotationError(f"找不到视频文件：{relative}")
    return candidate


def _format_video_path(
    pattern: str,
    *,
    video_key: str,
    chunk_index: int,
    file_index: int,
) -> str:
    try:
        return pattern.format(
            video_key=video_key,
            chunk_index=chunk_index,
            file_index=file_index,
        )
    except (KeyError, ValueError) as exc:
        raise AnnotationError("info.json 中的 video_path 模板无效。") from exc


def _episode_length(row: dict[str, Any]) -> int:
    """读取 LeRobot v3 Episode 帧数，并兼容平台早期测试数据。"""

    # LeRobot v3 官方写入 ``length``。早期平台样例使用了 ``num_frames``，
    # 因此保留兼容；索引区间则作为最后一道无歧义的回退。
    value = row.get("length")
    if value is None:
        value = row.get("num_frames")
    if value is None:
        start = row.get("dataset_from_index")
        end = row.get("dataset_to_index")
        if start is not None and end is not None:
            value = int(end) - int(start)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError) as exc:
        raise AnnotationError("Episode 元数据中的 length 无效。") from exc


def load_episodes(dataset: DatasetRef) -> list[dict[str, Any]]:
    info = _load_info(dataset)
    _, task_names = _load_tasks(dataset)
    episode_annotations, subtask_annotations = _load_annotation_sidecars(dataset)
    files = _parquet_files(dataset.path / "meta" / "episodes")
    if not files:
        raise AnnotationError("数据集缺少 meta/episodes 下的 Parquet 文件。")

    tables = [pq.read_table(path) for path in files]
    table = pa.concat_tables(tables, promote_options="default")
    if "episode_index" not in table.column_names:
        raise AnnotationError("Episode 元数据缺少 episode_index。")

    data_task_indexes: dict[int, int] = {}
    if "task" not in table.column_names and "tasks" not in table.column_names:
        data_task_indexes = _episode_task_from_data(dataset)

    video_keys = _video_keys(info)
    pattern = str(
        info.get("video_path")
        or "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
    )
    fps = float(info.get("fps") or 0)
    episodes: list[dict[str, Any]] = []
    for row in table.to_pylist():
        episode_index = int(row["episode_index"])
        task_value = row.get("task")
        if task_value is None:
            tasks_value = row.get("tasks")
            if isinstance(tasks_value, list) and tasks_value:
                task_value = tasks_value[0]
        if task_value is None:
            task_value = task_names.get(data_task_indexes.get(episode_index, -1), "")

        num_frames = _episode_length(row)
        videos: list[dict[str, Any]] = []
        for video_key in video_keys:
            chunk = row.get(f"videos/{video_key}/chunk_index")
            file_index = row.get(f"videos/{video_key}/file_index")
            if chunk is None or file_index is None:
                continue
            relative = _format_video_path(
                pattern,
                video_key=video_key,
                chunk_index=int(chunk),
                file_index=int(file_index),
            )
            _safe_video_path(dataset, relative)
            start = float(row.get(f"videos/{video_key}/from_timestamp") or 0)
            end_value = row.get(f"videos/{video_key}/to_timestamp")
            end = float(end_value) if end_value is not None else start + num_frames / fps
            videos.append(
                {
                    "camera": video_key,
                    "start": start,
                    "end": end,
                    "url": (
                        f"/annotation/api/datasets/{quote(dataset.dataset_id, safe='')}"
                        f"/episodes/{episode_index}/video?camera={quote(video_key, safe='')}"
                    ),
                }
            )

        annotation = episode_annotations.get(episode_index)
        episodes.append(
            {
                "episode_index": episode_index,
                "task": str(task_value or ""),
                "num_frames": num_frames,
                "duration_seconds": round(num_frames / fps, 3) if fps else None,
                "videos": videos,
                "annotation": {
                    "outcome": annotation["outcome"] if annotation else None,
                    "failure_reason": annotation["failure_reason"] if annotation else "",
                    "subtasks": subtask_annotations.get(episode_index, []),
                },
            }
        )
    episodes.sort(key=lambda item: item["episode_index"])
    return episodes


def list_datasets(data_root: Path) -> list[dict[str, Any]]:
    datasets: list[dict[str, Any]] = []
    for record in DatasetRepository(data_root).list():
        try:
            dataset = resolve_dataset(data_root, record["id"])
            info = _load_info(dataset)
        except AnnotationError:
            continue
        kind = record["storage_area"]
        datasets.append(
            {
                "dataset_id": record["id"],
                "dataset_name": record["name"],
                "kind": kind,
                "kind_label": "原始数据集" if kind == "raw" else "派生数据集",
                "total_episodes": int(info.get("total_episodes") or 0),
                "total_frames": int(info.get("total_frames") or 0),
                "fps": info.get("fps"),
                "robot_type": info.get("robot_type") or "未声明",
            }
        )
    return datasets


def get_workspace(data_root: Path, dataset_id: str) -> dict[str, Any]:
    dataset = resolve_dataset(data_root, dataset_id)
    info = _load_info(dataset)
    tasks, _ = _load_tasks(dataset)
    episodes = load_episodes(dataset)
    return {
        "dataset": {
            "dataset_id": dataset.dataset_id,
            "kind": dataset.kind,
            "total_episodes": int(info.get("total_episodes") or len(episodes)),
            "total_frames": int(info.get("total_frames") or 0),
            "fps": info.get("fps"),
            "robot_type": info.get("robot_type") or "未声明",
            "cameras": _video_keys(info),
        },
        "tasks": tasks,
        "episodes": episodes,
    }


def resolve_episode_video(
    data_root: Path,
    dataset_id: str,
    episode_index: int,
    camera: str,
) -> Path:
    dataset = resolve_dataset(data_root, dataset_id)
    info = _load_info(dataset)
    if camera not in _video_keys(info):
        raise AnnotationError("请求的相机不存在。")

    files = _parquet_files(dataset.path / "meta" / "episodes")
    for parquet_path in files:
        table = pq.read_table(parquet_path)
        for row in table.to_pylist():
            if int(row.get("episode_index", -1)) != episode_index:
                continue
            chunk = row.get(f"videos/{camera}/chunk_index")
            file_index = row.get(f"videos/{camera}/file_index")
            if chunk is None or file_index is None:
                raise AnnotationError("该 Episode 没有请求相机的视频。")
            pattern = str(
                info.get("video_path")
                or "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
            )
            relative = _format_video_path(
                pattern,
                video_key=camera,
                chunk_index=int(chunk),
                file_index=int(file_index),
            )
            return _safe_video_path(dataset, relative)
    raise AnnotationError("请求的 Episode 不存在。")


def _write_parquet_atomic(table: pa.Table, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, temporary, compression="snappy")
    temporary.replace(path)


def _copy_source(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, copy_function=shutil.copy2)


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _updated_task_catalog(
    current: list[dict[str, Any]], changes: dict[int, str]
) -> tuple[pd.DataFrame, dict[str, int]]:
    by_text = {str(item["task"]): int(item["task_index"]) for item in current}
    next_index = max(by_text.values(), default=-1) + 1
    for text in changes.values():
        if text not in by_text:
            by_text[text] = next_index
            next_index += 1
    ordered = sorted(by_text.items(), key=lambda item: item[1])
    frame = pd.DataFrame(
        {"task_index": [index for _, index in ordered]},
        index=pd.Index([text for text, _ in ordered], name="task"),
    )
    return frame, by_text


def _write_tasks_atomic(frame: pd.DataFrame, path: Path) -> None:
    """按照 LeRobot 官方 write_tasks 的 DataFrame 索引格式原子写入。"""

    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary)
    temporary.replace(path)


def _update_episode_files(staging: Path, changes: dict[int, str]) -> None:
    for path in _parquet_files(staging / "meta" / "episodes"):
        table = pq.read_table(path)
        rows = table.to_pylist()
        if "task" in table.column_names:
            values = [changes.get(int(row["episode_index"]), row.get("task") or "") for row in rows]
            index = table.schema.get_field_index("task")
            table = table.set_column(index, "task", pa.array(values, type=table.schema.field(index).type))
        elif "tasks" in table.column_names:
            values = [
                [changes[int(row["episode_index"])]]
                if int(row["episode_index"]) in changes
                else row.get("tasks") or []
                for row in rows
            ]
            index = table.schema.get_field_index("tasks")
            table = table.set_column(index, "tasks", pa.array(values, type=table.schema.field(index).type))
        else:
            raise AnnotationError("Episode 元数据缺少 task/tasks 列，无法安全发布。")
        _write_parquet_atomic(table, path)


def _update_data_files(staging: Path, changes: dict[int, str], task_indexes: dict[str, int]) -> None:
    for path in _parquet_files(staging / "data"):
        table = pq.read_table(path)
        if not {"episode_index", "task_index"}.issubset(table.column_names):
            raise AnnotationError(f"{path.name} 缺少 episode_index/task_index。")
        rows = table.select(["episode_index", "task_index"]).to_pylist()
        values = [
            task_indexes[changes[int(row["episode_index"])]]
            if int(row["episode_index"]) in changes
            else int(row["task_index"])
            for row in rows
        ]
        index = table.schema.get_field_index("task_index")
        table = table.set_column(
            index,
            "task_index",
            pa.array(values, type=table.schema.field(index).type),
        )
        _write_parquet_atomic(table, path)


def _normalize_annotation_changes(
    changes: list[EpisodeAnnotationChange],
    episodes: dict[int, dict[str, Any]],
    fps: float,
) -> dict[int, dict[str, Any]]:
    """清理标注并把 Episode 相对时间换算为稳定的帧区间。"""

    if changes and fps <= 0:
        raise AnnotationError("数据集 FPS 无效，无法保存 Subtask 时间段。")

    normalized: dict[int, dict[str, Any]] = {}
    for change in changes:
        episode_index = change.episode_index
        if episode_index not in episodes:
            raise AnnotationError(f"数据集中不存在 Episode {episode_index}。")
        if episode_index in normalized:
            raise AnnotationError(f"Episode {episode_index} 出现重复执行结果标注。")

        failure_reason = change.failure_reason.strip()
        if change.outcome in {"partial", "failure"} and not failure_reason:
            raise AnnotationError("部分成功或失败的 Episode 必须填写原因。")
        if change.outcome == "success":
            failure_reason = ""

        episode = episodes[episode_index]
        num_frames = int(episode["num_frames"])
        if num_frames <= 0:
            raise AnnotationError(f"Episode {episode_index} 没有可标注的帧。")
        duration = num_frames / fps
        segments: list[dict[str, Any]] = []
        for segment in change.subtasks:
            start = float(segment.start_time)
            end = float(segment.end_time)
            if not math.isfinite(start) or not math.isfinite(end):
                raise AnnotationError("Subtask 时间必须是有限数字。")
            if end <= start:
                raise AnnotationError("Subtask 结束时间必须晚于开始时间。")
            if end > duration + 1e-6:
                raise AnnotationError(
                    f"Episode {episode_index} 的 Subtask 超出 Episode 时长。"
                )

            description = segment.description.strip()
            action_type = segment.action_type.strip()
            object_name = segment.object_name.strip()
            if not description or not action_type or not object_name:
                raise AnnotationError("Subtask 描述、动作类型和对象均不能为空。")

            start_frame = min(num_frames - 1, math.floor(start * fps + 1e-9))
            end_frame_exclusive = min(num_frames, math.ceil(end * fps - 1e-9))
            end_frame_exclusive = max(start_frame + 1, end_frame_exclusive)
            segments.append(
                {
                    "start_time": round(start, 6),
                    "end_time": round(end, 6),
                    "start_frame": start_frame,
                    "end_frame_exclusive": end_frame_exclusive,
                    "description": description,
                    "action_type": action_type,
                    "object_name": object_name,
                    "outcome": segment.outcome,
                }
            )

        segments.sort(key=lambda item: (item["start_time"], item["end_time"]))
        previous_end = -1.0
        for segment_index, segment in enumerate(segments):
            if segment["start_time"] < previous_end - 1e-9:
                raise AnnotationError(
                    f"Episode {episode_index} 的 Subtask 时间段不能互相重叠。"
                )
            segment["segment_index"] = segment_index
            previous_end = segment["end_time"]

        normalized[episode_index] = {
            "outcome": change.outcome,
            "failure_reason": failure_reason,
            "subtasks": segments,
        }
    return normalized


def _write_annotation_sidecars(
    staging: Path,
    changes: dict[int, dict[str, Any]],
) -> None:
    """合并并写入平台扩展标注，不改变 LeRobot v3 核心 Parquet。"""

    dataset = DatasetRef("staging", staging, "staging")
    outcomes, subtasks = _load_annotation_sidecars(dataset)
    for episode_index, annotation in changes.items():
        outcomes[episode_index] = {
            "outcome": annotation["outcome"],
            "failure_reason": annotation["failure_reason"],
        }
        subtasks[episode_index] = annotation["subtasks"]

    episode_schema = pa.schema(
        [
            ("episode_index", pa.int64()),
            ("outcome", pa.string()),
            ("failure_reason", pa.string()),
        ]
    )
    episode_rows = [
        {
            "episode_index": episode_index,
            "outcome": value["outcome"],
            "failure_reason": value["failure_reason"],
        }
        for episode_index, value in sorted(outcomes.items())
    ]

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
    subtask_rows: list[dict[str, Any]] = []
    for episode_index, values in sorted(subtasks.items()):
        for value in values:
            subtask_rows.append({"episode_index": episode_index, **value})

    root = staging / "meta" / "annotations"
    root.mkdir(parents=True, exist_ok=True)
    _write_parquet_atomic(
        pa.Table.from_pylist(episode_rows, schema=episode_schema),
        root / "episodes.parquet",
    )
    _write_parquet_atomic(
        pa.Table.from_pylist(subtask_rows, schema=subtask_schema),
        root / "subtasks.parquet",
    )
    _write_json_atomic(
        root / "schema.json",
        {
            "schema_version": ANNOTATION_SCHEMA_VERSION,
            "time_unit": "seconds_relative_to_episode_start",
            "frame_interval": "[start_frame, end_frame_exclusive)",
            "outcome_values": sorted(ANNOTATION_OUTCOMES),
            "updated_at": _utc_now(),
        },
    )


def _validate_published_dataset(
    staging: Path,
    task_changes: dict[int, str],
    annotation_changes: dict[int, dict[str, Any]],
) -> None:
    dataset = DatasetRef("staging", staging, "staging")
    tasks, mapping = _load_tasks(dataset)
    valid_indexes = {int(item["task_index"]) for item in tasks}
    episodes = {item["episode_index"]: item for item in load_episodes(dataset)}
    for episode_index, task in task_changes.items():
        if episode_index not in episodes or episodes[episode_index]["task"] != task:
            raise AnnotationError("发布后 Episode 标注校验失败。")
    for episode_index, annotation in annotation_changes.items():
        if episode_index not in episodes:
            raise AnnotationError("发布后 Episode 扩展标注校验失败。")
        if episodes[episode_index]["annotation"] != annotation:
            raise AnnotationError("发布后 Episode/Subtask 扩展标注校验失败。")
    for path in _parquet_files(staging / "data"):
        table = pq.read_table(path, columns=["task_index"])
        indexes = {int(value) for value in table["task_index"].to_pylist()}
        if not indexes.issubset(valid_indexes):
            raise AnnotationError("发布后的 frame task_index 与任务表不一致。")
    if len(mapping) != len(tasks):
        raise AnnotationError("发布后的任务表包含重复 task_index。")


def publish_annotation(
    *,
    data_root: Path,
    dataset_id: str,
    version_name: str,
    description: str,
    changes: list[EpisodeTaskChange],
    annotations: list[EpisodeAnnotationChange],
) -> dict[str, Any]:
    """创建完整派生副本，并发布 task 与平台扩展标注。"""

    source = resolve_dataset(data_root, dataset_id)
    clean_name = version_name.strip()
    clean_description = description.strip()
    if len(clean_name) < 2:
        raise AnnotationError("版本名称至少需要 2 个字符。")

    episode_changes: dict[int, str] = {}
    for change in changes:
        task = change.task.strip()
        if not task:
            raise AnnotationError("task 不能为空。")
        previous = episode_changes.get(change.episode_index)
        if previous is not None and previous != task:
            raise AnnotationError(f"Episode {change.episode_index} 出现两种不同修改。")
        episode_changes[change.episode_index] = task

    info = _load_info(source)
    episodes = {item["episode_index"]: item for item in load_episodes(source)}
    missing = sorted(set(episode_changes) - set(episodes))
    if missing:
        raise AnnotationError(f"数据集中不存在 Episode {missing[0]}。")
    episode_changes = {
        index: task
        for index, task in episode_changes.items()
        if episodes[index]["task"] != task
    }

    annotation_changes = _normalize_annotation_changes(
        annotations,
        episodes,
        float(info.get("fps") or 0),
    )
    annotation_changes = {
        index: annotation
        for index, annotation in annotation_changes.items()
        if episodes[index]["annotation"] != annotation
    }
    if not episode_changes and not annotation_changes:
        raise AnnotationError("没有实际发生变化的标注，无法发布新版本。")

    data_root.mkdir(parents=True, exist_ok=True)
    source_bytes = _directory_size(source.path)
    if shutil.disk_usage(data_root).free < source_bytes + 64 * 1024 * 1024:
        raise AnnotationError("磁盘剩余空间不足，无法创建完整派生数据集。")

    storage_name = f"ann-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    staging = data_root / "staging" / "annotation" / storage_name
    destination = data_root / "derived" / storage_name
    registered_id: str | None = None
    moved = False

    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_source(source.path, staging)
        if episode_changes:
            current_tasks, _ = _load_tasks(DatasetRef(storage_name, staging, "staging"))
            task_frame, task_indexes = _updated_task_catalog(current_tasks, episode_changes)
            _write_tasks_atomic(task_frame, staging / "meta" / "tasks.parquet")
            _update_episode_files(staging, episode_changes)
            _update_data_files(staging, episode_changes, task_indexes)

            info_path = staging / "meta" / "info.json"
            staged_info = _read_json(info_path)
            staged_info["total_tasks"] = len(task_frame)
            _write_json_atomic(info_path, staged_info)
        if annotation_changes:
            _write_annotation_sidecars(staging, annotation_changes)

        _validate_published_dataset(staging, episode_changes, annotation_changes)
        if destination.exists():
            raise AnnotationError("生成的派生版本编号发生冲突，请重试。")
        staging.replace(destination)
        moved = True
        registered = datasets_service.register_dataset(
            data_root, "derived", storage_name, clean_name, [source.dataset_id]
        )
        registered_id = registered["id"]
        record = {
            "dataset_id": registered_id,
            "storage_name": storage_name,
            "version_name": clean_name,
            "description": clean_description,
            "source_dataset_id": source.dataset_id,
            "source_kind": source.kind,
            "created_at": _utc_now(),
            "changed_episode_count": len(set(episode_changes) | set(annotation_changes)),
            "changed_task_count": len(episode_changes),
            "annotated_episode_count": len(annotation_changes),
            "subtask_segment_count": sum(
                len(annotation["subtasks"]) for annotation in annotation_changes.values()
            ),
            "derived_relative_path": f"derived/{storage_name}",
        }
        _write_json_atomic(
            data_root / "system" / "dataset-versions" / f"{registered_id}.json",
            record,
        )
        return {"ok": True, **record}
    except AnnotationError:
        if registered_id:
            DatasetRepository(data_root).unregister(registered_id)
            (data_root / "system" / "dataset-versions" / f"{registered_id}.json").unlink(
                missing_ok=True
            )
        if moved:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    except (OSError, ValueError, pa.ArrowException) as exc:
        if registered_id:
            DatasetRepository(data_root).unregister(registered_id)
            (data_root / "system" / "dataset-versions" / f"{registered_id}.json").unlink(
                missing_ok=True
            )
        if moved:
            shutil.rmtree(destination, ignore_errors=True)
        raise AnnotationError("派生数据集写入失败，请检查存储空间和文件权限。") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
