"""Deterministic checks over actual LeRobot v3 Parquet rows and video streams."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from app.modules.datasets.service import safe_file

MAX_FINDINGS = 200


@dataclass
class Findings:
    rows: list[dict] = field(default_factory=list)
    count: int = 0

    def add(self, message: str, **evidence) -> None:
        self.count += 1
        if len(self.rows) < MAX_FINDINGS:
            self.rows.append({"message": message, "evidence": evidence})


def _files(folder: Path, pattern: str) -> list[Path]:
    return [file for file in sorted(folder.glob(pattern))
            if file.is_file() and safe_file(folder, file.relative_to(folder).as_posix())]


def _info(folder: Path) -> dict:
    path = safe_file(folder, "meta/info.json")
    if path is None:
        raise ValueError("meta/info.json 不可读取")
    return json.loads(path.read_text(encoding="utf-8"))


def _episode_rows(folder: Path) -> list[dict]:
    import pyarrow.parquet as pq

    paths = _files(folder, "meta/episodes/**/*.parquet")
    if not paths:
        raise ValueError("meta/episodes/ 中没有 Parquet")
    rows = []
    for path in paths:
        rows.extend(pq.read_table(path).to_pylist())
    return rows


def _coverage(*, files: int = 0, rows: int = 0, episodes: int = 0,
              videos: int = 0, frames: int = 0, sampled: bool = False) -> dict:
    return {"files": files, "rows": rows, "episodes": episodes,
            "videos": videos, "frames": frames, "sampled": sampled}


def _result(rule: dict, findings: Findings, *, standard: dict | None = None,
            declared: dict | None = None, measured: dict | None = None,
            coverage: dict | None = None, reason: str = "") -> dict:
    status = "SKIPPED" if reason else rule["severity"] if findings.count else "PASS"
    return {"id": rule["id"], "kind": rule["kind"], "name": rule["name"],
            "status": status, "findings": findings.rows, "finding_count": findings.count,
            "reason": reason, "standard": standard or {}, "declared": declared or {},
            "measured": measured or {}, "coverage": coverage or _coverage()}


def _arrow_dtype(data_type) -> str:
    import pyarrow as pa

    while pa.types.is_list(data_type) or pa.types.is_large_list(data_type) or pa.types.is_fixed_size_list(data_type):
        data_type = data_type.value_type
    aliases = {"halffloat": "float16", "float": "float32", "double": "float64"}
    return aliases.get(str(data_type), str(data_type))


def _vector(value) -> list | None:
    if isinstance(value, list):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [value]
    return None


def _feature_dimension(folder: Path, rule: dict) -> dict:
    import pyarrow.parquet as pq

    params = rule["params"]
    name = params["field_name"]
    expected_dtype = params["expected_dtype"]
    expected_dimension = int(params["expected_dimension"])
    info = _info(folder)
    feature = (info.get("features") or {}).get(name)
    declared = feature if isinstance(feature, dict) else {}
    findings = Findings()
    if not declared:
        findings.add("info.json 未声明字段", field=name, file="meta/info.json")
    else:
        if declared.get("dtype") != expected_dtype:
            findings.add("声明类型与方案标准不一致", field=name,
                         expected=expected_dtype, actual=declared.get("dtype"), file="meta/info.json")
        if declared.get("shape") != [expected_dimension]:
            findings.add("声明维度与方案标准不一致", field=name,
                         expected=[expected_dimension], actual=declared.get("shape"),
                         file="meta/info.json")
    paths = _files(folder, "data/**/*.parquet")
    episodes = set()
    checked_rows = invalid_rows = 0
    actual_types = set()
    if not paths:
        findings.add("没有帧数据 Parquet", directory="data/")
    for path in paths:
        schema = pq.read_schema(path)
        if name not in schema.names:
            findings.add("Parquet 缺少字段", field=name,
                         file=path.relative_to(folder).as_posix())
            continue
        actual_type = _arrow_dtype(schema.field(name).type)
        actual_types.add(actual_type)
        if actual_type != expected_dtype:
            findings.add("Parquet 实际类型与方案标准不一致", field=name,
                         expected=expected_dtype, actual=actual_type,
                         file=path.relative_to(folder).as_posix())
        columns = [name]
        for optional in ("episode_index", "frame_index"):
            if optional in schema.names:
                columns.append(optional)
        for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=4096):
            for row in batch.to_pylist():
                checked_rows += 1
                episode = row.get("episode_index")
                if episode is not None:
                    episodes.add(episode)
                value = _vector(row.get(name))
                if value is None or len(value) != expected_dimension:
                    invalid_rows += 1
                    findings.add("实际帧向量维度不符", field=name, episode=episode,
                                 frame_index=row.get("frame_index"),
                                 expected=expected_dimension,
                                 actual=None if value is None else len(value),
                                 file=path.relative_to(folder).as_posix())
    return _result(rule, findings,
                   standard={"field": name, "dtype": expected_dtype,
                             "dimension": expected_dimension},
                   declared={"dtype": declared.get("dtype"), "shape": declared.get("shape")},
                   measured={"dtypes": sorted(actual_types), "valid_rows": checked_rows - invalid_rows,
                             "invalid_rows": invalid_rows},
                   coverage=_coverage(files=len(paths), rows=checked_rows, episodes=len(episodes)))


def _frame_rate(folder: Path, rule: dict) -> dict:
    import av
    import pyarrow.parquet as pq

    params = rule["params"]
    timestamp_key = params["timestamp_field"]
    expected = float(params["expected_fps"])
    tolerance = float(params["tolerance_fps"])
    max_gap = float(params["max_gap_ms"]) / 1000
    max_gap_ratio = float(params["max_gap_ratio"])
    findings = Findings()
    info = _info(folder)
    declared_fps = info.get("fps")
    if not isinstance(declared_fps, (int, float)) or abs(float(declared_fps) - expected) > tolerance:
        findings.add("声明 FPS 超出方案范围", expected=expected, tolerance=tolerance,
                     actual=declared_fps, file="meta/info.json")
    paths = _files(folder, "data/**/*.parquet")
    episodes: dict[object, dict] = {}
    checked_rows = gap_count = interval_count = 0
    if not paths:
        findings.add("没有帧数据 Parquet", directory="data/")
    for path in paths:
        schema = pq.read_schema(path)
        if timestamp_key not in schema.names or "episode_index" not in schema.names:
            findings.add("Parquet 缺少帧率检查字段", timestamp_field=timestamp_key,
                         file=path.relative_to(folder).as_posix())
            continue
        for batch in pq.ParquetFile(path).iter_batches(
            columns=["episode_index", timestamp_key], batch_size=4096
        ):
            for row in batch.to_pylist():
                checked_rows += 1
                episode = row["episode_index"]
                timestamp = row[timestamp_key]
                if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                    findings.add("时间戳不是有限数值", episode=episode, actual=timestamp,
                                 file=path.relative_to(folder).as_posix())
                    continue
                state = episodes.setdefault(episode, {"first": float(timestamp),
                                                       "last": float(timestamp), "count": 0})
                if state["count"]:
                    delta = float(timestamp) - state["last"]
                    interval_count += 1
                    if delta <= 0:
                        findings.add("时间戳重复或倒退", episode=episode, previous=state["last"],
                                     actual=timestamp, file=path.relative_to(folder).as_posix())
                    elif delta > max_gap:
                        gap_count += 1
                state["last"] = float(timestamp)
                state["count"] += 1
    measured_episode_fps = []
    for episode, state in episodes.items():
        duration = state["last"] - state["first"]
        if state["count"] > 1 and duration > 0:
            actual = (state["count"] - 1) / duration
            measured_episode_fps.append(actual)
            if abs(actual - expected) > tolerance:
                findings.add("Episode 实际 FPS 超出方案范围", episode=episode,
                             expected=expected, tolerance=tolerance, actual=round(actual, 4))
    ratio = gap_count / interval_count if interval_count else 0.0
    if ratio > max_gap_ratio:
        findings.add("异常帧间隔比例超过阈值", actual=round(ratio, 6),
                     expected_max=max_gap_ratio, abnormal_intervals=gap_count,
                     total_intervals=interval_count)
    video_rates = []
    video_files = []
    decoded_frames = 0
    for key in params["video_keys"]:
        paths_for_key = _files(folder, f"videos/{key}/**/*.mp4")
        if not paths_for_key:
            findings.add("视频字段没有可读取的 MP4 文件", camera=key,
                         directory=f"videos/{key}/")
        for path in paths_for_key:
            video_files.append(path)
            try:
                with av.open(str(path)) as container:
                    stream = container.streams.video[0]
                    first = last = None
                    count = 0
                    for index, frame in enumerate(container.decode(stream)):
                        timestamp = float(frame.time if frame.time is not None else
                                          index / float(stream.average_rate or expected))
                        first = timestamp if first is None else first
                        last = timestamp
                        count += 1
                    decoded_frames += count
                    if count > 1 and last is not None and first is not None and last > first:
                        actual = (count - 1) / (last - first)
                        video_rates.append(actual)
                        if abs(actual - expected) > tolerance:
                            findings.add("视频实际 FPS 超出方案范围", camera=key,
                                         expected=expected, tolerance=tolerance,
                                         actual=round(actual, 4),
                                         file=path.relative_to(folder).as_posix())
            except Exception as exc:  # noqa: BLE001 - one bad media file is a finding
                findings.add("视频无法解码以测量 FPS", camera=key,
                             file=path.relative_to(folder).as_posix(), error=str(exc)[:300])
    return _result(rule, findings,
                   standard={"expected_fps": expected, "tolerance_fps": tolerance,
                             "max_gap_ms": params["max_gap_ms"],
                             "max_gap_ratio": max_gap_ratio},
                   declared={"fps": declared_fps},
                   measured={"episode_fps_min": round(min(measured_episode_fps), 4) if measured_episode_fps else None,
                             "episode_fps_max": round(max(measured_episode_fps), 4) if measured_episode_fps else None,
                             "video_fps_min": round(min(video_rates), 4) if video_rates else None,
                             "video_fps_max": round(max(video_rates), 4) if video_rates else None,
                             "abnormal_gap_ratio": round(ratio, 6)},
                   coverage=_coverage(files=len(paths), rows=checked_rows,
                                      episodes=len(episodes), videos=len(video_files),
                                      frames=decoded_frames))


def _timeline(folder: Path, timestamp_key: str) -> tuple[dict, int, int]:
    import pyarrow.parquet as pq

    episodes = {}
    rows = 0
    paths = _files(folder, "data/**/*.parquet")
    for path in paths:
        schema = pq.read_schema(path)
        if not {"episode_index", timestamp_key}.issubset(schema.names):
            continue
        for batch in pq.ParquetFile(path).iter_batches(
            columns=["episode_index", timestamp_key], batch_size=4096
        ):
            for row in batch.to_pylist():
                timestamp = row[timestamp_key]
                if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                    continue
                rows += 1
                state = episodes.setdefault(row["episode_index"], {
                    "first": float(timestamp), "last": float(timestamp), "count": 0,
                })
                state["first"] = min(state["first"], float(timestamp))
                state["last"] = max(state["last"], float(timestamp))
                state["count"] += 1
    return episodes, len(paths), rows


def _video_duration(path: Path) -> float | None:
    import av

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
        if container.duration is not None:
            return float(container.duration / av.time_base)
    return None


def _episode_video_path(folder: Path, episode: dict, key: str) -> Path | None:
    prefix = f"videos/{key}"
    chunk = episode.get(f"{prefix}/chunk_index")
    file_index = episode.get(f"{prefix}/file_index")
    if not isinstance(chunk, int) or not isinstance(file_index, int):
        return None
    return safe_file(folder, f"videos/{key}/chunk-{chunk:03d}/file-{file_index:03d}.mp4")


def _temporal_alignment(folder: Path, rule: dict) -> dict:
    params = rule["params"]
    expected_fps = float(params["expected_fps"])
    tolerance = float(params["max_offset_ms"])
    timeline, file_count, row_count = _timeline(folder, params["timestamp_field"])
    findings = Findings()
    if not timeline:
        findings.add("没有可用于对齐的实际帧时间轴", field=params["timestamp_field"])
    metadata = _episode_rows(folder)
    info = _info(folder)
    duration_cache = {}
    checked_videos = set()
    measured_offsets = []
    for episode in metadata:
        episode_id = episode.get("episode_index")
        frame_state = timeline.get(episode_id)
        if frame_state is None:
            findings.add("Episode 缺少实际帧时间轴", episode=episode_id)
            continue
        reference_duration = ((frame_state["last"] - frame_state["first"])
                              + 1 / expected_fps) * 1000
        starts = []
        ends = []
        for key in params["video_keys"]:
            start = episode.get(f"videos/{key}/from_timestamp")
            end = episode.get(f"videos/{key}/to_timestamp")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                findings.add("Episode 缺少视频边界", episode=episode_id, camera=key)
                continue
            starts.append(float(start))
            ends.append(float(end))
            offset = abs((float(end) - float(start)) * 1000 - reference_duration)
            measured_offsets.append(offset)
            if offset > tolerance:
                findings.add("视频时长与实际帧时间轴不一致", episode=episode_id, camera=key,
                             expected_ms=round(reference_duration, 3),
                             actual_ms=round((float(end) - float(start)) * 1000, 3),
                             offset_ms=round(offset, 3), threshold_ms=tolerance)
            video = _episode_video_path(folder, episode, key)
            if video is None:
                findings.add("Episode 对应视频文件不存在", episode=episode_id, camera=key)
                continue
            checked_videos.add(video)
            if video not in duration_cache:
                try:
                    duration_cache[video] = _video_duration(video)
                except Exception as exc:  # noqa: BLE001
                    duration_cache[video] = None
                    findings.add("视频无法解码以核对实际时长", episode=episode_id, camera=key,
                                 file=video.relative_to(folder).as_posix(), error=str(exc)[:300])
            actual_duration = duration_cache[video]
            if actual_duration is not None and float(end) * 1000 - actual_duration * 1000 > tolerance:
                findings.add("Episode 视频边界超过 MP4 实际时长", episode=episode_id,
                             camera=key, to_timestamp=end,
                             video_duration=round(actual_duration, 3), threshold_ms=tolerance)
        if starts and (max(starts) - min(starts)) * 1000 > tolerance:
            findings.add("多个相机片段起点不同步", episode=episode_id,
                         offset_ms=round((max(starts) - min(starts)) * 1000, 3),
                         threshold_ms=tolerance)
        if ends and (max(ends) - min(ends)) * 1000 > tolerance:
            findings.add("多个相机片段终点不同步", episode=episode_id,
                         offset_ms=round((max(ends) - min(ends)) * 1000, 3),
                         threshold_ms=tolerance)
    return _result(rule, findings,
                   standard={"timestamp_field": params["timestamp_field"],
                             "video_keys": params["video_keys"],
                             "expected_fps": expected_fps, "max_offset_ms": tolerance},
                   declared={"fps": info.get("fps"), "episode_rows": len(metadata),
                             "video_keys": params["video_keys"]},
                   measured={"max_duration_offset_ms": round(max(measured_offsets), 3)
                             if measured_offsets else None},
                   coverage=_coverage(files=file_count, rows=row_count,
                                      episodes=len(metadata), videos=len(checked_videos)))


def _numeric_validity(folder: Path, rule: dict) -> dict:
    import pyarrow.parquet as pq

    params = rule["params"]
    name = params["field_name"]
    declared = (_info(folder).get("features") or {}).get(name) or {}
    findings = Findings()
    paths = _files(folder, "data/**/*.parquet")
    episodes = set()
    checked_rows = invalid_rows = 0
    previous = {}
    if not paths:
        findings.add("没有帧数据 Parquet", directory="data/")
    for path in paths:
        schema = pq.read_schema(path)
        if name not in schema.names:
            findings.add("Parquet 缺少数值字段", field=name,
                         file=path.relative_to(folder).as_posix())
            continue
        columns = [name] + [key for key in ("episode_index", "frame_index") if key in schema.names]
        for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=4096):
            for row in batch.to_pylist():
                checked_rows += 1
                episode = row.get("episode_index")
                episodes.add(episode)
                values = _vector(row.get(name))
                bad = False
                if values is None:
                    bad = True
                    findings.add("数值字段为空或不是数值向量", field=name, episode=episode,
                                 frame_index=row.get("frame_index"),
                                 file=path.relative_to(folder).as_posix())
                else:
                    for index, value in enumerate(values):
                        if not isinstance(value, (int, float)) or not math.isfinite(value):
                            bad = True
                            findings.add("字段包含非有限数值", field=name, dimension=index,
                                         episode=episode, frame_index=row.get("frame_index"),
                                         value=value, file=path.relative_to(folder).as_posix())
                            continue
                        if params["min_value"] is not None and value < params["min_value"]:
                            bad = True
                            findings.add("字段数值低于方案下限", field=name, dimension=index,
                                         episode=episode, value=value, minimum=params["min_value"])
                        if params["max_value"] is not None and value > params["max_value"]:
                            bad = True
                            findings.add("字段数值高于方案上限", field=name, dimension=index,
                                         episode=episode, value=value, maximum=params["max_value"])
                    prior = previous.get(episode)
                    if params["max_delta"] is not None and prior is not None and len(prior) == len(values):
                        for index, (left, right) in enumerate(zip(prior, values)):
                            if all(isinstance(item, (int, float)) and math.isfinite(item)
                                   for item in (left, right)) and abs(right - left) > params["max_delta"]:
                                bad = True
                                findings.add("相邻帧数值突变超过阈值", field=name, dimension=index,
                                             episode=episode, delta=abs(right - left),
                                             threshold=params["max_delta"])
                    previous[episode] = values
                invalid_rows += bad
    return _result(rule, findings, standard=params, declared=declared,
                   measured={"valid_rows": checked_rows - invalid_rows,
                             "invalid_rows": invalid_rows},
                   coverage=_coverage(files=len(paths), rows=checked_rows, episodes=len(episodes)))


def _close_vector(left: list | None, right: list | None, epsilon: float) -> bool:
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
        return False
    return all(isinstance(a, (int, float)) and isinstance(b, (int, float))
               and math.isfinite(a) and math.isfinite(b) and abs(a - b) <= epsilon
               for a, b in zip(left, right))


def _numeric_freeze(folder: Path, rule: dict) -> dict:
    import pyarrow.parquet as pq

    params = rule["params"]
    name = params["field_name"]
    declared = (_info(folder).get("features") or {}).get(name) or {}
    epsilon = float(params["epsilon"])
    threshold = float(params["freeze_seconds"])
    findings = Findings()
    paths = _files(folder, "data/**/*.parquet")
    checked_rows = 0
    episodes = set()
    current_episode = previous = start = last = None
    if not paths:
        findings.add("没有帧数据 Parquet", directory="data/")

    def finish() -> None:
        if start is not None and last is not None and last - start >= threshold:
            findings.add("连续数值冻结", field=name, episode=current_episode,
                         from_timestamp=round(start, 3), to_timestamp=round(last, 3),
                         duration_seconds=round(last - start, 3), threshold_seconds=threshold)

    for path in paths:
        schema = pq.read_schema(path)
        required = {"episode_index", "timestamp", name}
        if not required.issubset(schema.names):
            findings.add("Parquet 缺少冻结检查字段", field=name,
                         file=path.relative_to(folder).as_posix())
            continue
        for batch in pq.ParquetFile(path).iter_batches(columns=list(required), batch_size=4096):
            for row in batch.to_pylist():
                checked_rows += 1
                episode = row["episode_index"]
                episodes.add(episode)
                timestamp = row["timestamp"]
                value = _vector(row[name])
                if not isinstance(timestamp, (int, float)) or value is None:
                    continue
                timestamp = float(timestamp)
                if episode != current_episode:
                    finish()
                    current_episode, previous, start, last = episode, value, timestamp, timestamp
                elif _close_vector(previous, value, epsilon):
                    last = timestamp
                    previous = value
                else:
                    finish()
                    previous, start, last = value, timestamp, timestamp
    finish()
    return _result(rule, findings, standard=params, declared=declared,
                   measured={"freeze_count": findings.count},
                   coverage=_coverage(files=len(paths), rows=checked_rows, episodes=len(episodes)))


def _video_quality(folder: Path, rule: dict) -> dict:
    import av
    import numpy as np

    params = rule["params"]
    key = params["video_key"]
    findings = Findings()
    info = _info(folder)
    declared = (info.get("features") or {}).get(key) or {}
    paths = _files(folder, f"videos/{key}/**/*.mp4")
    if not paths:
        findings.add("视频字段没有可读取的 MP4 文件", camera=key,
                     directory=f"videos/{key}/")
    sampled_total = dark_total = bright_total = decoded_total = 0
    sizes = set()
    for path in paths:
        try:
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                width, height = stream.width, stream.height
                sizes.add((width, height))
                if params["expected_width"] is not None and width != params["expected_width"]:
                    findings.add("视频实际宽度与方案标准不一致", camera=key,
                                 expected=params["expected_width"], actual=width,
                                 file=path.relative_to(folder).as_posix())
                if params["expected_height"] is not None and height != params["expected_height"]:
                    findings.add("视频实际高度与方案标准不一致", camera=key,
                                 expected=params["expected_height"], actual=height,
                                 file=path.relative_to(folder).as_posix())
                previous = None
                previous_timestamp = None
                freeze_start = None
                freeze_reported = False
                next_sample = 0.0
                sampled = dark = bright = decoded = 0
                for index, frame in enumerate(container.decode(stream)):
                    decoded += 1
                    timestamp = float(frame.time if frame.time is not None else
                                      index / float(stream.average_rate or 20))
                    if timestamp + 1e-9 < next_sample:
                        continue
                    next_sample = timestamp + float(params["sample_interval_ms"]) / 1000
                    gray = frame.to_ndarray(format="gray")[::8, ::8].astype(np.int16)
                    mean = float(gray.mean())
                    sampled += 1
                    dark += mean <= params["dark_mean"]
                    bright += mean >= params["bright_mean"]
                    if previous is not None and float(np.abs(gray - previous).mean()) < params["frame_diff_threshold"]:
                        freeze_start = previous_timestamp if freeze_start is None else freeze_start
                        if not freeze_reported and timestamp - freeze_start >= params["freeze_seconds"]:
                            findings.add("画面连续冻结", camera=key,
                                         file=path.relative_to(folder).as_posix(),
                                         from_timestamp=round(freeze_start, 3),
                                         to_timestamp=round(timestamp, 3),
                                         threshold_seconds=params["freeze_seconds"])
                            freeze_reported = True
                    else:
                        freeze_start = None
                        freeze_reported = False
                    previous = gray
                    previous_timestamp = timestamp
                sampled_total += sampled
                dark_total += dark
                bright_total += bright
                decoded_total += decoded
                if not decoded or not sampled:
                    findings.add("视频没有可解码的抽样帧", camera=key,
                                 file=path.relative_to(folder).as_posix())
                if sampled and dark / sampled > params["max_dark_ratio"]:
                    findings.add("黑暗帧比例超过方案阈值", camera=key,
                                 file=path.relative_to(folder).as_posix(),
                                 actual=round(dark / sampled, 6),
                                 threshold=params["max_dark_ratio"])
                if sampled and bright / sampled > params["max_bright_ratio"]:
                    findings.add("过曝帧比例超过方案阈值", camera=key,
                                 file=path.relative_to(folder).as_posix(),
                                 actual=round(bright / sampled, 6),
                                 threshold=params["max_bright_ratio"])
        except Exception as exc:  # noqa: BLE001 - report corrupt media without aborting other files
            findings.add("视频无法解码", camera=key,
                         file=path.relative_to(folder).as_posix(), error=str(exc)[:300])
    return _result(rule, findings, standard=params, declared=declared,
                   measured={"sizes": [list(item) for item in sorted(sizes)],
                             "dark_ratio": round(dark_total / sampled_total, 6) if sampled_total else None,
                             "bright_ratio": round(bright_total / sampled_total, 6) if sampled_total else None,
                             "sampled_frames": sampled_total},
                   coverage=_coverage(videos=len(paths), frames=decoded_total, sampled=True))


RUNNERS = {
    "feature_dimension": _feature_dimension,
    "frame_rate": _frame_rate,
    "temporal_alignment": _temporal_alignment,
    "numeric_validity": _numeric_validity,
    "numeric_freeze": _numeric_freeze,
    "video_quality": _video_quality,
}


def run_robot_rules(folder: Path, snapshot: dict) -> dict:
    results = []
    for rule in snapshot["robot_rules"]:
        if not rule["enabled"]:
            results.append({"id": rule["id"], "kind": rule["kind"], "name": rule["name"],
                            "status": "SKIPPED", "findings": [], "finding_count": 0,
                            "reason": "本次未启用", "standard": rule["params"],
                            "declared": {}, "measured": {}, "coverage": _coverage()})
            continue
        try:
            result = RUNNERS[rule["kind"]](folder, rule)
        except Exception as exc:  # noqa: BLE001 - keep one failed rule visible in the report
            result = {"id": rule["id"], "kind": rule["kind"], "name": rule["name"],
                      "status": "ERROR", "findings": [], "finding_count": 0,
                      "reason": str(exc)[:1000], "standard": rule.get("params", {}),
                      "declared": {}, "measured": {}, "coverage": _coverage()}
        results.append(result)
    active = [row["status"] for row in results if row["status"] != "SKIPPED"]
    status = ("FAIL" if "FAIL" in active else "ERROR" if "ERROR" in active else
              "WARN" if "WARN" in active else "PASS" if active else "SKIPPED")
    return {"status": status, "results": results}
