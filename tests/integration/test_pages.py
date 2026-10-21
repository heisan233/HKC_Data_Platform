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
        "/scenes/",
        "/data/",
        "/ingestion/",
        "/preprocess/",
        "/qc/",
        "/annotation/",
        "/export/",
        "/training/",
        "/system/",
    ],
)
def test_legacy_pages_still_render(path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
