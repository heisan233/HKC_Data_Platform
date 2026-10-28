"""The data page registers existing folders without handling uploads or moving data."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.dataset_robots import DatasetRobotRepository
from app.db.quality import QualityRepository
from app.db.robots import RobotRepository
from app.main import app
from app.modules.datasets import router as data_router
from app.modules.quality import router as quality_router
from app.modules.quality.service import raw_fingerprint


@pytest.fixture
def platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(data_root=tmp_path)
    monkeypatch.setattr(data_router, "settings", settings)
    monkeypatch.setattr(quality_router, "settings", settings)
    (tmp_path / "raw").mkdir()
    (tmp_path / "reports" / "qc").mkdir(parents=True)
    return TestClient(app), tmp_path


def dataset(root: Path, name: str, *, valid: bool = True) -> Path:
    folder = root / "raw" / name
    (folder / "meta").mkdir(parents=True)
    if valid:
        (folder / "meta" / "info.json").write_text(json.dumps({
            "codebase_version": "v3.0", "robot_type": "zhiyan",
            "total_episodes": 7, "total_frames": 2647, "fps": 20,
            "features": {},
        }), encoding="utf-8")
    return folder


def test_select_and_register_keeps_raw_folder(platform):
    client, root = platform
    source = dataset(root, "ds-20261019T030516Z-23781860")
    dataset(root, "broken", valid=False)
    page = client.get("/data/manage/")
    assert page.status_code == 200
    assert source.name in page.text
    assert "broken" in page.text

    result = client.post("/data/api/register", json={
        "folders": [source.name, "broken"], "names": {source.name: "展示样本"},
    })
    assert result.status_code == 200
    assert result.json()["registered"] == [{"folder": source.name, "id": "DS000001"}]
    assert result.json()["errors"][0]["folder"] == "broken"
    assert (source / "meta/info.json").exists()
    assert not (root / "raw" / "DS000001").exists()
    assert (root / "system/platform.sqlite").exists()
    assert "DS000001" in client.get("/data/raw/").text
    assert "展示样本" in client.get("/data/raw/").text
    assert source.name in client.get("/data/raw/DS000001").text

    duplicate = client.post("/data/api/register", json={"folders": [source.name]})
    assert duplicate.json()["registered"] == []
    assert "已经登记" in duplicate.json()["errors"][0]["message"]


def test_dataset_pages_show_read_only_collecting_robot(platform):
    client, root = platform
    dataset(root, "collected")
    client.post("/data/api/register", json={"folders": ["collected"]})
    robot = RobotRepository(root).create(
        name="一号采集臂", robot_type="other-type", serial_number="COLLECT-01",
    )
    DatasetRobotRepository(root).bind(robot["id"], ["DS000001"], "test_user")

    registered = client.get("/data/manage/?view=registered")
    listed = client.get("/data/raw/")
    detail = client.get("/data/raw/DS000001")

    for response in (registered, listed, detail):
        assert response.status_code == 200
        assert "RB000001" in response.text
        assert "一号采集臂" in response.text
    assert "关联关系只能在机器人管理页面修改" in detail.text


def test_folder_name_does_not_control_platform_sequence(platform):
    client, root = platform
    dataset(root, "DS000026")
    dataset(root, "later")
    first = client.post("/data/api/register", json={"folders": ["DS000026"]})
    second = client.post("/data/api/register", json={"folders": ["later"]})
    assert first.json()["registered"][0]["id"] == "DS000001"
    assert second.json()["registered"][0]["id"] == "DS000002"


def test_metadata_changes_and_missing_directory_are_visible(platform):
    client, root = platform
    source = dataset(root, "incoming")
    client.post("/data/api/register", json={"folders": ["incoming"]})
    changed = client.patch("/data/api/datasets/DS000001", json={
        "name": "抓取数据", "description": "批次 A", "tags": ["抓取", "抓取", "训练"],
    })
    assert changed.status_code == 200
    assert changed.json()["tags"] == ["抓取", "训练"]
    info = source / "meta/info.json"
    info.write_text(info.read_text(encoding="utf-8").replace("2647", "2648"), encoding="utf-8")
    page = client.get("/data/manage/?view=registered")
    assert "元数据已变化" in page.text
    assert client.post("/data/api/datasets/DS000001/rescan").status_code == 200
    assert "目录正常" in client.get("/data/manage/?view=registered").text
    source.rename(root / "raw" / "moved")
    assert "目录缺失" in client.get("/data/manage/?view=registered").text


def test_unsafe_paths_cannot_be_registered_or_served(platform):
    client, root = platform
    dataset(root, "safe")
    outside = root / "outside"
    outside.mkdir()
    (root / "raw" / "link").symlink_to(outside, target_is_directory=True)
    result = client.post("/data/api/register", json={"folders": ["../outside", "link", "safe"]})
    assert len(result.json()["registered"]) == 1
    assert len(result.json()["errors"]) == 2
    assert client.get("/data/raw/DS000001/video/../../outside").status_code in {404, 307}


def test_reports_use_registered_id(platform):
    client, root = platform
    dataset(root, "source")
    client.post("/data/api/register", json={"folders": ["source"]})
    report = root / "reports" / "qc" / "DS000001" / "run.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"decision":"PASS","status":"COMPLETED"}', encoding="utf-8")
    assert client.get("/data/detected/").status_code == 404
    assert "run.json" in client.get("/data/reports/").text
    assert 'aria-label="移入回收站 run.json"' in client.get("/data/reports/").text
    assert client.get("/data/reports/DS000001/run.json").json()["decision"] == "PASS"
    moved = client.delete("/data/reports/DS000001/run.json")
    assert moved.status_code == 200
    assert moved.json()["dataset_id"] == "DS000001"
    assert moved.json()["filename"] == "run.json"
    assert moved.json()["trashed"] is True
    assert not report.exists()
    trashed = root / "trash" / "reports" / "DS000001" / "run.json"
    metadata = root / "trash" / "reports" / "DS000001" / ".run.json.trash-meta.json"
    assert trashed.is_file()
    assert metadata.is_file()
    assert "run.json" not in client.get("/data/reports/").text
    trash_page = client.get("/data/reports/?view=trash")
    assert "run.json" in trash_page.text
    assert 'aria-label="恢复报告 run.json"' in trash_page.text
    assert client.get("/data/reports/trash/DS000001/run.json").json()["decision"] == "PASS"
    assert client.get("/data/reports/DS000001/run.json").status_code == 404
    assert client.delete("/data/reports/DS000001/run.json").status_code == 404
    restored = client.post("/data/reports/trash/DS000001/run.json/restore")
    assert restored.json() == {
        "dataset_id": "DS000001",
        "filename": "run.json",
        "original_path": "reports/qc/DS000001/run.json",
        "restored": True,
    }
    assert report.is_file()
    assert not trashed.exists()
    assert not metadata.exists()
    assert "run.json" in client.get("/data/reports/").text
    assert "run.json" not in client.get("/data/reports/?view=trash").text


def test_delete_qc_report_preserves_task_and_review(platform):
    client, root = platform
    source = dataset(root, "source")
    client.post("/data/api/register", json={"folders": ["source"]})
    repo = QualityRepository(root)
    plan = repo.create_plan("测试方案", "zhiyan", {})
    task = repo.create_task("DS000001", plan, {}, raw_fingerprint(source))
    report = root / "reports" / "qc" / "DS000001" / f"{task['id']}.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"status":"COMPLETED","decision":"PASS"}', encoding="utf-8")

    assert client.delete(f"/data/reports/DS000001/{report.name}").status_code == 409
    assert report.exists()
    repo.set_task(task["id"], status="COMPLETED", outcome="PASS")
    repo.review(task["id"], "PASS", "人工确认")
    assert client.delete(f"/data/reports/DS000001/{report.name}").status_code == 200
    assert not report.exists()
    trashed = root / "trash" / "reports" / "DS000001" / report.name
    assert trashed.is_file()
    assert repo.get_task(task["id"])["decision"] == "PASS"
    assert repo.reviews(task["id"])[0]["reason"] == "人工确认"
    assert "报告文件已移入回收站或不可读取" in client.get(
        f"/qc/reports/{task['id']}"
    ).text
    assert client.get(f"/qc/reports/{task['id']}/download").status_code == 404
    assert client.post(f"/qc/api/tasks/{task['id']}/review", json={
        "decision": "PASS", "reason": "再次确认",
    }).status_code == 409
    assert client.post(
        f"/data/reports/trash/DS000001/{report.name}/restore"
    ).status_code == 200
    assert report.is_file()
    assert repo.get_task(task["id"])["decision"] == "PASS"
    assert repo.reviews(task["id"])[0]["reason"] == "人工确认"
    assert client.get(f"/qc/reports/{task['id']}/download").status_code == 200
    assert client.get(f"/data/reports/DS000001/{report.name}").status_code == 200


def test_report_delete_rejects_other_files(platform):
    client, root = platform
    dataset(root, "source")
    client.post("/data/api/register", json={"folders": ["source"]})
    report_dir = root / "reports" / "qc" / "DS000001"
    report_dir.mkdir(parents=True)
    outside = root / "secret.json"
    outside.write_text("private", encoding="utf-8")
    (report_dir / "link.json").symlink_to(outside)
    assert client.delete("/data/reports/DS000001/link.json").status_code == 404
    assert client.delete("/data/reports/DS999999/secret.json").status_code == 404
    assert client.get("/data/reports/trash/DS000001/../secret.json").status_code == 404
    assert client.post(
        "/data/reports/trash/DS000001/missing.json/restore"
    ).status_code == 404
    assert outside.read_text(encoding="utf-8") == "private"


def test_data_pages_show_latest_human_review_state(platform):
    client, root = platform
    source = dataset(root, "source")
    client.post("/data/api/register", json={"folders": ["source"]})
    repo = QualityRepository(root)
    plan = repo.create_plan("测试方案", "zhiyan", {})

    assert "未质检" in client.get("/data/raw/").text
    assert "/data/raw/?qc=review" in client.get("/data/").text

    task = repo.create_task("DS000001", plan, {}, raw_fingerprint(source))
    repo.set_task(task["id"], status="COMPLETED", outcome="PASS")
    assert "待人工审核" in client.get("/data/raw/?qc=review").text
    assert "DS000001" in client.get("/data/raw/?qc=review").text
    assert "已审核通过" not in client.get("/data/manage/?view=registered").text

    repo.review(task["id"], "PASS", "")
    assert "DS000001" not in client.get("/data/raw/?qc=review").text
    assert "DS000001" in client.get("/data/raw/?qc=passed").text
    assert "已审核通过" in client.get("/data/raw/DS000001").text

    repo.set_task_visibility(task["id"], True)
    assert "DS000001" in client.get("/data/raw/?qc=passed").text

    (source / "meta" / "new.txt").write_text("changed", encoding="utf-8")
    assert "DS000001" in client.get("/data/raw/?qc=stale").text
    assert "DS000001" not in client.get("/data/raw/?qc=passed").text

    latest = repo.create_task("DS000001", plan, {}, raw_fingerprint(source))
    assert latest["id"] != task["id"]
    assert "DS000001" in client.get("/data/raw/?qc=running").text
    assert "DS000001" not in client.get("/data/raw/?qc=stale").text


def test_video_preview_stays_inside_dataset(platform):
    client, root = platform
    source = dataset(root, "source")
    client.post("/data/api/register", json={"folders": ["source"]})
    video = source / "videos" / "camera" / "chunk-000" / "file-000.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"fake-mp4-for-routing-test")
    response = client.get("/data/raw/DS000001/video/camera/chunk-000/file-000.mp4")
    assert response.status_code == 200
    assert response.content == video.read_bytes()
    outside = root / "other.mp4"
    outside.write_bytes(b"private")
    (source / "videos" / "leak.mp4").symlink_to(outside)
    assert client.get("/data/raw/DS000001/video/leak.mp4").status_code == 404
