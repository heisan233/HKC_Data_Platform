"""Supported quality checks, editable robot-rule schemas, and family templates."""

from __future__ import annotations

from copy import deepcopy

from app.modules.datasets.service import robot_type_label

DOCTOR_CHECKS = [
    ("metadata", "元数据与格式"), ("temporal", "时序一致性"),
    ("actions", "动作质量"), ("videos", "视频完整性"),
    ("statistics", "统计分布"), ("episodes", "Episode 健康度"),
    ("consistency", "字段一致性"), ("training", "训练就绪性"),
    ("anomalies", "异常检测"), ("portability", "可移植性"),
    ("per_episode", "逐 Episode 诊断"),
]
DOCTOR_IDS = {item[0] for item in DOCTOR_CHECKS}
DOCTOR_REPORT_NAMES = {
    "Metadata & Format Compliance": "元数据与格式合规", "Temporal Consistency": "时序一致性",
    "Action Quality": "动作质量", "Video Integrity": "视频完整性",
    "Data Distribution": "数据分布", "Episode Health": "Episode 健康度",
    "Feature Consistency": "字段一致性", "Training Readiness": "训练就绪性",
    "Anomaly Detection": "异常检测", "Portability": "数据可移植性",
    "Per-Episode Summary": "逐 Episode 诊断",
}


def _number(label: str, *, minimum: float = 0, maximum: float = 1e9,
            step: str = "any", optional: bool = False) -> dict:
    return {"label": label, "type": "number", "min": minimum, "max": maximum,
            "step": step, "optional": optional}


def _text(label: str, *, options: list[str] | None = None) -> dict:
    result = {"label": label, "type": "select" if options else "text"}
    if options:
        result["options"] = options
    return result


ROBOT_RULE_TYPES = {
    "feature_dimension": {
        "label": "字段、类型与实际维度",
        "default_severity": "FAIL",
        "description": "比对方案标准、info.json 声明、全部 Parquet Schema 和每帧实际向量。",
        "params": {"field_name": "observation.state", "expected_dtype": "float32",
                   "expected_dimension": 1},
        "fields": {
            "field_name": _text("检查字段"),
            "expected_dtype": _text("标准类型", options=[
                "float16", "float32", "float64", "int8", "int16", "int32", "int64",
                "uint8", "uint16", "uint32", "uint64", "bool",
            ]),
            "expected_dimension": _number("标准维度", minimum=1, maximum=100000, step="1"),
        },
    },
    "frame_rate": {
        "label": "时间戳完整性与实际 FPS",
        "default_severity": "FAIL",
        "description": "检查声明 FPS、Parquet 时间戳、视频 PTS、重复时间戳和异常间隔。",
        "params": {"timestamp_field": "timestamp", "video_keys": [], "expected_fps": 30.0,
                   "tolerance_fps": 0.5, "max_gap_ms": 100.0, "max_gap_ratio": 0.01},
        "fields": {
            "timestamp_field": _text("时间戳字段"),
            "video_keys": {"label": "视频字段（每行一个）", "type": "lines"},
            "expected_fps": _number("标准 FPS", minimum=0.1, maximum=500),
            "tolerance_fps": _number("允许偏差（FPS）", maximum=500),
            "max_gap_ms": _number("最大相邻帧间隔（ms）", maximum=600000),
            "max_gap_ratio": _number("最大异常间隔比例", maximum=1),
        },
    },
    "temporal_alignment": {
        "label": "多模态实际时长对齐",
        "default_severity": "FAIL",
        "description": "比对帧时间轴、Episode 视频边界和 MP4 实际覆盖时长。",
        "params": {"timestamp_field": "timestamp", "video_keys": [], "expected_fps": 30.0,
                   "max_offset_ms": 100.0},
        "fields": {
            "timestamp_field": _text("参考时间戳字段"),
            "video_keys": {"label": "参与对齐的视频字段（每行一个）", "type": "lines"},
            "expected_fps": _number("标准 FPS", minimum=0.1, maximum=500),
            "max_offset_ms": _number("最大允许偏差（ms）", maximum=600000),
        },
    },
    "numeric_validity": {
        "label": "数值有效性、范围与突变",
        "default_severity": "WARN",
        "description": "检查实际数值中的空值、NaN、无穷、统一范围和相邻帧突变。",
        "params": {"field_name": "observation.state", "min_value": None,
                   "max_value": None, "max_delta": None},
        "fields": {
            "field_name": _text("检查字段"),
            "min_value": _number("最小值（可留空）", minimum=-1e12, maximum=1e12, optional=True),
            "max_value": _number("最大值（可留空）", minimum=-1e12, maximum=1e12, optional=True),
            "max_delta": _number("最大单帧变化量（可留空）", maximum=1e12, optional=True),
        },
    },
    "numeric_freeze": {
        "label": "数值字段冻结",
        "default_severity": "WARN",
        "description": "按 Episode 检查目标向量连续近似不变的实际持续时间。",
        "params": {"field_name": "observation.state", "epsilon": 0.0001,
                   "freeze_seconds": 3.0},
        "fields": {
            "field_name": _text("检查字段"),
            "epsilon": _number("数值变化阈值 epsilon", maximum=1e12),
            "freeze_seconds": _number("连续冻结时间（秒）", minimum=0.01, maximum=86400),
        },
    },
    "video_quality": {
        "label": "视频规格、曝光与冻结",
        "default_severity": "WARN",
        "description": "实际解码视频并检查分辨率、黑暗、过曝及连续画面冻结。",
        "params": {"video_key": "", "expected_width": None, "expected_height": None,
                   "dark_mean": 5.0, "bright_mean": 250.0, "max_dark_ratio": 0.02,
                   "max_bright_ratio": 0.02, "freeze_seconds": 1.0,
                   "frame_diff_threshold": 0.5, "sample_interval_ms": 200.0},
        "fields": {
            "video_key": _text("视频字段"),
            "expected_width": _number("标准宽度（可留空）", minimum=1, maximum=32768,
                                      step="1", optional=True),
            "expected_height": _number("标准高度（可留空）", minimum=1, maximum=32768,
                                       step="1", optional=True),
            "dark_mean": _number("黑暗帧均值阈值", maximum=255),
            "bright_mean": _number("过曝帧均值阈值", maximum=255),
            "max_dark_ratio": _number("最大黑暗帧比例", maximum=1),
            "max_bright_ratio": _number("最大过曝帧比例", maximum=1),
            "freeze_seconds": _number("画面冻结时间（秒）", minimum=0.01, maximum=86400),
            "frame_diff_threshold": _number("画面变化阈值", maximum=255),
            "sample_interval_ms": _number("抽样间隔（ms）", minimum=1, maximum=600000),
        },
    },
}

