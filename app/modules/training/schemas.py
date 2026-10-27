"""HTTP payloads for the training interface."""

from pydantic import BaseModel, Field, StrictInt


class PreflightRequest(BaseModel):
    dataset_id: str = Field(pattern=r"^DS\d{6,}$")
    model_id: str = "xvla"


class TrainingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    dataset_id: str = Field(pattern=r"^DS\d{6,}$")
    model_id: str = "xvla"
    preset: str = "standard"
    executor_id: str = "primary"
    runtime_id: str = Field(default="lerobot", pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    parameters: dict[str, StrictInt] | None = None


class RetryRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
