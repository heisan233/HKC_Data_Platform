"""Robot-specific, deterministic checks over LeRobot v3 files."""

from __future__ import annotations

import json
import math
from pathlib import Path

from app.modules.datasets.service import safe_file

MAX_FINDINGS = 200


def _files(folder: Path, pattern: str) -> list[Path]:
    return [file for file in sorted(folder.glob(pattern))
            if file.is_file() and safe_file(folder, file.relative_to(folder).as_posix())]


def _finding(message: str, **evidence) -> dict:
    return {"message": message, "evidence": evidence}


def _result(rule: dict, findings: list[dict], *, reason: str = "") -> dict:
    status = rule["severity"] if findings else "PASS"
    if reason:
        status = "SKIPPED"
    return {"id": rule["id"], "kind": rule["kind"], "name": rule["name"],
            "status": status, "findings": findings[:MAX_FINDINGS],
            "finding_count": len(findings), "reason": reason}


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


def _feature_schema(folder: Path, robot: dict, rule: dict) -> dict:
    import pyarrow.parquet as pq

    info = _info(folder)
    features = info.get("features") if isinstance(info.get("features"), dict) else {}
    findings = []
    for name, dimension in ((robot["state_key"], robot.get("state_dim")),
                            (robot["action_key"], robot.get("action_dim"))):
        item = features.get(name)
        if not isinstance(item, dict):
            findings.append(_finding("缺少机器人字段", field=name, file="meta/info.json"))
        elif dimension is not None and item.get("shape") != [dimension]:
            findings.append(_finding("机器人字段维度不符", field=name,
                                     actual=item.get("shape"), expected=[dimension],
                                     file="meta/info.json"))
    for key in robot["video_keys"]:
        if not isinstance(features.get(key), dict) or features[key].get("dtype") != "video":
            findings.append(_finding("相机字段缺失或类型不是 video", field=key,
                                     file="meta/info.json"))
    paths = _files(folder, "data/**/*.parquet")
    if not paths:
        findings.append(_finding("没有帧数据 Parquet", file="data/"))
    else:
        columns = set(pq.read_schema(paths[0]).names)
        for key in (robot["state_key"], robot["action_key"]):
            if key not in columns:
                findings.append(_finding("帧数据缺少字段", field=key,
                                         file=paths[0].relative_to(folder).as_posix()))
    return _result(rule, findings)


def _capture_fps(folder: Path, robot: dict, rule: dict) -> dict:
    info = _info(folder)
    expected = robot.get("expected_fps")
    if expected is None:
        return _result(rule, [], reason="机器人配置没有预期 FPS")
    tolerance = float(rule["params"]["tolerance_fps"])
    findings = []
    actual = info.get("fps")
    if not isinstance(actual, (int, float)) or abs(actual - expected) > tolerance:
        findings.append(_finding("声明帧率超出机器人允许范围", actual=actual,
                                 expected=expected, tolerance_fps=tolerance,
                                 file="meta/info.json"))
    features = info.get("features") or {}
    for key in robot["video_keys"]:
        video_info = (features.get(key) or {}).get("video_info") or {}
        video_fps = video_info.get("video.fps")
        if video_fps is not None and abs(float(video_fps) - expected) > tolerance:
            findings.append(_finding("相机声明帧率超出允许范围", camera=key,
                                     actual=video_fps, expected=expected,
                                     tolerance_fps=tolerance, file="meta/info.json"))
    return _result(rule, findings)


