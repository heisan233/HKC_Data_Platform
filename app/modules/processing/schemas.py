"""数据处理 API 请求结构。"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ProcessingOperation = Literal["filter", "split", "merge", "reorder", "deduplicate"]
SplitValue = float | list[int]


class ProcessingJobRequest(BaseModel):
    """创建一次不可变派生处理任务。"""

    operation: ProcessingOperation
    source_dataset_ids: list[str] = Field(min_length=1, max_length=32)
    version_name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=1000)
    episode_indices: list[int] = Field(default_factory=list, max_length=100_000)
    splits: dict[str, SplitValue] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def validate_operation_arguments(self) -> "ProcessingJobRequest":
        self.source_dataset_ids = list(dict.fromkeys(self.source_dataset_ids))
        if any(index < 0 for index in self.episode_indices):
            raise ValueError("Episode 编号不能为负数。")
        if len(self.episode_indices) != len(set(self.episode_indices)):
            raise ValueError("Episode 编号不能重复。")

        single_source = {"filter", "split", "reorder", "deduplicate"}
        if self.operation in single_source and len(self.source_dataset_ids) != 1:
            raise ValueError("该操作必须且只能选择一个源数据集。")
        if self.operation == "merge" and len(self.source_dataset_ids) < 2:
            raise ValueError("合并操作至少需要两个源数据集。")
        if self.operation in {"filter", "reorder"} and not self.episode_indices:
            raise ValueError("该操作必须提供 Episode 编号。")
        if self.operation == "split" and not self.splits:
            raise ValueError("拆分操作必须提供至少一个分组。")
        return self
