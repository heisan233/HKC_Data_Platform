"""Evaluate an X-VLA checkpoint on held-out LeRobot v3 episodes.

Uses the installed LeRobot code. The metric is offline forward loss, not a
robot success rate or an action MAE. The saved checkpoint is loaded afresh.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import torch
from lerobot.configs.policies import PreTrainedConfig
from lerobot.datasets.factory import resolve_delta_timestamps
from lerobot.datasets.lerobot_dataset import LeRobotDataset, LeRobotDatasetMetadata
from lerobot.policies.factory import make_policy, make_pre_post_processors


def checkpoint_camera_rename_map(checkpoint: Path) -> dict[str, str]:
    """Read the exact camera mapping saved with the training preprocessor."""
    config = json.loads((checkpoint / "policy_preprocessor.json").read_text(encoding="utf-8"))
    for step in config.get("steps", []):
        if step.get("registry_name") == "rename_observations_processor":
            value = step.get("config", {}).get("rename_map", {})
            if not isinstance(value, dict) or not all(
                isinstance(key, str) and isinstance(target, str) for key, target in value.items()
            ):
                raise ValueError("checkpoint 相机映射无效")
            return value
    raise ValueError("checkpoint 缺少相机映射处理器")


def evaluate(folder: Path, repo_id: str, checkpoint: Path,
             episodes: list[int]) -> dict:
    if not episodes:
        raise ValueError("验证集 Episode 为空")
    if not torch.cuda.is_available():
        raise RuntimeError("验证阶段未检测到 CUDA GPU")
    policy_cfg = PreTrainedConfig.from_pretrained(checkpoint)
    policy_cfg.pretrained_path = checkpoint
    metadata = LeRobotDatasetMetadata(repo_id, root=folder)
    delta_timestamps = resolve_delta_timestamps(policy_cfg, metadata)
    dataset = LeRobotDataset(repo_id, root=folder, episodes=episodes,
                             delta_timestamps=delta_timestamps,
                             video_backend="torchcodec")
    camera_map = checkpoint_camera_rename_map(checkpoint)
    policy = make_policy(cfg=policy_cfg, ds_meta=dataset.meta, rename_map=camera_map)
    preprocessor, _ = make_pre_post_processors(
        policy_cfg=policy_cfg, pretrained_path=checkpoint,
        preprocessor_overrides={"device_processor": {"device": "cuda"}},
    )
    policy.eval()
    loader = torch.utils.data.DataLoader(dataset, batch_size=1,
                                         shuffle=False, num_workers=0)
    total_loss = 0.0
    count = 0
    smoke = False
    with torch.inference_mode():
        for batch in loader:
            prepared = preprocessor(batch)
            loss, _ = policy.forward(prepared)
            value = float(loss.item())
            if not math.isfinite(value):
                raise ValueError("验证损失不是有限数")
            total_loss += value
            count += 1
            if not smoke:
                action = policy.select_action(prepared)
                if not torch.isfinite(action).all():
                    raise ValueError("推理冒烟测试输出包含 NaN 或 Inf")
                smoke = True
    if count == 0:
        raise ValueError("验证集没有帧")
    return {
        "checkpoint_smoke_test": smoke,
        "metric_definition": "Held-out Episode mean forward loss; not action MAE or task success",
        "metrics": {"validation_forward_loss": total_loss / count},
        "validation_frames": count,
        "validation_episode_ids": episodes,
        "camera_rename_map": camera_map,
        "checkpoint": str(checkpoint),
    }


def main() -> None:
    if len(sys.argv) < 6:
        raise SystemExit("usage: training_evaluate.py DATASET_ROOT REPO_ID CHECKPOINT OUTPUT EPISODE...")
    folder, repo_id, checkpoint, output = sys.argv[1:5]
    episodes = [int(value) for value in sys.argv[5:]]
    result = evaluate(Path(folder), repo_id, Path(checkpoint), episodes)
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