PARAMETER_HELP = {
    ("feature_dimension", "expected_dtype"): "按采集协议或模型输入定义填写，不能只照抄待检数据的声明。",
    ("feature_dimension", "expected_dimension"): "按机器人接口或模型输入维度填写；检查每个实际帧向量。",
    ("frame_rate", "expected_fps"): "填写采集配置规定的目标帧率。",
    ("frame_rate", "tolerance_fps"): "由采集时钟和编码波动确定；初始可取 0.5 FPS 或目标 FPS 的 2% 中较大者。",
    ("frame_rate", "max_gap_ms"): "允许的最大相邻帧间隔；初始可取两个标准帧周期。",
    ("frame_rate", "max_gap_ratio"): "允许超过最大帧间隔的比例，例如 0.01 表示 1%。",
    ("temporal_alignment", "max_offset_ms"): "按下游同步容忍度填写；初始可取一至两个标准帧周期。",
    ("numeric_validity", "min_value"): "按传感器量程、关节限位或动作归一化规范填写。",
    ("numeric_validity", "max_value"): "按传感器量程、关节限位或动作归一化规范填写。",
    ("numeric_validity", "max_delta"): "按正常运动速度和采样周期换算单帧最大变化量。",
    ("numeric_freeze", "epsilon"): "取略高于静止状态下的测量噪声；需用正常样本标定。",
    ("numeric_freeze", "freeze_seconds"): "填写业务中不应连续不变的时长；需避开正常静止或等待阶段。",
    ("video_quality", "dark_mean"): "用合格样本的灰度均值分布标定黑暗阈值。",
    ("video_quality", "bright_mean"): "用合格样本的灰度均值分布标定过曝阈值。",
    ("video_quality", "max_dark_ratio"): "允许黑暗抽样帧占比，例如 0.02 表示 2%。",
    ("video_quality", "max_bright_ratio"): "允许过曝抽样帧占比，例如 0.02 表示 2%。",
    ("video_quality", "freeze_seconds"): "填写业务允许画面连续不变的最长时长。",
    ("video_quality", "frame_diff_threshold"): "相邻抽样灰度差低于此值视为近似相同，需用静态和正常运动视频标定。",
    ("video_quality", "sample_interval_ms"): "曝光与冻结检查的抽样间隔，应不大于冻结时长。",
}
for (rule_kind, parameter), help_text in PARAMETER_HELP.items():
    ROBOT_RULE_TYPES[rule_kind]["fields"][parameter]["help"] = help_text

VLM_RULE_TYPES = {
    "task_completion": {"label": "任务完成判断",
        "prompt": "判断是否完成预期任务。若结尾状态不可见，返回 uncertain。"},
    "key_action_visible": {"label": "关键动作可见性",
        "prompt": "判断完成任务的关键动作是否在视频中清晰可见。不可见时返回 uncertain。"},
    "obvious_failure": {"label": "明显失败事件",
        "prompt": "检查是否出现掉落、碰撞、卡死、任务中断等明显失败。未观察到返回 pass。"},
    "custom": {"label": "自定义视频判断", "prompt": "描述需要判断的具体可见行为。"},
}
FAMILY_LABELS = {"vla": "VLA", "wam": "WAM", "ego": "EGO"}


