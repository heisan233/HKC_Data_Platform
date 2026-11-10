"""Request models for robot-side collection clients."""

from pydantic import BaseModel, Field


class UploadPreflight(BaseModel):
    archive_sha256: str = Field(min_length=64, max_length=64)
    archive_size: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=255)
    archive_robot_type: str | None = Field(default=None, max_length=100)
    robot_type_confirmation: str | None = Field(default=None, max_length=100)


class RebindDuplicate(BaseModel):
    archive_sha256: str = Field(min_length=64, max_length=64)
    dataset_id: str = Field(pattern=r"^DS[0-9]+$")
    confirm: bool
