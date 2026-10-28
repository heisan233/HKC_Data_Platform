"""Request models for robot registry operations."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RobotWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    robot_type: str = Field(min_length=1, max_length=100)
    serial_number: str = Field(default="", max_length=100)
    network_address: str = Field(default="", max_length=255)
    notes: str = Field(default="", max_length=2000)

    @field_validator("name", "robot_type")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("serial_number", "network_address", "notes")
    @classmethod
    def optional_text(cls, value: str) -> str:
        return value.strip()


class DatasetBindingWrite(BaseModel):
    dataset_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("dataset_ids")
    @classmethod
    def stable_dataset_ids(cls, values: list[str]) -> list[str]:
        result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not result or any(
            not value.startswith("DS") or not value[2:].isdigit() for value in result
        ):
            raise ValueError("数据集编号格式无效")
        return result
