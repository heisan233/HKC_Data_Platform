"""数据标注 HTTP API 使用的请求结构。"""

from pydantic import BaseModel, Field


class EpisodeTaskChange(BaseModel):
    """一个 Episode 的 task 修改。"""

    episode_index: int = Field(ge=0)
    task: str = Field(min_length=1, max_length=500)


class PublishAnnotationRequest(BaseModel):
    """把当前标注正式发布成一个完整派生数据集。"""

    dataset_id: str = Field(min_length=1, max_length=128)
    version_name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=1000)
    changes: list[EpisodeTaskChange] = Field(min_length=1, max_length=100_000)
