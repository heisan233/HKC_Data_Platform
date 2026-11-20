import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_redirects_to_dashboard() -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard/"


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/",
        "/semantics/",
        "/robots/",
        "/data/",
        "/ingestion/",
        "/preprocess/",
        "/qc/",
        "/annotation/",
        "/export/",
        "/training/",
        "/training/lawam",
        "/system/",
    ],
)
def test_legacy_pages_still_render(path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_removed_scenes_page_returns_not_found() -> None:
    response = client.get("/scenes/")

    assert response.status_code == 404


def test_lawam_page_keeps_parameters_and_exposes_live_controls() -> None:
    page = client.get("/training/lawam")

    assert page.status_code == 200
    assert "<h1>WAM 训练</h1>" in page.text
    assert 'href="/training/" class=""' in page.text
    assert 'href="/training/lawam" class="active"' in page.text
    for field in (
        "lawam-base", "lawam-dataset", "lawam-mode", "lawam-episodes", "lawam-steps",
        "lawam-batch", "lawam-horizon", "lawam-warmup", "lawam-lr",
        "lawam-unfreeze", "lawam-save", "lawam-freeze-vision",
        "lawam-freeze-llm", "lawam-stop-vllm", "lawam-eval-task",
        "lawam-eval-task-id", "lawam-eval-episodes", "lawam-eval-replan",
        "lawam-eval-text",
    ):
        assert f'id="{field}"' in page.text
    assert 'id="lawam-submit" type="submit"' in page.text
    assert 'id="lawam-eval-submit" type="submit"' in page.text
    assert 'id="lawam-task-rows"' in page.text
    assert 'id="lawam-detail"' in page.text
    assert 'id="lawam-metric-chart"' in page.text
    assert 'id="lawam-metric-select"' in page.text
    assert 'id="lawam-trash-toggle"' in page.text
    assert 'id="lawam-evaluation-rows"' in page.text
    for field in ("lawam-env-overview", "lawam-env-state", "lawam-env-gpu",
                  "lawam-env-meter-fill", "lawam-env-refresh"):
        assert f'id="{field}"' in page.text
    assert 'LaWAM 平台任务尚未接入' not in page.text
    assert '本页不读取 18001 训练台任务' not in page.text


def test_vla_and_wam_training_are_separate_navigation_entries() -> None:
    page = client.get("/training/")

    assert page.status_code == 200
    assert "<h1>VLA 训练</h1>" in page.text
    assert 'href="/training/" class="active"' in page.text
    assert 'href="/training/lawam" class=""' in page.text
    assert page.text.index("🧠 VLA 训练") < page.text.index("🐟 WAM 训练")
    assert 'class="train-lawam-link"' not in page.text
