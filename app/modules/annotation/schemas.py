"""数据标注 HTTP API 使用的请求结构。"""

from typing import Literal

from pydantic import BaseModel, Field


class EpisodeTaskChange(BaseModel):
    """一个 Episode 的 task 修改。"""

    episode_index: int = Field(ge=0)
    task: str = Field(min_length=1, max_length=500)


AnnotationOutcome = Literal["success", "partial", "failure"]


class SubtaskSegment(BaseModel):
    """一个 Episode 内的子任务时间段；时间相对 Episode 开始位置。"""

    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    description: str = Field(min_length=1, max_length=500)
    action_type: str = Field(min_length=1, max_length=100)
    object_name: str = Field(min_length=1, max_length=200)
    outcome: AnnotationOutcome


class EpisodeAnnotationChange(BaseModel):
    """一个 Episode 的执行结果及其完整 Subtask 列表。"""

    episode_index: int = Field(ge=0)
    outcome: AnnotationOutcome
    failure_reason: str = Field(default="", max_length=1000)
    subtasks: list[SubtaskSegment] = Field(default_factory=list, max_length=10_000)


class PublishAnnotationRequest(BaseModel):
    """把当前标注正式发布成一个完整派生数据集。"""

    dataset_id: str = Field(min_length=1, max_length=128)
    version_name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=1000)
    changes: list[EpisodeTaskChange] = Field(default_factory=list, max_length=100_000)
    annotations: list[EpisodeAnnotationChange] = Field(
        default_factory=list,
        max_length=100_000,
    )
