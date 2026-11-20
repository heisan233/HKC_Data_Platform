"""Validated LaWAM requests sent from the platform to its host executor."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LaWAMCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    dataset_id: str = Field(pattern=r"^DS[0-9]{6,}$")
    base: str = Field(min_length=1, max_length=512)
    dataset_mode: Literal["all", "goal", "custom"]
    episodes: str = Field(default="", max_length=10000)
    steps: int = Field(ge=1, le=200000)
    batch: int = Field(ge=1, le=64)
    action_horizon: int = Field(ge=1, le=128)
    warmup: int = Field(ge=0, le=199999)
    lr: float = Field(gt=0, le=1)
    unfreeze_llm_layers: int = Field(ge=0, le=128)
    save_freq: int = Field(ge=1, le=200000)
    freeze_vision: bool
    freeze_llm: bool
    stop_vllm: bool

    @field_validator("episodes")
    @classmethod
    def normalize_episode_separators(cls, value: str) -> str:
        return value.strip().replace("，", ",").replace("、", ",")


class LaWAMEvaluation(BaseModel):
    train_id: str = Field(pattern=r"^WAM[0-9A-F]{12}$")
    checkpoint: str = Field(min_length=1, max_length=512)
    task: Literal["libero_object", "libero_spatial", "libero_goal", "libero_10"]
    task_id: int = Field(ge=0, le=99)
    episodes: int = Field(ge=1, le=100)
    replan: int = Field(ge=1, le=128)
    task_text: Literal["auto", "none"]
