"""Dashboard aggregation and empty-state rendering tests."""

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.datasets import DatasetRepository
from app.db.quality import QualityRepository
from app.db.robots import RobotRepository
from app.main import app

client = TestClient(app)


def _dataset(root: Path) -> str:
    folder = root / "raw" / "sample"
    (folder / "meta").mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "robot_type": "so100",
        "total_episodes": 3,
        "total_frames": 120,
        "fps": 20,
        "features": {},
    }
    raw = json.dumps(info).encode()
    (folder / "meta" / "info.json").write_bytes(raw)
    return DatasetRepository(root).register(
        "raw", "sample", "示例数据集", "so100", hashlib.sha256(raw).hexdigest()
    )["id"]


def test_dashboard_empty_state_has_no_demo_content() -> None:
    response = client.get("/dashboard/")

    assert response.status_code == 200
    assert 'href="/dashboard" class="active"' in response.text
    assert "还没有已登记数据集" in response.text
    assert "H4展厅导览采集" not in response.text
    assert "暂无质检任务" in response.text
    assert "暂无训练任务" in response.text


def test_dashboard_reads_real_dataset_robot_and_quality_records(tmp_path: Path) -> None:
    dataset_id = _dataset(tmp_path)
    RobotRepository(tmp_path).create(
        name="SO100 一号机",
        robot_type="so100",
        serial_number="SN-001",
        network_address="192.0.2.10",
    )
    quality = QualityRepository(tmp_path)
    plan = quality.create_plan("SO100 基础质检", "so100", {})
    task = quality.create_task(dataset_id, plan, {}, "fingerprint")
    quality.set_task(task["id"], status="COMPLETED", phase="complete")
    quality.review(task["id"], "PASS", "测试通过")

    response = client.get("/dashboard/")

    assert response.status_code == 200
    assert dataset_id in response.text
    assert "示例数据集" in response.text
    assert "3 Episodes" in response.text
    assert "SO100 一号机" not in response.text  # Same robot_type does not imply a link.
    assert "已审核通过" in response.text
    assert task["id"] in response.text
