"""Evaluate an X-VLA checkpoint on held-out LeRobot v3 episodes.

Uses the installed LeRobot code. Both forward loss and action MAE are offline
metrics on the task's fixed held-out episodes, never robot success rates.
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
from training_action_metrics import EpisodeActionMetrics, validated_action_width


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


def _action_values(action: torch.Tensor) -> list[float]:
    """Return the current action vector (not the future training action chunk)."""
    if action.ndim == 3:
        action = action[:, 0, :]
    return [float(value) for value in action.detach().cpu().reshape(-1).tolist()]


def _dataset_action_prediction(policy, policy_cfg, prepared: dict,
                               actual: torch.Tensor, postprocessor) -> tuple[list[float], list[float]]:
    """Discard only X-VLA's padded tail before restoring dataset action units."""
    truth = _action_values(actual)
    feature = policy_cfg.action_feature
    expected_dim = feature.shape[-1] if feature is not None else None
    prediction = policy.select_action(prepared)
    width = validated_action_width(
        len(truth), expected_dim, policy.model.dim_action, prediction.shape[-1],
    )
    prediction = prediction[..., :width]
    return truth, _action_values(postprocessor(prediction))


def evaluate(folder: Path, repo_id: str, checkpoint: Path,
             episodes: list[int], trace_dir: Path) -> dict:
    if not episodes:
        raise ValueError("验证集 Episode 为空")
    if not torch.cuda.is_available():
        raise RuntimeError("验证阶段未检测到 CUDA GPU")
    torch.manual_seed(0)
    torch.cuda.manual_seed_all(0)
    policy_cfg = PreTrainedConfig.from_pretrained(checkpoint)
    policy_cfg.pretrained_path = checkpoint
    metadata = LeRobotDatasetMetadata(repo_id, root=folder)
    delta_timestamps = resolve_delta_timestamps(policy_cfg, metadata)
    dataset = LeRobotDataset(repo_id, root=folder, episodes=episodes,
                             delta_timestamps=delta_timestamps,
                             video_backend="torchcodec")
    camera_map = checkpoint_camera_rename_map(checkpoint)
    policy = make_policy(cfg=policy_cfg, ds_meta=dataset.meta, rename_map=camera_map)
    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=policy_cfg, pretrained_path=checkpoint,
        preprocessor_overrides={"device_processor": {"device": "cuda"}},
    )
    policy.eval()
    loader = torch.utils.data.DataLoader(dataset, batch_size=1,
                                         shuffle=False, num_workers=0)
    total_loss = 0.0
    count = 0
    smoke = False
    per_episode: list[dict] = []
    current: EpisodeActionMetrics | None = None
    seen: set[int] = set()
    trace_dir.mkdir(parents=True, exist_ok=True)

    def finish_episode() -> None:
        if current is None:
            return
        summary, trace = current.report()
        trace_path = trace_dir / f"episode-{current.episode_id}.json"
        trace_path.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
        per_episode.append(summary)

    with torch.inference_mode():
        for batch in loader:
            episode_id = int(batch["episode_index"].reshape(-1)[0].item())
            if episode_id not in episodes:
                raise ValueError("验证数据包含非留出 Episode")
            if current is None or current.episode_id != episode_id:
                finish_episode()
                if episode_id in seen:
                    raise ValueError("同一验证 Episode 的帧未连续排列")
                seen.add(episode_id)
                current = EpisodeActionMetrics(episode_id)
                policy.reset()
            prepared = preprocessor(batch)
            loss, _ = policy.forward(prepared)
            value = float(loss.item())
            if not math.isfinite(value):
                raise ValueError("验证损失不是有限数")
            total_loss += value
            count += 1
            truth, prediction = _dataset_action_prediction(
                policy, policy_cfg, prepared, batch["action"], postprocessor,
            )
            current.add(truth, prediction)
            smoke = True
    finish_episode()
    if count == 0:
        raise ValueError("验证集没有帧")
    if seen != set(episodes):
        raise ValueError("验证集缺少部分留出 Episode")
    dimensions = len(per_episode[0]["action_mae_per_dimension"])
    if any(len(row["action_mae_per_dimension"]) != dimensions for row in per_episode):
        raise ValueError("验证 Episode 的动作维度不一致")
    absolute_errors = [0.0] * dimensions
    for row in per_episode:
        for item in row["action_mae_per_dimension"]:
            absolute_errors[item["index"]] += item["mae"] * row["frames"]
    per_dimension = [
        {"index": index, "mae": error / count}
        for index, error in enumerate(absolute_errors)
    ]
    return {
        "checkpoint_smoke_test": smoke,
        "metric_definition": "留出 Episode 全帧平均前向损失；动作 MAE 为逐帧预测与示教动作的绝对误差，非机器人任务成功率",
        "metrics": {
            "validation_forward_loss": total_loss / count,
            "action_mae": sum(absolute_errors) / (count * dimensions),
            "action_mae_per_dimension": per_dimension,
        },
        "validation_frames": count,
        "validation_episode_ids": episodes,
        "action_episodes": per_episode,
        "action_evaluation_method": "按帧输入留出 Episode 的示教观测；每个 Episode 重置策略动作队列，使用 checkpoint 后处理还原动作单位",
        "action_evaluation_seed": 0,
        "camera_rename_map": camera_map,
        "checkpoint": str(checkpoint),
    }


def main() -> None:
    if len(sys.argv) < 6:
        raise SystemExit("usage: training_evaluate.py DATASET_ROOT REPO_ID CHECKPOINT OUTPUT EPISODE...")
    folder, repo_id, checkpoint, output = sys.argv[1:5]
    episodes = [int(value) for value in sys.argv[5:]]
    result = evaluate(Path(folder), repo_id, Path(checkpoint), episodes,
                      Path(output).parent / "action-traces")
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
