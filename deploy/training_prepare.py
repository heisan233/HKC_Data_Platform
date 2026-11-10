"""Validate and normalize a LeRobot task table inside the training image."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def prepare_tasks(source: Path, target: Path) -> None:
    tasks = pd.read_parquet(source)
    if ("task_index" not in tasks.columns or
            tasks["task_index"].tolist() != list(range(len(tasks)))):
        raise ValueError("数据集 task_index 必须与任务表行序一致并从 0 连续编号")
    if "task" in tasks.columns:
        if tasks["task"].isna().any() or not tasks["task"].map(
            lambda value: isinstance(value, str) and bool(value.strip())
        ).all():
            raise ValueError("数据集任务文本为空或格式无效")
        tasks = tasks.set_index("task")
    elif tasks.index.name != "task" or not all(
        isinstance(value, str) and value.strip() for value in tasks.index
    ):
        raise ValueError("数据集任务表缺少可用的 task 文本")
    tasks.to_parquet(target)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("用法：training_prepare.py SOURCE TARGET")
    prepare_tasks(Path(sys.argv[1]), Path(sys.argv[2]))
