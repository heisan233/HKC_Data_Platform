"""Supported checks and rule templates for the pinned Doctor release and HKC checkers."""

from __future__ import annotations

from copy import deepcopy

# lerobot-doctor 0.2.0 exposes these names in runner._get_all_checks().
DOCTOR_CHECKS = [
    ("metadata", "元数据与格式"),
    ("temporal", "时序一致性"),
    ("actions", "动作质量"),
    ("videos", "视频完整性"),
    ("statistics", "统计分布"),
    ("episodes", "Episode 健康度"),
    ("consistency", "字段一致性"),
    ("training", "训练就绪性"),
    ("anomalies", "异常检测"),
    ("portability", "可移植性"),
    ("per_episode", "逐 Episode 诊断"),
]
DOCTOR_IDS = {item[0] for item in DOCTOR_CHECKS}
DOCTOR_REPORT_NAMES = {
    "Metadata & Format Compliance": "元数据与格式合规",
    "Temporal Consistency": "时序一致性",
    "Action Quality": "动作质量",
    "Video Integrity": "视频完整性",
    "Data Distribution": "数据分布",
    "Episode Health": "Episode 健康度",
    "Feature Consistency": "字段一致性",
    "Training Readiness": "训练就绪性",
    "Anomaly Detection": "异常检测",
    "Portability": "数据可移植性",
    "Per-Episode Summary": "逐 Episode 诊断",
}

ROBOT_RULE_TYPES = {
    "feature_schema": {
        "label": "机器人字段与维度",
        "description": "校验 state、action 和相机字段与机器人配置一致。",
        "params": {},
    },
    "capture_fps": {
        "label": "采集帧率范围",
        "description": "比对数据集声明帧率和机器人配置的允许偏差。",
        "params": {"tolerance_fps": 2.0},
    },
    "camera_sync": {
        "label": "相机与状态时长对齐",
        "description": "使用 LeRobot v3 Episode 元数据比对各相机片段与状态帧时长。",
        "params": {"max_offset_ms": 100.0},
    },
    "state_freeze": {
        "label": "机器人状态冻结",
        "description": "检测状态向量连续不变的时间。",
        "params": {"freeze_seconds": 3.0, "epsilon": 0.0001},
    },
    "action_freeze": {
        "label": "机器人动作冻结",
        "description": "检测动作向量连续不变的时间。",
        "params": {"freeze_seconds": 3.0, "epsilon": 0.0001},
    },
    "joint_limits": {
        "label": "专属关节范围",
        "description": "根据机器人配置中的各关节上下限检查状态或动作数值。",
        "params": {"field": "action"},
    },
    "video_quality": {
        "label": "画面曝光与冻结",
        "description": "按配置相机抽样识别黑帧、过曝及画面冻结；记录视频时间点。",
        "params": {"dark_mean": 5.0, "bright_mean": 250.0,
                   "freeze_seconds": 1.0, "sample_stride": 5.0},
    },
}

VLM_RULE_TYPES = {
    "task_completion": {
        "label": "任务完成判断",
        "prompt": "判断机器人是否完成预期任务。若结尾状态不可见，返回 uncertain。",
    },
    "key_action_visible": {
        "label": "关键动作可见性",
        "prompt": "判断完成任务的关键动作是否在视频中清晰可见。不可见时返回 uncertain。",
    },
    "obvious_failure": {
        "label": "明显失败事件",
        "prompt": "检查是否出现掉落、碰撞、卡死、任务中断等明显失败。未观察到返回 pass。",
    },
    "custom": {"label": "自定义视频判断", "prompt": "描述需要判断的具体可见行为。"},
}


def default_config(info: dict, *, vlm_enabled: bool = False) -> dict:
    features = info.get("features") if isinstance(info.get("features"), dict) else {}
    state = features.get("observation.state") or {}
    action = features.get("action") or {}
    video_keys = [key for key, value in features.items()
                  if isinstance(value, dict) and value.get("dtype") == "video"]

    def dimension(item: dict) -> int | None:
        shape = item.get("shape") if isinstance(item, dict) else None
        return shape[0] if isinstance(shape, list) and len(shape) == 1 and isinstance(shape[0], int) else None

    rules = []
    for kind in ("feature_schema", "capture_fps", "camera_sync", "state_freeze", "action_freeze", "joint_limits", "video_quality"):
        entry = ROBOT_RULE_TYPES[kind]
        rules.append({"id": kind, "kind": kind, "name": entry["label"],
                      "enabled": kind != "joint_limits", "severity": "WARN" if kind in
                      {"state_freeze", "action_freeze", "video_quality"} else "FAIL",
                      "params": deepcopy(entry["params"])})
    vlm_rules = []
    for kind in ("task_completion", "key_action_visible", "obvious_failure"):
        entry = VLM_RULE_TYPES[kind]
        vlm_rules.append({"id": kind, "kind": kind, "name": entry["label"],
                          "enabled": vlm_enabled, "camera_key": video_keys[0] if video_keys else "",
                          "prompt": entry["prompt"]})
    return {
        "robot": {
            "robot_type": str(info.get("robot_type", "")).strip(),
            "expected_fps": info.get("fps"),
            "state_key": "observation.state",
            "state_dim": dimension(state),
            "action_key": "action",
            "action_dim": dimension(action),
            "video_keys": video_keys,
            "joint_limits": {},
        },
        "doctor": {"checks": [{"id": check, "enabled": True} for check, _ in DOCTOR_CHECKS],
                   "max_episodes": None},
        "robot_rules": rules,
        "vlm": {"model": "qwen3-vl-4b", "timeout_seconds": 120, "max_tokens": 256,
                "max_episodes": None, "rules": vlm_rules},
    }
