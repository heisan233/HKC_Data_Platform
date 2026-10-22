"""Business operations and accurate LeRobot v3 reference data."""

from pathlib import Path

from app.db.semantics import SemanticDefinitionRepository
from app.modules.semantics.schemas import DefinitionCreate, DefinitionUpdate

CATEGORY_INFO = {
    "scene": {
        "label": "场景分类",
        "description": "采集环境或作业场景，例如实验台、仓储货架。",
        "example": "lab_table",
    },
    "task": {
        "label": "任务类型",
        "description": "对自然语言 task 的上层分类，不替代 Episode task 原文。",
        "example": "pick_and_place",
    },
    "action": {
        "label": "动作类型",
        "description": "Subtask 使用的动作词表，例如抓取、移动、插入。",
        "example": "grasp",
    },
    "object": {
        "label": "对象类别",
        "description": "机器人操作对象的统一类别，例如连接器、料盒。",
        "example": "connector",
    },
}

STATUS_LABELS = {"active": "启用", "inactive": "已停用"}

LEROBOT_STRUCTURE = [
    {
        "level": "数据集",
        "path": "meta/info.json",
        "fields": "codebase_version、robot_type、fps、total_episodes、total_frames、features",
        "meaning": "声明数据集版本、机器人类型、统计量、文件模板和每帧特征。",
    },
    {
        "level": "任务",
        "path": "meta/tasks.parquet",
        "fields": "task（DataFrame 索引）、task_index",
        "meaning": "把自然语言任务映射为帧数据引用的整数索引。",
    },
    {
        "level": "Episode",
        "path": "meta/episodes/chunk-*/file-*.parquet",
        "fields": "episode_index、tasks、length、data/chunk_index、data/file_index",
        "meaning": "描述每段轨迹、帧数、任务，以及对应 frame Parquet 的位置。",
    },
    {
        "level": "帧",
        "path": "data/chunk-*/file-*.parquet",
        "fields": "index、episode_index、frame_index、timestamp、task_index + features",
        "meaning": "保存训练实际读取的逐帧状态、动作和任务索引。",
    },
    {
        "level": "视频",
        "path": "videos/<feature>/chunk-*/file-*.mp4",
        "fields": "由 info.json 的 video_path 与视频特征元数据定位",
        "meaning": "保存相机图像流；视频分片与 Episode 范围由元数据关联。",
    },
    {
        "level": "平台扩展标注",
        "path": "meta/annotations/",
        "fields": "执行结果、失败原因、Subtask 时间段/动作/对象/结果",
        "meaning": "平台旁路扩展，不向 LeRobot v3 核心 features 强塞自定义列。",
    },
]


def list_definitions(
    root: Path,
    category: str | None = None,
    status: str | None = None,
) -> list[dict]:
    records = SemanticDefinitionRepository(root).list(category, status)
    return [
        {
            **record,
            "category_label": CATEGORY_INFO[record["category"]]["label"],
            "status_label": STATUS_LABELS[record["status"]],
        }
        for record in records
    ]


def create_definition(root: Path, payload: DefinitionCreate) -> dict:
    return SemanticDefinitionRepository(root).create(**payload.model_dump())


def update_definition(root: Path, definition_id: str, payload: DefinitionUpdate) -> dict | None:
    return SemanticDefinitionRepository(root).update(definition_id, **payload.model_dump())


def set_definition_status(root: Path, definition_id: str, status: str) -> dict | None:
    return SemanticDefinitionRepository(root).set_status(definition_id, status)
