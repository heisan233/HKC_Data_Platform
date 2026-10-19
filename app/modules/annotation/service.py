"""LeRobot v3 Episode/task 标注与完整派生版本发布。

本模块只操作 ``settings.data_root`` 下的数据。原始数据集和既有派生版本均只读；
正式发布时先复制到 staging，完成 Parquet/JSON 修改和校验后，再原子移动到
``storage/derived``。
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from app.modules.annotation.schemas import EpisodeTaskChange

DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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
    """只允许通过直接子目录编号访问 raw/derived 中的数据集。"""

    _validate_dataset_id(dataset_id)
    for kind in ("raw", "derived"):
        candidate = data_root / kind / dataset_id
        if candidate.is_dir() and (candidate / "meta" / "info.json").is_file():
            return DatasetRef(dataset_id=dataset_id, path=candidate, kind=kind)
    raise DatasetNotFoundError(f"未找到数据集 {dataset_id}。")


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


def _load_info(dataset: DatasetRef) -> dict[str, Any]:
    info = _read_json(dataset.path / "meta" / "info.json")
    if info.get("codebase_version") != "v3.0":
        raise AnnotationError("当前标注工作台只支持 LeRobot v3.0 数据集。")
    return info


def _load_tasks(dataset: DatasetRef) -> tuple[list[dict[str, Any]], dict[int, str]]:
    path = dataset.path / "meta" / "tasks.parquet"
    if not path.is_file():
        raise AnnotationError("数据集缺少 meta/tasks.parquet。")
    table = pq.read_table(path)
    if not {"task_index", "task"}.issubset(table.column_names):
        raise AnnotationError("tasks.parquet 缺少 task_index 或 task 列。")

    tasks: list[dict[str, Any]] = []
    mapping: dict[int, str] = {}
    for item in table.select(["task_index", "task"]).to_pylist():
        index = int(item["task_index"])
        task = str(item["task"])
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


def load_episodes(dataset: DatasetRef) -> list[dict[str, Any]]:
    info = _load_info(dataset)
    _, task_names = _load_tasks(dataset)
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

        num_frames = int(row.get("num_frames") or 0)
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

        episodes.append(
            {
                "episode_index": episode_index,
                "task": str(task_value or ""),
                "num_frames": num_frames,
                "duration_seconds": round(num_frames / fps, 3) if fps else None,
                "videos": videos,
            }
        )
    episodes.sort(key=lambda item: item["episode_index"])
    return episodes


def list_datasets(data_root: Path) -> list[dict[str, Any]]:
    names = _record_names(data_root)
    datasets: list[dict[str, Any]] = []
    for kind in ("raw", "derived"):
        root = data_root / kind
        if not root.exists():
            continue
        for path in sorted(root.iterdir(), reverse=True):
            if not path.is_dir() or not (path / "meta" / "info.json").is_file():
                continue
            dataset = DatasetRef(path.name, path, kind)
            try:
                info = _load_info(dataset)
            except AnnotationError:
                continue
            datasets.append(
                {
                    "dataset_id": path.name,
                    "dataset_name": names.get(path.name, path.name),
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
) -> tuple[pa.Table, dict[str, int]]:
    by_text = {str(item["task"]): int(item["task_index"]) for item in current}
    next_index = max(by_text.values(), default=-1) + 1
    for text in changes.values():
        if text not in by_text:
            by_text[text] = next_index
            next_index += 1
    ordered = sorted(by_text.items(), key=lambda item: item[1])
    table = pa.table(
        {
            "task_index": pa.array([index for _, index in ordered], type=pa.int64()),
            "task": pa.array([text for text, _ in ordered], type=pa.string()),
        }
    )
    return table, by_text


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


def _validate_published_dataset(staging: Path, changes: dict[int, str]) -> None:
    dataset = DatasetRef("staging", staging, "staging")
    tasks, mapping = _load_tasks(dataset)
    valid_indexes = {int(item["task_index"]) for item in tasks}
    episodes = {item["episode_index"]: item for item in load_episodes(dataset)}
    for episode_index, task in changes.items():
        if episode_index not in episodes or episodes[episode_index]["task"] != task:
            raise AnnotationError("发布后 Episode 标注校验失败。")
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
) -> dict[str, Any]:
    """创建完整派生副本，并在副本内发布 Episode/task 修改。"""

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

    episodes = {item["episode_index"]: item for item in load_episodes(source)}
    missing = sorted(set(episode_changes) - set(episodes))
    if missing:
        raise AnnotationError(f"数据集中不存在 Episode {missing[0]}。")
    episode_changes = {
        index: task
        for index, task in episode_changes.items()
        if episodes[index]["task"] != task
    }
    if not episode_changes:
        raise AnnotationError("没有实际发生变化的 task，无法发布新版本。")

    data_root.mkdir(parents=True, exist_ok=True)
    source_bytes = _directory_size(source.path)
    if shutil.disk_usage(data_root).free < source_bytes + 64 * 1024 * 1024:
        raise AnnotationError("磁盘剩余空间不足，无法创建完整派生数据集。")

    version_id = f"ann-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    staging = data_root / "staging" / "annotation" / version_id
    destination = data_root / "derived" / version_id
    record = {
        "dataset_id": version_id,
        "version_name": clean_name,
        "description": clean_description,
        "source_dataset_id": source.dataset_id,
        "source_kind": source.kind,
        "created_at": _utc_now(),
        "changed_episode_count": len(episode_changes),
        "derived_relative_path": f"derived/{version_id}",
    }

    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_source(source.path, staging)
        current_tasks, _ = _load_tasks(DatasetRef(version_id, staging, "staging"))
        task_table, task_indexes = _updated_task_catalog(current_tasks, episode_changes)
        _write_parquet_atomic(task_table, staging / "meta" / "tasks.parquet")
        _update_episode_files(staging, episode_changes)
        _update_data_files(staging, episode_changes, task_indexes)

        info_path = staging / "meta" / "info.json"
        info = _read_json(info_path)
        info["total_tasks"] = task_table.num_rows
        _write_json_atomic(info_path, info)
        _validate_published_dataset(staging, episode_changes)
        if destination.exists():
            raise AnnotationError("生成的派生版本编号发生冲突，请重试。")
        staging.replace(destination)
        _write_json_atomic(
            data_root / "system" / "dataset-versions" / f"{version_id}.json",
            record,
        )
        return {"ok": True, **record}
    except AnnotationError:
        raise
    except (OSError, pa.ArrowException) as exc:
        raise AnnotationError("派生数据集写入失败，请检查存储空间和文件权限。") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