def _dimension(feature: dict) -> int | None:
    shape = feature.get("shape") if isinstance(feature, dict) else None
    return shape[0] if isinstance(shape, list) and len(shape) == 1 and isinstance(shape[0], int) else None


def _video_size(feature: dict) -> tuple[int | None, int | None]:
    """Resolve width/height from the HWC and CHW shapes found in current datasets."""
    shape = feature.get("shape") if isinstance(feature.get("shape"), list) else []
    if len(shape) < 2 or any(not isinstance(value, int) for value in shape):
        return None, None
    names = feature.get("names")
    if isinstance(names, list) and {"height", "width"}.issubset(names):
        return shape[names.index("width")], shape[names.index("height")]
    if len(shape) >= 3 and shape[0] in {1, 3, 4} and shape[-1] not in {1, 3, 4}:
        return shape[2], shape[1]
    return shape[1], shape[0]


def _rule(kind: str, suffix: str, name: str, params: dict, *, severity: str = "FAIL") -> dict:
    return {"id": f"{kind}_{suffix}", "kind": kind, "name": name, "enabled": True,
            "severity": severity, "params": params}


def default_config(info: dict, *, dataset_family: str = "vla",
                   vlm_enabled: bool = False, reference_dataset_id: str | None = None) -> dict:
    """Build an editable family template with values suggested by one LeRobot dataset."""
    features = info.get("features") if isinstance(info.get("features"), dict) else {}
    video_keys = [key for key, value in features.items()
                  if isinstance(value, dict) and value.get("dtype") == "video"]
    fps = float(info.get("fps")) if isinstance(info.get("fps"), (int, float)) else 30.0
    rules = []
    for field, suffix, label in (
        ("observation.state", "state", "状态字段与实际维度"),
        ("action", "action", "动作字段与实际维度"),
    ):
        feature = features.get(field) if isinstance(features.get(field), dict) else {}
        rules.append(_rule("feature_dimension", suffix, label, {
            "field_name": field, "expected_dtype": str(feature.get("dtype") or "float32"),
            "expected_dimension": _dimension(feature) or 1,
        }))
    rules.append(_rule("frame_rate", "capture", "实际采集帧率", {
        "timestamp_field": "timestamp", "video_keys": video_keys,
        "expected_fps": fps, "tolerance_fps": 0.5,
        "max_gap_ms": round(2000 / fps, 3), "max_gap_ratio": 0.01,
    }))
    rules.append(_rule("temporal_alignment", "modalities", "状态、动作与视频时长对齐", {
        "timestamp_field": "timestamp", "video_keys": video_keys,
        "expected_fps": fps, "max_offset_ms": round(2000 / fps, 3),
    }))
    for field, suffix, label in (
        ("observation.state", "state", "状态数值有效性与突变"),
        ("action", "action", "动作数值有效性与突变"),
    ):
        rules.append(_rule("numeric_validity", suffix, label, {
            "field_name": field, "min_value": None, "max_value": None, "max_delta": None,
        }, severity="WARN"))
        rules.append(_rule("numeric_freeze", suffix, f"{label[:2]}字段冻结", {
            "field_name": field, "epsilon": 0.0001, "freeze_seconds": 3.0,
        }, severity="WARN"))
    for index, key in enumerate(video_keys):
        feature = features[key]
        width, height = _video_size(feature)
        rules.append(_rule("video_quality", f"camera_{index + 1}", f"{key} 视频规格与质量", {
            "video_key": key, "expected_width": width, "expected_height": height,
            "dark_mean": 5.0, "bright_mean": 250.0,
            "max_dark_ratio": 0.02, "max_bright_ratio": 0.02,
            "freeze_seconds": 1.0, "frame_diff_threshold": 0.5,
            "sample_interval_ms": 200.0,
        }, severity="WARN"))
    vlm_rules = []
    for kind in ("task_completion", "key_action_visible", "obvious_failure"):
        entry = VLM_RULE_TYPES[kind]
        vlm_rules.append({"id": kind, "kind": kind, "name": entry["label"],
                          "enabled": vlm_enabled, "camera_key": video_keys[0] if video_keys else "",
                          "prompt": entry["prompt"]})
    return {
        "config_schema_version": 2,
        "template": {"id": dataset_family, "version": 1,
                     "dataset_family": dataset_family,
                     "reference_dataset_id": reference_dataset_id},
        "robot": {"robot_type": robot_type_label(info), "video_keys": video_keys},
        "doctor": {"checks": [{"id": check, "enabled": True} for check, _ in DOCTOR_CHECKS],
                   "max_episodes": None},
        "robot_rules": deepcopy(rules),
        "vlm": {"model": "qwen3-vl-4b", "timeout_seconds": 120, "max_tokens": 256,
                "max_episodes": None, "rules": vlm_rules},
    }
