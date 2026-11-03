"""Action comparison uses every held-out frame, even when charts are sampled."""

import pytest

from deploy.training_action_metrics import EpisodeActionMetrics, validated_action_width


def test_xvla_padded_prediction_uses_only_declared_dataset_action_dimensions():
    assert validated_action_width(14, 14, 20, 20) == 14
    assert validated_action_width(14, 14, 20, 14) == 14
    with pytest.raises(ValueError, match="checkpoint 动作维度"):
        validated_action_width(14, 20, 20, 20)
    with pytest.raises(ValueError, match="模型预测动作维度"):
        validated_action_width(14, 14, 20, 16)


def test_episode_mae_uses_all_frames_and_keeps_bounded_trace():
    metrics = EpisodeActionMetrics(7, max_chart_points=8)
    for frame in range(100):
        metrics.add([float(frame), 2.0], [float(frame) + 1.0, 0.0])
    summary, trace = metrics.report()
    assert summary["episode_id"] == 7
    assert summary["frames"] == 100
    assert summary["action_mae"] == pytest.approx(1.5)
    assert [row["mae"] for row in summary["action_mae_per_dimension"]] == [1.0, 2.0]
    assert len(trace["points"]) <= 8
    assert trace["points"][0]["frame"] == 0
    assert trace["points"][-1]["frame"] == 99


@pytest.mark.parametrize("truth,prediction", [([1.0], [1.0, 2.0]), ([float("nan")], [1.0])])
def test_episode_rejects_incompatible_or_non_finite_actions(truth, prediction):
    metrics = EpisodeActionMetrics(1)
    with pytest.raises(ValueError):
        metrics.add(truth, prediction)
