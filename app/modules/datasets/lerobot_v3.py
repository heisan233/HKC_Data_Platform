"""Shared readers for official LeRobot v3 task metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def parquet_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.parquet") if path.is_file())


def load_tasks(folder: Path) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Read the official task-index DataFrame and the legacy column layout."""

    path = folder / "meta" / "tasks.parquet"
    if not path.is_file():
        raise ValueError("数据集缺少 meta/tasks.parquet。")
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError, pa.ArrowException) as exc:
        raise ValueError("无法读取 meta/tasks.parquet。") from exc
    if "task_index" not in frame.columns:
        raise ValueError("tasks.parquet 缺少 task_index 列。")
    if isinstance(frame.index, pd.RangeIndex):
        if "task" not in frame.columns:
            raise ValueError("tasks.parquet 缺少 task 索引或兼容 task 列。")
        frame = frame.set_index("task")
    frame.index.name = "task"
    if frame.index.hasnans or not frame.index.is_unique:
        raise ValueError("tasks.parquet 的 task 索引为空或重复。")
    if frame["task_index"].isna().any() or not frame["task_index"].is_unique:
        raise ValueError("tasks.parquet 的 task_index 为空或重复。")

    tasks: list[dict[str, Any]] = []
    mapping: dict[int, str] = {}
    for task_value, index_value in zip(frame.index, frame["task_index"], strict=True):
        index = int(index_value)
        task = str(task_value).strip()
        if not task:
            raise ValueError("tasks.parquet 包含空 task。")
        if index in mapping:
            raise ValueError("tasks.parquet 包含重复 task_index。")
        mapping[index] = task
        tasks.append({"task_index": index, "task": task})
    tasks.sort(key=lambda item: item["task_index"])
    return tasks, mapping


def episode_task_indexes(folder: Path) -> dict[int, list[int]]:
    """Collect ordered task indexes per Episode from frame Parquet files."""

    result: dict[int, list[int]] = {}
    try:
        for path in parquet_files(folder / "data"):
            schema = pq.read_schema(path)
            if not {"episode_index", "task_index"}.issubset(schema.names):
                continue
            table = pq.read_table(path, columns=["episode_index", "task_index"])
            for row in table.to_pylist():
                episode_index = int(row["episode_index"])
                task_index = int(row["task_index"])
                values = result.setdefault(episode_index, [])
                if task_index not in values:
                    values.append(task_index)
    except (OSError, pa.ArrowException, TypeError, ValueError) as exc:
        raise ValueError("无法从 frame Parquet 读取 Episode 任务索引。") from exc
    return result


def episode_task_text(
    episode: dict[str, Any],
    task_names: dict[int, str],
    frame_task_indexes: dict[int, list[int]],
) -> str:
    """Resolve one Episode task, preferring official v3 Episode metadata."""

    direct = episode.get("task")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    values = episode.get("tasks")
    if isinstance(values, (list, tuple)):
        names: list[str] = []
        for value in values:
            if isinstance(value, str) and value.strip():
                name = value.strip()
            else:
                try:
                    name = task_names.get(int(value), "")
                except (TypeError, ValueError):
                    name = ""
            if name and name not in names:
                names.append(name)
        if names:
            return "；".join(names)

    try:
        episode_index = int(episode["episode_index"])
    except (KeyError, TypeError, ValueError):
        return ""
    names = [
        task_names[index]
        for index in frame_task_indexes.get(episode_index, [])
        if index in task_names
    ]
    return "；".join(dict.fromkeys(names))