def _camera_sync(folder: Path, robot: dict, rule: dict) -> dict:
    fps = robot.get("expected_fps") or _info(folder).get("fps")
    if not fps or not robot["video_keys"]:
        return _result(rule, [], reason="缺少 FPS 或相机字段")
    tolerance = float(rule["params"]["max_offset_ms"])
    findings = []
    for episode in _episode_rows(folder):
        episode_id = episode.get("episode_index")
        length = episode.get("num_frames") or episode.get("length")
        if not isinstance(length, (int, float)) or length <= 0:
            findings.append(_finding("Episode 缺少有效帧数", episode=episode_id,
                                     actual=length, file="meta/episodes/"))
            continue
        expected_ms = float(length) / float(fps) * 1000
        starts = []
        ends = []
        for key in robot["video_keys"]:
            start = episode.get(f"videos/{key}/from_timestamp")
            end = episode.get(f"videos/{key}/to_timestamp")
            if start is None or end is None:
                findings.append(_finding("相机缺少 Episode 视频边界", episode=episode_id,
                                         camera=key, file="meta/episodes/"))
                continue
            starts.append(float(start))
            ends.append(float(end))
            deviation = abs((float(end) - float(start)) * 1000 - expected_ms)
            if deviation > tolerance:
                findings.append(_finding("相机视频时长与状态帧不匹配", episode=episode_id,
                                         camera=key, measured_ms=round(deviation, 2),
                                         threshold_ms=tolerance,
                                         from_timestamp=start, to_timestamp=end))
        if starts and (max(starts) - min(starts)) * 1000 > tolerance:
            findings.append(_finding("多个相机片段起点不同步", episode=episode_id,
                                     offset_ms=round((max(starts) - min(starts)) * 1000, 2),
                                     threshold_ms=tolerance))
        if ends and (max(ends) - min(ends)) * 1000 > tolerance:
            findings.append(_finding("多个相机片段终点不同步", episode=episode_id,
                                     offset_ms=round((max(ends) - min(ends)) * 1000, 2),
                                     threshold_ms=tolerance))
    return _result(rule, findings)


def _close_vector(left: list | None, right: list | None, epsilon: float) -> bool:
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
        return False
    return all(isinstance(a, (int, float)) and isinstance(b, (int, float))
               and math.isfinite(a) and math.isfinite(b) and abs(a - b) <= epsilon
               for a, b in zip(left, right))


def _freeze(folder: Path, robot: dict, rule: dict, key: str) -> dict:
    import pyarrow.parquet as pq

    threshold = float(rule["params"]["freeze_seconds"])
    epsilon = float(rule["params"]["epsilon"])
    findings = []
    previous = None
    start = None
    last = None
    current_episode = None

    def finish() -> None:
        if start is not None and last is not None and last - start >= threshold:
            findings.append(_finding("连续数值冻结", episode=current_episode, field=key,
                                     from_timestamp=round(start, 3),
                                     to_timestamp=round(last, 3),
                                     duration_seconds=round(last - start, 3),
                                     threshold_seconds=threshold))

    files = _files(folder, "data/**/*.parquet")
    if not files:
        return _result(rule, [], reason="没有帧数据 Parquet")
    for path in files:
        available = set(pq.read_schema(path).names)
        if not {"episode_index", "timestamp", key}.issubset(available):
            return _result(rule, [], reason=f"{path.name} 缺少冻结检测需要的字段")
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(columns=["episode_index", "timestamp", key], batch_size=4096):
            for row in batch.to_pylist():
                episode = row["episode_index"]
                timestamp = row["timestamp"]
                value = row[key]
                if not isinstance(timestamp, (int, float)):
                    continue
                if episode != current_episode:
                    finish()
                    current_episode, previous, start, last = episode, value, float(timestamp), float(timestamp)
                elif _close_vector(previous, value, epsilon):
                    last = float(timestamp)
                else:
                    finish()
                    previous, start, last = value, float(timestamp), float(timestamp)
    finish()
    return _result(rule, findings)


def _joint_limits(folder: Path, robot: dict, rule: dict) -> dict:
    import pyarrow.parquet as pq

    limits = robot.get("joint_limits") or {}
    if not limits:
        return _result(rule, [], reason="机器人配置未设置关节限位")
    field = robot["state_key"] if rule["params"]["field"] == "state" else robot["action_key"]
    feature = (_info(folder).get("features") or {}).get(field) or {}
    names = (feature.get("names") or {}).get("joints")
    if not isinstance(names, list):
        return _result(rule, [], reason=f"{field} 未提供关节名称")
    indices = {name: index for index, name in enumerate(names) if name in limits}
    if not indices:
        return _result(rule, [], reason="配置中的关节名称未出现在数据字段中")
    findings = []
    files = _files(folder, "data/**/*.parquet")
    if not files:
        return _result(rule, [], reason="没有帧数据 Parquet")
    for path in files:
        available = set(pq.read_schema(path).names)
        if not {field, "episode_index", "timestamp"}.issubset(available):
            return _result(rule, [], reason=f"{path.name} 缺少关节检测需要的字段")
        for batch in pq.ParquetFile(path).iter_batches(
            columns=[field, "episode_index", "timestamp"], batch_size=4096
        ):
            for row in batch.to_pylist():
                vector = row[field]
                if not isinstance(vector, list):
                    continue
                for name, index in indices.items():
                    if index >= len(vector):
                        continue
                    value = vector[index]
                    low, high = limits[name]
                    if isinstance(value, (int, float)) and not low <= value <= high:
                        findings.append(_finding("关节值超出机器人配置范围", field=field,
                                                 joint=name, value=value, min=low, max=high,
                                                 episode=row["episode_index"],
                                                 timestamp=row["timestamp"],
                                                 file=path.relative_to(folder).as_posix()))
                        if len(findings) >= MAX_FINDINGS:
                            return _result(rule, findings)
    return _result(rule, findings)


