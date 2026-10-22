"""Request models for semantic vocabulary operations."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

DefinitionCategory = Literal["scene", "task", "action", "object"]
DefinitionStatus = Literal["active", "inactive"]


def _examples(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in result:
            continue
        if len(normalized) > 200:
            raise ValueError("单条示例不能超过 200 个字符")
        result.append(normalized)
    return result


class DefinitionCreate(BaseModel):
    category: DefinitionCategory
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    examples: list[str] = Field(default_factory=list, max_length=20)
    status: DefinitionStatus = "active"
    sort_order: int = Field(default=100, ge=0, le=9999)

    @field_validator("code")
    @classmethod
    def normalized_code(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("name")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("description")
    @classmethod
    def optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("examples")
    @classmethod
    def example_values(cls, values: list[str]) -> list[str]:
        return _examples(values)


class DefinitionUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    examples: list[str] = Field(default_factory=list, max_length=20)
    status: DefinitionStatus = "active"
    sort_order: int = Field(default=100, ge=0, le=9999)

    @field_validator("name")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("不能为空")
        return normalized

    @field_validator("description")
    @classmethod
    def optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("examples")
    @classmethod
    def example_values(cls, values: list[str]) -> list[str]:
        return _examples(values)
