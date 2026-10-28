"""System management renders real, redacted request logs."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.modules.system import service


def test_request_logs_are_persistent_and_do_not_store_query_values(tmp_path: Path) -> None:
    client = TestClient(app)

    response = client.get("/dashboard/?token=must-not-be-logged")
    assert response.status_code == 200
    missing = client.get("/definitely-missing")
    assert missing.status_code == 404

    log_file = tmp_path / "system" / "logs" / "platform.jsonl"
    assert log_file.is_file()
    raw = log_file.read_text(encoding="utf-8")
    assert "must-not-be-logged" not in raw
    events = [json.loads(line) for line in raw.splitlines()]
    assert events[0]["path"] == "/dashboard/"
    assert events[0]["module"] == "工作台"
    assert events[0]["username"] == "test_user"
    assert events[0]["status_code"] == 200
    assert events[1]["level"] == "WARNING"
    assert events[1]["status_code"] == 404

    page = client.get("/system/")
    assert page.status_code == 200
    assert "真实访问记录" in page.text
    assert "GET /dashboard/" in page.text
    assert "test_user" in page.text
    assert "成功" in page.text
    assert ">404<" in page.text
    assert "must-not-be-logged" not in page.text


def test_system_log_filters_use_real_records(tmp_path: Path) -> None:
    service.write_request_log(
        tmp_path,
        username="operator",
        method="POST",
        path="/qc/api/tasks",
        status_code=500,
        duration_ms=12.345,
    )
    service.write_request_log(
        tmp_path,
        username="viewer",
        method="GET",
        path="/data/raw/DS000001",
        status_code=200,
        duration_ms=3,
    )

    snapshot = service.log_snapshot(
        tmp_path,
        level="ERROR",
        module="数据质检",
        query="operator",
    )
    assert snapshot["matched"] == 1
    assert snapshot["stats"]["total"] == 2
    assert snapshot["stats"]["error"] == 1
    assert snapshot["logs"][0]["path"] == "/qc/api/tasks"
    assert snapshot["logs"][0]["duration_ms"] == 12.35