def _video_quality(folder: Path, robot: dict, rule: dict) -> dict:
    import av
    import numpy as np

    params = rule["params"]
    stride = max(1, int(params["sample_stride"]))
    dark_mean = float(params["dark_mean"])
    bright_mean = float(params["bright_mean"])
    freeze_seconds = float(params["freeze_seconds"])
    findings = []
    if not robot["video_keys"]:
        return _result(rule, [], reason="机器人配置未设置相机字段")
    for key in robot["video_keys"]:
        files = _files(folder, f"videos/{key}/**/*.mp4")
        if not files:
            findings.append(_finding("相机没有可读取的 MP4 文件", camera=key,
                                     directory=f"videos/{key}/"))
        for path in files:
            relative = path.relative_to(folder).as_posix()
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                previous = None
                freeze_start = None
                frozen_reported = False
                sampled = dark = bright = 0
                for index, frame in enumerate(container.decode(stream)):
                    if index % stride:
                        continue
                    timestamp = float(frame.time or index / float(stream.average_rate or 20))
                    gray = frame.to_ndarray(format="gray")[::8, ::8].astype(np.int16)
                    mean = float(gray.mean())
                    sampled += 1
                    dark += mean <= dark_mean
                    bright += mean >= bright_mean
                    if previous is not None and float(np.abs(gray - previous).mean()) < 0.5:
                        if freeze_start is None:
                            freeze_start = timestamp
                        elif not frozen_reported and timestamp - freeze_start >= freeze_seconds:
                            findings.append(_finding("画面连续冻结", camera=key, file=relative,
                                                     from_timestamp=round(freeze_start, 2),
                                                     to_timestamp=round(timestamp, 2),
                                                     threshold_seconds=freeze_seconds))
                            frozen_reported = True
                    else:
                        freeze_start = None
                        frozen_reported = False
                    previous = gray
                if not sampled:
                    findings.append(_finding("视频没有可解码的抽样帧", camera=key,
                                             file=relative))
                if sampled and dark / sampled > 0.02:
                    findings.append(_finding("黑帧比例超过 2%", camera=key, file=relative,
                                             ratio=round(dark / sampled, 4), sampled_frames=sampled))
                if sampled and bright / sampled > 0.02:
                    findings.append(_finding("过曝帧比例超过 2%", camera=key, file=relative,
                                             ratio=round(bright / sampled, 4), sampled_frames=sampled))
    return _result(rule, findings)


def run_robot_rules(folder: Path, snapshot: dict) -> dict:
    robot = snapshot["robot"]
    rows = snapshot["robot_rules"]
    results = []
    for rule in rows:
        if not rule["enabled"]:
            results.append({"id": rule["id"], "kind": rule["kind"], "name": rule["name"],
                            "status": "SKIPPED", "findings": [], "reason": "本次未启用"})
            continue
        try:
            kind = rule["kind"]
            if kind == "feature_schema":
                result = _feature_schema(folder, robot, rule)
            elif kind == "capture_fps":
                result = _capture_fps(folder, robot, rule)
            elif kind == "camera_sync":
                result = _camera_sync(folder, robot, rule)
            elif kind == "state_freeze":
                result = _freeze(folder, robot, rule, robot["state_key"])
            elif kind == "action_freeze":
                result = _freeze(folder, robot, rule, robot["action_key"])
            elif kind == "joint_limits":
                result = _joint_limits(folder, robot, rule)
            else:
                result = _video_quality(folder, robot, rule)
        except Exception as exc:  # noqa: BLE001 - keep one rule failure in its report row
            result = {"id": rule["id"], "kind": rule["kind"], "name": rule["name"],
                      "status": "ERROR", "findings": [], "reason": str(exc)[:1000]}
        results.append(result)
    active = [row["status"] for row in results if row["status"] != "SKIPPED"]
    status = ("FAIL" if "FAIL" in active else "ERROR" if "ERROR" in active else
              "WARN" if "WARN" in active else "PASS" if active else "SKIPPED")
    return {"status": status, "results": results}
