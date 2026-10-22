"""Request models for robot registry operations."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RobotWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    robot_type: str = Field(min_length=1, max_length=100)
    model: str = Field(default="", max_length=100)
    serial_number: str = Field(default="", max_length=100)
    status: Literal["active", "maintenance", "inactive"] = "active"
    network_address: str = Field(default="", max_length=255)
    sensors: list[str] = Field(default_factory=list, max_length=50)
    notes: str = Field(default="", max_length=2000)

    @field_validator("name", "robot_type")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("model", "serial_number", "network_address", "notes")
    @classmethod
    def optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("sensors")
    @classmethod
    def sensor_names(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in result:
                continue
            if len(normalized) > 80:
                raise ValueError("单个传感器名称不能超过 80 个字符")
            result.append(normalized)
        return result
