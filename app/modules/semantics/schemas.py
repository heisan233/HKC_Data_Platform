"""Request models for the dataset schema editor."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

FieldScope = Literal["episode", "frame"]
FieldDtype = Literal["string", "int64", "float64", "bool", "float32", "int32"]

FIELD_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _field_name(value: str) -> str:
    normalized = value.strip()
    if not FIELD_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("字段名只能包含英文字母、数字、下划线和点，且不能以数字开头")
    return normalized


class SchemaFieldCreateRequest(BaseModel):
    """Create one Episode field or one frame feature in a new Derived DS."""

    scope: FieldScope
    name: str = Field(min_length=1, max_length=200)
    dtype: FieldDtype
    shape: list[int] = Field(default_factory=lambda: [1], min_length=1, max_length=4)
    names: list[str] | None = Field(default=None, max_length=4096)
    description: str = Field(default="", max_length=1000)
    nullable: bool = True
    value: Any = None
    version_name: str = Field(min_length=2, max_length=120)
    version_description: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _field_name(value)

    @field_validator("shape")
    @classmethod
    def validate_shape(cls, value: list[int]) -> list[int]:
        if any(size < 1 or size > 16_384 for size in value):
            raise ValueError("shape 的每一维必须在 1–16384 之间")
        product = 1
        for size in value:
            product *= size
        if product > 65_536:
            raise ValueError("第一版单个数值 Feature 最多包含 65536 个元素")
        return value

    @field_validator("description", "version_description")
    @classmethod
    def strip_optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("version_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("派生版本名称不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_scope(self) -> SchemaFieldCreateRequest:
        if self.scope == "episode":
            if self.dtype not in {"string", "int64", "float64", "bool"}:
                raise ValueError("Episode 字段只支持 string、int64、float64、bool")
            if self.shape != [1]:
                raise ValueError("Episode 字段第一版只支持标量 shape=[1]")
            if self.names:
                raise ValueError("Episode 标量字段不需要 names")
        else:
            if self.dtype not in {"int32", "int64", "float32", "float64", "bool"}:
                raise ValueError("逐帧 Feature 第一版只支持数值或 bool 类型")
            expected = 1
            for size in self.shape:
                expected *= size
            if self.names is not None:
                names = [name.strip() for name in self.names]
                if any(not name for name in names) or len(set(names)) != len(names):
                    raise ValueError("names 不能为空或重复")
                if len(names) != expected:
                    raise ValueError("names 数量必须等于 shape 的元素总数")
                self.names = names
            if self.value is not None:
                raise ValueError("逐帧 Feature 请保持为空，或使用 CSV/Parquet 导入接口")
            if not self.nullable:
                raise ValueError("新增空逐帧 Feature 必须允许空值")
        return self


class SchemaFieldDeleteRequest(BaseModel):
    """Delete one field from a new Derived DS."""

    scope: FieldScope
    name: str = Field(min_length=1, max_length=200)
    confirm_official: bool = False
    version_name: str = Field(min_length=2, max_length=120)
    version_description: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _field_name(value)

    @field_validator("version_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("派生版本名称不能为空")
        return normalized

    @field_validator("version_description")
    @classmethod
    def strip_optional_text(cls, value: str) -> str:
        return value.strip()
