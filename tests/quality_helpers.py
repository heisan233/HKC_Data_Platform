"""Create a real persisted QC approval for integration-test datasets."""

from pathlib import Path

from app.db.datasets import DatasetRepository
from app.db.quality import QualityRepository
from app.modules.datasets.service import directory
from app.modules.quality.service import raw_fingerprint


def approve_dataset(root: Path, dataset_id: str) -> str:
    record = DatasetRepository(root).get(dataset_id)
    assert record is not None
    folder = directory(root, record["storage_area"], record["storage_relpath"])
    assert folder is not None
    repo = QualityRepository(root)
    plan = repo.create_plan("测试审核", record["robot_type"], {})
    task = repo.create_task(dataset_id, plan, {}, raw_fingerprint(folder))
    repo.set_task(task["id"], status="COMPLETED", phase="completed", outcome="PASS")
    repo.review(task["id"], "PASS", "测试审核通过")
    return task["id"]
