"""Streaming, framework-independent action errors for held-out episodes."""

from __future__ import annotations

import math


def validated_action_width(dataset_dim: int, checkpoint_dim: int | None,
                           model_dim: int, prediction_dim: int) -> int:
    """Accept only the X-VLA model tail used to pad a smaller dataset action."""
    if dataset_dim < 1 or checkpoint_dim != dataset_dim:
        raise ValueError("checkpoint 动作维度与验证数据集不一致")
    if prediction_dim == dataset_dim:
        return dataset_dim
    if model_dim > dataset_dim and prediction_dim == model_dim:
        return dataset_dim
    raise ValueError("模型预测动作维度与验证数据集不一致")


class EpisodeActionMetrics:
    """Score every frame, while retaining a bounded set of chart points."""

    def __init__(self, episode_id: int, max_chart_points: int = 800) -> None:
        self.episode_id = episode_id
        self.max_chart_points = max_chart_points
        self.frames = 0
        self.absolute_error: list[float] = []
        self.points: list[dict] = []
        self.stride = 1
        self.last_point: dict | None = None

    def add(self, truth: list[float], prediction: list[float]) -> None:
        if not truth or len(truth) != len(prediction):
            raise ValueError("动作真值与预测的维度不一致")
        if not self.absolute_error:
            self.absolute_error = [0.0] * len(truth)
        if len(truth) != len(self.absolute_error):
            raise ValueError("同一 Episode 的动作维度发生变化")
        if not all(math.isfinite(value) for value in (*truth, *prediction)):
            raise ValueError("动作真值或预测包含 NaN/Inf")
        for index, (actual, predicted) in enumerate(zip(truth, prediction, strict=True)):
            self.absolute_error[index] += abs(actual - predicted)
        point = {"frame": self.frames, "truth": truth, "prediction": prediction}
        self.last_point = point
        if self.frames % self.stride == 0:
            self.points.append(point)
        self.frames += 1
        if len(self.points) > self.max_chart_points:
            self.points = self.points[::2]
            self.stride *= 2

    def report(self) -> tuple[dict, dict]:
        if self.frames == 0:
            raise ValueError("留出 Episode 没有可评估的帧")
        if self.last_point is not None and self.points[-1]["frame"] != self.last_point["frame"]:
            if len(self.points) >= self.max_chart_points:
                self.points.pop()
            self.points.append(self.last_point)
        dimensions = [
            {"index": index, "mae": error / self.frames}
            for index, error in enumerate(self.absolute_error)
        ]
        summary = {
            "episode_id": self.episode_id,
            "frames": self.frames,
            "action_mae": sum(self.absolute_error) / (self.frames * len(dimensions)),
            "action_mae_per_dimension": dimensions,
            "chart_points": len(self.points),
        }
        trace = {**summary, "points": self.points}
        return summary, trace
