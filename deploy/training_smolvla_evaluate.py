"""Offline checkpoint smoke test and held-out loss for SmolVLA/LeRobot v3."""

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


def camera_map(checkpoint: Path) -> dict[str, str]:
    processor = json.loads((checkpoint / "policy_preprocessor.json").read_text(encoding="utf-8"))
    for step in processor.get("steps", []):
        if step.get("registry_name") == "rename_observations_processor":
            result = step.get("config", {}).get("rename_map", {})
            if isinstance(result, dict) and all(isinstance(key, str) and
                                                isinstance(value, str)
                                                for key, value in result.items()):
                return result
    raise ValueError("SmolVLA checkpoint 缺少相机映射")


def evaluate(folder: Path, repo_id: str, checkpoint: Path,
             episodes: list[int]) -> dict:
    if not episodes or not torch.cuda.is_available():
        raise ValueError("SmolVLA 验证需要留出 Episode 和 CUDA GPU")
    torch.manual_seed(0)
    torch.cuda.manual_seed_all(0)
    policy_cfg = PreTrainedConfig.from_pretrained(checkpoint)
    policy_cfg.pretrained_path = checkpoint
    metadata = LeRobotDatasetMetadata(repo_id, root=folder)
    dataset = LeRobotDataset(repo_id, root=folder, episodes=episodes,
                             delta_timestamps=resolve_delta_timestamps(policy_cfg, metadata),
                             video_backend="torchcodec")
    rename_map = camera_map(checkpoint)
    policy = make_policy(cfg=policy_cfg, ds_meta=dataset.meta, rename_map=rename_map)
    preprocessor, _postprocessor = make_pre_post_processors(
        policy_cfg=policy_cfg, pretrained_path=checkpoint,
        preprocessor_overrides={"device_processor": {"device": "cuda"}},
    )
    policy.eval()
    loader = torch.utils.data.DataLoader(dataset, batch_size=1,
                                         shuffle=False, num_workers=0)
    count = 0
    total_loss = 0.0
    seen: set[int] = set()
    smoke = False
    with torch.inference_mode():
        for batch in loader:
            seen.add(int(batch["episode_index"].reshape(-1)[0].item()))
            prepared = preprocessor(batch)
            loss, _ = policy.forward(prepared)
            value = float(loss.item())
            if not math.isfinite(value):
                raise ValueError("SmolVLA 验证损失不是有限数")
            total_loss += value
            count += 1
            if not smoke:
                policy.reset()
                action = policy.select_action(prepared)
                if not torch.isfinite(action).all():
                    raise ValueError("SmolVLA checkpoint 推理结果包含非有限值")
                smoke = True
    if count == 0 or seen != set(episodes):
        raise ValueError("SmolVLA 验证集缺少留出 Episode 帧")
    return {
        "checkpoint_smoke_test": smoke,
        "metric_definition": "留出 Episode 全帧平均前向损失；非机器人任务成功率",
        "metrics": {"validation_forward_loss": total_loss / count},
        "validation_frames": count,
        "validation_episode_ids": episodes,
        "camera_rename_map": rename_map,
        "checkpoint": str(checkpoint),
    }


def main() -> None:
    if len(sys.argv) < 6:
        raise SystemExit(
            "usage: training_smolvla_evaluate.py DATASET_ROOT REPO_ID CHECKPOINT OUTPUT EPISODE..."
        )
    folder, repo_id, checkpoint, output = sys.argv[1:5]
    episodes = [int(value) for value in sys.argv[5:]]
    result = evaluate(Path(folder), repo_id, Path(checkpoint), episodes)
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                            encoding="utf-8")


if __name__ == "__main__":
    main()
