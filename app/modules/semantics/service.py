"""LeRobot v3 schema inspection and immutable schema-derived datasets."""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.db.datasets import DatasetRepository
from app.modules.datasets import service as datasets_service
from app.modules.semantics.schemas import SchemaFieldCreateRequest, SchemaFieldDeleteRequest

SCHEMA_EXTENSION_VERSION = 1
CORE_FRAME_FIELDS = frozenset(
    {"index", "episode_index", "frame_index", "timestamp", "task_index"}
)
CORE_EPISODE_FIELDS = frozenset(
    {
        "episode_index",
        "dataset_from_index",
        "dataset_to_index",
        "length",
        "num_frames",
        "task",
        "tasks",
        "data/chunk_index",
        "data/file_index",
        "meta/episodes/chunk_index",
        "meta/episodes/file_index",
    }
)
DTYPES: dict[str, pa.DataType] = {
    "string": pa.string(),
    "int32": pa.int32(),
    "int64": pa.int64(),
    "float32": pa.float32(),
    "float64": pa.float64(),
    "bool": pa.bool_(),
}


class SchemaEditorError(Exception):
    """An error safe to return from the schema editor API."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parquet_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.parquet") if path.is_file())


def _read_info(folder: Path) -> dict[str, Any]:
    info, error = datasets_service.dataset_info(folder)
    if error or info is None:
        raise SchemaEditorError(error or "无法读取 meta/info.json")
    return info


def _resolve(data_root: Path, dataset_id: str) -> tuple[dict, Path]:
    try:
        return datasets_service.resolve_registered_dataset(data_root, dataset_id)
    except ValueError as exc:
        raise SchemaEditorError(str(exc)) from exc


def list_datasets(data_root: Path) -> list[dict[str, Any]]:
    """List registered LeRobot v3 datasets without changing their files."""

    rows: list[dict[str, Any]] = []
    for record in DatasetRepository(data_root).list():
        try:
            _, folder = _resolve(data_root, record["id"])
            info = _read_info(folder)
        except SchemaEditorError:
            continue
        rows.append(
            {
                "dataset_id": record["id"],
                "dataset_name": record["name"],
                "storage_area": record["storage_area"],
                "robot_type": record["robot_type"],
                "total_episodes": int(info.get("total_episodes") or 0),
                "total_frames": int(info.get("total_frames") or 0),
            }
        )
    return rows


def _consistent_schema(files: list[Path], label: str) -> pa.Schema:
    if not files:
        raise SchemaEditorError(f"数据集缺少 {label} Parquet。")
    try:
        first = pq.read_schema(files[0])
        for path in files[1:]:
            if not pq.read_schema(path).equals(first, check_metadata=False):
                raise SchemaEditorError(f"{label} Parquet 分片结构不一致，不能安全修改。")
        return first
    except SchemaEditorError:
        raise
    except (OSError, pa.ArrowException) as exc:
        raise SchemaEditorError(f"无法读取 {label} Parquet 结构。") from exc


def _field_counts(files: list[Path], names: list[str]) -> dict[str, tuple[int, int]]:
    """Count nulls with one Parquet read per shard instead of one read per field."""

    totals = {name: 0 for name in names}
    non_null = {name: 0 for name in names}
    try:
        for path in files:
            parquet = pq.ParquetFile(path)
            rows = parquet.metadata.num_rows
            available = [name for name in names if name in parquet.schema_arrow.names]
            for name in names:
                totals[name] += rows
            if not available:
                continue
            table = pq.read_table(path, columns=available)
            for name in available:
                column = table[name]
                non_null[name] += len(column) - column.null_count
    except (OSError, pa.ArrowException) as exc:
        raise SchemaEditorError("无法统计 Parquet 字段完整率。") from exc
    return {name: (totals[name], non_null[name]) for name in names}


def _extension(info: dict[str, Any]) -> dict[str, Any]:
    value = info.get("hkc_extensions")
    return value if isinstance(value, dict) else {}


def _field_row(
    *,
    name: str,
    scope: str,
    arrow_field: pa.Field | None,
    definition: dict[str, Any] | None,
    extension_definition: dict[str, Any] | None,
    counts: tuple[int, int],
) -> dict[str, Any]:
    is_media = scope == "frame" and (definition or {}).get("dtype") in {"image", "video"}
    if is_media:
        total, non_null, completeness = 0, 0, 100.0
    else:
        total, non_null = counts
        completeness = round(non_null / total * 100, 2) if total else 0.0
    is_custom = extension_definition is not None
    is_core = name in (CORE_EPISODE_FIELDS if scope == "episode" else CORE_FRAME_FIELDS)
    if scope == "episode" and name.startswith(("videos/", "data/")):
        is_core = True
    state = "external_media" if is_media else (
        "complete" if total and non_null == total else "declared_empty" if non_null == 0 else "partial"
    )
    if arrow_field is None and not is_media:
        state = "missing_column"
    feature_state = extension_definition or {}
    if is_media:
        deletion = "unsupported"
    elif is_core:
        deletion = "blocked"
    elif is_custom:
        deletion = "direct"
    else:
        deletion = "warning"
    return {
        "name": name,
        "scope": scope,
        "dtype": (definition or {}).get("dtype") or feature_state.get("dtype") or (
            str(arrow_field.type) if arrow_field else "未声明"
        ),
        "shape": (definition or {}).get("shape") or feature_state.get("shape") or [1],
        "names": (definition or {}).get("names"),
        "arrow_type": (
            str(arrow_field.type) if arrow_field else "外部媒体文件" if is_media else "缺少物理列"
        ),
        "nullable": bool(feature_state.get("nullable", arrow_field.nullable if arrow_field else True)),
        "description": str(feature_state.get("description") or ""),
        "origin": "platform" if is_custom else "official",
        "core": is_core,
        "deletion": deletion,
        "state": state,
        "completeness": completeness,
        "non_null": non_null,
        "total": total,
    }


def inspect_schema(data_root: Path, dataset_id: str) -> dict[str, Any]:
    """Inspect declared and physical schema across every Parquet shard."""

    record, folder = _resolve(data_root, dataset_id)
    info = _read_info(folder)
    episode_files = _parquet_files(folder / "meta" / "episodes")
    frame_files = _parquet_files(folder / "data")
    episode_schema = _consistent_schema(episode_files, "Episode")
    frame_schema = _consistent_schema(frame_files, "frame")
    extension = _extension(info)
    episode_definitions = extension.get("episode_features")
    episode_definitions = episode_definitions if isinstance(episode_definitions, dict) else {}
    frame_states = extension.get("frame_feature_states")
    frame_states = frame_states if isinstance(frame_states, dict) else {}
    features = info.get("features")
    features = features if isinstance(features, dict) else {}

    episode_names = list(dict.fromkeys([*episode_schema.names, *episode_definitions.keys()]))
    frame_names = list(dict.fromkeys([*features.keys(), *frame_schema.names, *frame_states.keys()]))
    episode_counts = _field_counts(episode_files, episode_names)
    frame_counts = _field_counts(frame_files, frame_names)
    episode_rows = [
        _field_row(
            name=name,
            scope="episode",
            arrow_field=episode_schema.field(name) if name in episode_schema.names else None,
            definition=None,
            extension_definition=episode_definitions.get(name),
            counts=episode_counts[name],
        )
        for name in episode_names
    ]
    frame_rows = [
        _field_row(
            name=name,
            scope="frame",
            arrow_field=frame_schema.field(name) if name in frame_schema.names else None,
            definition=features.get(name) if isinstance(features.get(name), dict) else None,
            extension_definition=frame_states.get(name),
            counts=frame_counts[name],
        )
        for name in frame_names
    ]
    training_blockers = [
        row["name"]
        for row in frame_rows
        if row["state"] not in {"complete", "external_media"} and row["name"] in features
    ]
    return {
        "dataset": {
            "dataset_id": record["id"],
            "dataset_name": record["name"],
            "storage_area": record["storage_area"],
            "robot_type": record["robot_type"],
            "total_episodes": int(info.get("total_episodes") or 0),
            "total_frames": int(info.get("total_frames") or 0),
            "codebase_version": info.get("codebase_version"),
        },
        "episode_fields": episode_rows,
        "frame_features": frame_rows,
        "status": {
            "structure_extended": bool(episode_definitions or frame_states),
            "training_ready": not training_blockers,
            "training_blockers": training_blockers,
            "episode_shards": len(episode_files),
            "frame_shards": len(frame_files),
        },
    }


def _arrow_type(dtype: str, shape: list[int], *, scope: str) -> pa.DataType:
    scalar = DTYPES[dtype]
    if scope == "episode" or math.prod(shape) == 1:
        return scalar
    return pa.list_(scalar, math.prod(shape))


def _episode_value(dtype: str, value: Any) -> Any:
    if value is None:
        return None
    try:
        if dtype == "string":
            return str(value)
        if dtype in {"int32", "int64"}:
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if dtype in {"float32", "float64"}:
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if dtype == "bool":
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
    except (TypeError, ValueError) as exc:
        raise SchemaEditorError(f"默认值无法转换为 {dtype}。") from exc
    raise SchemaEditorError(f"默认值无法转换为 {dtype}。")


def _write_table(path: Path, table: pa.Table) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        pq.write_table(table, temporary, compression="snappy")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_column(path: Path, name: str, arrow_type: pa.DataType, value: Any = None) -> None:
    try:
        table = pq.read_table(path)
        if name in table.column_names:
            raise SchemaEditorError(f"字段 {name} 已经存在。")
        values = pa.array([value] * table.num_rows, type=arrow_type)
        _write_table(path, table.append_column(pa.field(name, arrow_type, nullable=True), values))
    except SchemaEditorError:
        raise
    except (OSError, pa.ArrowException, TypeError, ValueError) as exc:
        raise SchemaEditorError(f"写入字段 {name} 失败。") from exc


def _drop_column(path: Path, name: str) -> None:
    try:
        table = pq.read_table(path)
        if name not in table.column_names:
            raise SchemaEditorError(f"物理 Parquet 中不存在字段 {name}。")
        _write_table(path, table.drop([name]))
    except SchemaEditorError:
        raise
    except (OSError, pa.ArrowException) as exc:
        raise SchemaEditorError(f"删除字段 {name} 失败。") from exc


def _write_info(folder: Path, info: dict[str, Any]) -> None:
    path = folder / "meta" / "info.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _ensure_extension(info: dict[str, Any]) -> dict[str, Any]:
    extension = info.get("hkc_extensions")
    if not isinstance(extension, dict):
        extension = {}
        info["hkc_extensions"] = extension
    extension["schema_version"] = SCHEMA_EXTENSION_VERSION
    episode_features = extension.get("episode_features")
    if not isinstance(episode_features, dict):
        extension["episode_features"] = {}
    frame_states = extension.get("frame_feature_states")
    if not isinstance(frame_states, dict):
        extension["frame_feature_states"] = {}
    return extension


def _add_field(folder: Path, payload: SchemaFieldCreateRequest) -> None:
    info = _read_info(folder)
    extension = _ensure_extension(info)
    if payload.scope == "episode":
        files = _parquet_files(folder / "meta" / "episodes")
        schema = _consistent_schema(files, "Episode")
        if payload.name in schema.names or payload.name in extension["episode_features"]:
            raise SchemaEditorError(f"Episode 字段 {payload.name} 已存在。")
        value = _episode_value(payload.dtype, payload.value)
        if value is None and not payload.nullable:
            raise SchemaEditorError("nullable=false 时必须填写统一值。")
        for path in files:
            _append_column(path, payload.name, _arrow_type(payload.dtype, [1], scope="episode"), value)
        extension["episode_features"][payload.name] = {
            "dtype": payload.dtype,
            "shape": [1],
            "nullable": payload.nullable,
            "description": payload.description,
            "state": "declared_empty" if value is None else "complete",
            "source": "uniform_value" if value is not None else "placeholder",
        }
    else:
        files = _parquet_files(folder / "data")
        schema = _consistent_schema(files, "frame")
        features = info.get("features")
        if not isinstance(features, dict):
            raise SchemaEditorError("info.json.features 不是有效对象。")
        if payload.name in schema.names or payload.name in features:
            raise SchemaEditorError(f"逐帧 Feature {payload.name} 已存在。")
        arrow_type = _arrow_type(payload.dtype, payload.shape, scope="frame")
        for path in files:
            _append_column(path, payload.name, arrow_type)
        features[payload.name] = {
            "dtype": payload.dtype,
            "shape": payload.shape,
            "names": payload.names,
        }
        extension["frame_feature_states"][payload.name] = {
            "dtype": payload.dtype,
            "shape": payload.shape,
            "nullable": True,
            "description": payload.description,
            "state": "declared_empty",
            "completeness": 0,
            "source": "placeholder",
        }
    _write_info(folder, info)


def _remove_stats(folder: Path, field_name: str) -> None:
    path = folder / "meta" / "stats.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict) and field_name in data:
        data.pop(field_name, None)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def _write_numeric_stats(folder: Path, field_name: str, values: list[Any], width: int) -> None:
    """Write LeRobot-style statistics for the non-null imported rows."""

    if not values:
        return
    array = np.asarray(values, dtype=np.float64).reshape(len(values), width)
    path = folder / "meta" / "stats.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[field_name] = {
        "min": array.min(axis=0).tolist(),
        "max": array.max(axis=0).tolist(),
        "mean": array.mean(axis=0).tolist(),
        "std": array.std(axis=0).tolist(),
        "count": [len(values)],
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _delete_field(folder: Path, payload: SchemaFieldDeleteRequest) -> None:
    info = _read_info(folder)
    extension = _ensure_extension(info)
    if payload.scope == "episode":
        if payload.name in CORE_EPISODE_FIELDS or payload.name.startswith(("data/", "videos/")):
            raise SchemaEditorError("这是 Episode 核心字段，平台禁止删除。")
        custom = payload.name in extension["episode_features"]
        if not custom and not payload.confirm_official:
            raise SchemaEditorError("删除非平台字段需要明确确认官方字段警告。")
        files = _parquet_files(folder / "meta" / "episodes")
        _consistent_schema(files, "Episode")
        for path in files:
            _drop_column(path, payload.name)
        extension["episode_features"].pop(payload.name, None)
    else:
        if payload.name in CORE_FRAME_FIELDS:
            raise SchemaEditorError("这是 frame 核心索引字段，平台禁止删除。")
        features = info.get("features")
        features = features if isinstance(features, dict) else {}
        custom = payload.name in extension["frame_feature_states"]
        if not custom and not payload.confirm_official:
            raise SchemaEditorError("删除官方 Feature 需要明确确认警告。")
        files = _parquet_files(folder / "data")
        _consistent_schema(files, "frame")
        for path in files:
            _drop_column(path, payload.name)
        features.pop(payload.name, None)
        extension["frame_feature_states"].pop(payload.name, None)
        _remove_stats(folder, payload.name)
    _write_info(folder, info)


def _storage_name() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"schema-{timestamp}-{uuid4().hex[:8]}"


def _write_version_record(
    data_root: Path,
    result: dict[str, Any],
    source_id: str,
    version_name: str,
    description: str,
    operation: str,
) -> None:
    path = data_root / "system" / "dataset-versions" / f"{result['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "dataset_id": result["id"],
                "version_name": version_name,
                "description": description,
                "operation": operation,
                "source_dataset_ids": [source_id],
                "created_at": _utc_now(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _publish(
    data_root: Path,
    dataset_id: str,
    version_name: str,
    description: str,
    operation: str,
    modifier: Callable[[Path], None],
) -> dict[str, Any]:
    """Copy, mutate in staging, validate, move and register one full Derived DS."""

    _, source = _resolve(data_root, dataset_id)
    transaction = uuid4().hex
    staging_root = data_root / "staging" / "schema" / transaction
    staged = staging_root / "dataset"
    final: Path | None = None
    registered: dict[str, Any] | None = None
    try:
        staging_root.mkdir(parents=True, exist_ok=False)
        shutil.copytree(source, staged)
        modifier(staged)
        # Re-open every shard and the JSON before publication.
        _read_info(staged)
        _consistent_schema(_parquet_files(staged / "meta" / "episodes"), "Episode")
        _consistent_schema(_parquet_files(staged / "data"), "frame")
        name = _storage_name()
        derived_root = data_root / "derived"
        derived_root.mkdir(parents=True, exist_ok=True)
        final = derived_root / name
        staged.replace(final)
        registered = datasets_service.register_dataset(
            data_root,
            "derived",
            name,
            version_name,
            [dataset_id],
        )
        _write_version_record(
            data_root,
            registered,
            dataset_id,
            version_name,
            description,
            operation,
        )
        return {
            "dataset_id": registered["id"],
            "dataset_name": registered["name"],
            "storage_area": "derived",
            "source_dataset_id": dataset_id,
        }
    except SchemaEditorError:
        if registered:
            DatasetRepository(data_root).unregister(registered["id"])
        if final:
            shutil.rmtree(final, ignore_errors=True)
        raise
    except Exception as exc:
        if registered:
            DatasetRepository(data_root).unregister(registered["id"])
        if final:
            shutil.rmtree(final, ignore_errors=True)
        raise SchemaEditorError("创建数据定义派生版本失败，源数据集未修改。") from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def create_field(
    data_root: Path,
    dataset_id: str,
    payload: SchemaFieldCreateRequest,
) -> dict[str, Any]:
    return _publish(
        data_root,
        dataset_id,
        payload.version_name,
        payload.version_description,
        f"schema_add_{payload.scope}",
        lambda folder: _add_field(folder, payload),
    )


def delete_field(
    data_root: Path,
    dataset_id: str,
    payload: SchemaFieldDeleteRequest,
) -> dict[str, Any]:
    return _publish(
        data_root,
        dataset_id,
        payload.version_name,
        payload.version_description,
        f"schema_delete_{payload.scope}",
        lambda folder: _delete_field(folder, payload),
    )


def _read_import(path: Path) -> pd.DataFrame:
    try:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path)
        if path.suffix.lower() in {".parquet", ".pq"}:
            return pd.read_parquet(path)
    except (OSError, ValueError, pa.ArrowException) as exc:
        raise SchemaEditorError("无法读取导入文件。") from exc
    raise SchemaEditorError("只支持 .csv、.parquet 或 .pq 导入文件。")


def _import_frame_values(
    folder: Path,
    *,
    field_name: str,
    dtype: str,
    shape: list[int],
    names: list[str] | None,
    description: str,
    import_path: Path,
    source_columns: list[str],
) -> None:
    info = _read_info(folder)
    extension = _ensure_extension(info)
    features = info.get("features")
    if not isinstance(features, dict):
        raise SchemaEditorError("info.json.features 不是有效对象。")
    files = _parquet_files(folder / "data")
    schema = _consistent_schema(files, "frame")
    if field_name in schema.names or field_name in features:
        raise SchemaEditorError(f"逐帧 Feature {field_name} 已存在。")
    expected = math.prod(shape)
    if len(source_columns) != expected:
        raise SchemaEditorError("导入列数量必须等于 shape 的元素总数。")
    if len(set(source_columns)) != len(source_columns):
        raise SchemaEditorError("导入列名不能重复。")
    frame = _read_import(import_path)
    required = {"episode_index", "frame_index", *source_columns}
    missing = required - set(frame.columns)
    if missing:
        raise SchemaEditorError(f"导入文件缺少列：{', '.join(sorted(missing))}")
    if frame[["episode_index", "frame_index"]].isna().any().any():
        raise SchemaEditorError("导入文件的 episode_index/frame_index 不能为空。")
    frame["episode_index"] = frame["episode_index"].astype("int64")
    frame["frame_index"] = frame["frame_index"].astype("int64")
    if frame.duplicated(["episode_index", "frame_index"]).any():
        raise SchemaEditorError("导入文件包含重复的 episode_index + frame_index。")
    mapping: dict[tuple[int, int], Any] = {}
    for row in frame.to_dict(orient="records"):
        key = (int(row["episode_index"]), int(row["frame_index"]))
        values = [row[column] for column in source_columns]
        if all(pd.isna(value) for value in values):
            mapping[key] = None
        elif any(pd.isna(value) for value in values):
            raise SchemaEditorError(f"{key} 的向量只有部分元素为空。")
        else:
            mapping[key] = values[0] if expected == 1 else values
    arrow_type = _arrow_type(dtype, shape, scope="frame")
    used: set[tuple[int, int]] = set()
    non_null = 0
    total = 0
    for path in files:
        table = pq.read_table(path)
        keys = list(
            zip(
                (int(value) for value in table["episode_index"].to_pylist()),
                (int(value) for value in table["frame_index"].to_pylist()),
                strict=True,
            )
        )
        values = [mapping.get(key) for key in keys]
        used.update(key for key in keys if key in mapping)
        non_null += sum(value is not None for value in values)
        total += len(values)
        try:
            array = pa.array(values, type=arrow_type)
        except (pa.ArrowException, TypeError, ValueError) as exc:
            raise SchemaEditorError(f"导入值无法转换为 {dtype} / shape={shape}。") from exc
        _write_table(path, table.append_column(pa.field(field_name, arrow_type), array))
    unknown = set(mapping) - used
    if unknown:
        first = min(unknown)
        raise SchemaEditorError(
            f"导入文件包含数据集中不存在的帧：episode_index={first[0]}, frame_index={first[1]}。"
        )
    used_values = [mapping[key] for key in used if mapping[key] is not None]
    _write_numeric_stats(folder, field_name, used_values, expected)
    features[field_name] = {"dtype": dtype, "shape": shape, "names": names}
    completeness = round(non_null / total * 100, 2) if total else 0
    extension["frame_feature_states"][field_name] = {
        "dtype": dtype,
        "shape": shape,
        "nullable": True,
        "description": description,
        "state": "complete" if non_null == total else "declared_empty" if not non_null else "partial",
        "completeness": completeness,
        "source": "csv_or_parquet_import",
        "source_columns": source_columns,
    }
    _write_info(folder, info)


def import_frame_feature(
    data_root: Path,
    dataset_id: str,
    *,
    field_name: str,
    dtype: str,
    shape: list[int],
    names: list[str] | None,
    description: str,
    version_name: str,
    version_description: str,
    import_path: Path,
    source_columns: list[str],
) -> dict[str, Any]:
    if dtype not in {"int32", "int64", "float32", "float64", "bool"}:
        raise SchemaEditorError("逐帧 Feature 类型无效。")
    if not shape or len(shape) > 4 or any(value < 1 for value in shape):
        raise SchemaEditorError("shape 无效。")
    return _publish(
        data_root,
        dataset_id,
        version_name.strip(),
        version_description.strip(),
        "schema_import_frame",
        lambda folder: _import_frame_values(
            folder,
            field_name=field_name,
            dtype=dtype,
            shape=shape,
            names=names,
            description=description.strip(),
            import_path=import_path,
            source_columns=source_columns,
        ),
    )
