import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient

from app.db.datasets import DatasetRepository
from app.main import app
from app.modules.datasets import router as datasets_router
from app.modules.ingestion import router as ingestion_router

client = TestClient(app)


def make_lerobot_v3_zip(
    *,
    version: str = "v3.0",
    unsafe_path: bool = False,
    robot_type: str | None = "test_robot",
) -> bytes:
    """生成不含真实业务数据的最小结构样本。"""

    payload = BytesIO()
    info = {
        "codebase_version": version,
        "robot_type": robot_type,
        "fps": 30,
        "total_episodes": 1,
        "total_frames": 2,
        "features": {
            "observation.state": {
                "dtype": "float32",
                "shape": [2],
                "names": ["joint_1", "joint_2"],
            }
        },
    }
    with ZipFile(payload, "w", ZIP_DEFLATED) as archive:
        archive.writestr("sample/meta/info.json", json.dumps(info))
        archive.writestr("sample/meta/stats.json", "{}")
        archive.writestr("sample/meta/tasks.parquet", b"PAR1-test-tasks")
        archive.writestr(
            "sample/meta/episodes/chunk-000/file-000.parquet",
            b"PAR1-test-episodes",
        )
        archive.writestr("sample/data/chunk-000/file-000.parquet", b"PAR1-test-data")
        if unsafe_path:
            archive.writestr("sample/../escape.txt", "must not escape")
    return payload.getvalue()


def use_temporary_storage(monkeypatch, data_root: Path) -> None:
    monkeypatch.setattr(
        ingestion_router,
        "settings",
        SimpleNamespace(data_root=data_root),
    )
    monkeypatch.setattr(datasets_router, "settings", SimpleNamespace(data_root=data_root))


def test_valid_v3_zip_is_extracted_to_raw(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "测试采集数据", "description": "自动化测试"},
        files={
            "archive": (
                "sample.zip",
                make_lerobot_v3_zip(),
                "application/zip",
            )
        },
    )

    assert response.status_code == 201
    result = response.json()
    assert result["ok"] is True
    assert result["dataset_id"] == "DS000001"
    assert result["summary"]["total_episodes"] == 1
    dataset_root = tmp_path / result["raw_relative_path"]
    assert (dataset_root / "meta" / "info.json").is_file()
    assert (dataset_root / "data" / "chunk-000" / "file-000.parquet").is_file()
    assert list((tmp_path / "system" / "upload-records").glob("*.json"))
    assert DatasetRepository(tmp_path).get("DS000001")["storage_relpath"] == result["storage_name"]
    assert DatasetRepository(tmp_path).get("DS000001")["dataset_family"] == "vla"
    assert not any((tmp_path / "staging" / "uploads").iterdir())


def test_wam_upload_uses_shared_storage_and_qc_state(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "WAM 测试数据", "dataset_family": "wam"},
        files={"archive": ("wam.zip", make_lerobot_v3_zip())},
    )
    assert response.status_code == 201
    row = DatasetRepository(tmp_path).get(response.json()["dataset_id"])
    assert row["dataset_family"] == "wam"
    assert row["availability"] == "unavailable"
    assert row["status_labels"] == ["待质检"]
    assert (tmp_path / "data" / row["id"] / "meta/info.json").is_file()


def make_ego_zip(*, unsafe_path: bool = False, missing_metadata: bool = False,
                 part: str = "part-000") -> bytes:
    payload = BytesIO()
    info = {
        "codebase_version": "v3.0", "robot_type": "human_egocentric", "fps": 30,
        "total_episodes": 2, "total_frames": 20,
        "features": {"observation.images.cam_high": {"dtype": "video", "shape": [3, 1080, 1920]}},
    }
    with ZipFile(payload, "w", ZIP_DEFLATED) as archive:
        if not missing_metadata:
            archive.writestr(f"{part}/meta/info.json", json.dumps(info))
        archive.writestr(f"{part}/meta/stats.json", "{}")
        archive.writestr(f"{part}/meta/tasks.parquet", b"PAR1-test-tasks")
        archive.writestr(f"{part}/meta/episodes/chunk-000/file-000.parquet", b"PAR1-test-episodes")
        archive.writestr(f"{part}/data/chunk-000/file-000.parquet", b"PAR1-test-data")
        archive.writestr(f"{part}/videos/observation.images.cam_high/chunk-000/file-000.mp4",
                         b"\x00\x00\x00\x18ftypisom")
        archive.writestr(f"{part}/provenance/records.json", "{}")
        if unsafe_path:
            archive.writestr("../escape.txt", b"escape")
    return payload.getvalue()


def test_ego_lerobot_part_upload_and_preview(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "EGO part 0", "dataset_family": "ego"},
        files={"archive": ("part-000.zip", make_ego_zip())},
    )
    assert response.status_code == 201
    dataset_id = response.json()["dataset_id"]
    row = DatasetRepository(tmp_path).get(dataset_id)
    assert row["availability"] == "unavailable"
    assert row["status_labels"] == ["待质检"]
    assert row["robot_type"] == "human_egocentric"
    assert row["info_sha256"] and row["manifest_sha256"] is None
    folder = tmp_path / "data" / dataset_id
    video = folder / "videos/observation.images.cam_high/chunk-000/file-000.mp4"
    assert video.read_bytes() == b"\x00\x00\x00\x18ftypisom"
    assert (folder / "provenance/records.json").read_text() == "{}"
    assert not (folder / "manifest.json").exists()
    detail = client.get(f"/data/raw/{dataset_id}")
    assert detail.status_code == 200
    assert "LeRobot v3" in detail.text
    assert "视频预览" in detail.text
    assert "HDF5" not in detail.text
    assert "下载原视频" not in detail.text
    assert client.get(f"/data/raw/{dataset_id}/video/observation.images.cam_high/chunk-000/file-000.mp4").status_code == 404
    converted = []

    def fake_ffmpeg(command, **_kwargs):
        converted.append(command)
        Path(command[-1]).write_bytes(b"browser-preview")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("app.modules.datasets.ego_preview.subprocess.run", fake_ffmpeg)
    preview_url = f"/data/ego/{dataset_id}/preview/observation.images.cam_high/chunk-000/file-000.mp4"
    assert client.get(preview_url).content == b"browser-preview"
    assert client.get(preview_url).content == b"browser-preview"
    assert len(converted) == 1
    assert client.get(f"/data/ego/{dataset_id}/preview/other/chunk-000/file-000.mp4").status_code == 404
    assert video.read_bytes() == b"\x00\x00\x00\x18ftypisom"
    assert client.post(f"/data/api/datasets/{dataset_id}/recheck").status_code == 200
    assert not any((tmp_path / "staging" / "uploads").iterdir())


def test_ego_parts_are_independent_datasets(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    ids = []
    for number in range(2):
        response = client.post(
            "/ingestion/api/uploads",
            data={"dataset_name": f"EGO part {number}", "dataset_family": "ego"},
            files={"archive": (f"part-{number:03d}.zip", make_ego_zip(part=f"part-{number:03d}"))},
        )
        assert response.status_code == 201
        ids.append(response.json()["dataset_id"])
    assert len(set(ids)) == 2
    assert all((tmp_path / "data" / dataset_id / "meta/info.json").is_file() for dataset_id in ids)


def test_existing_egodex_record_remains_readable(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    from app.modules.datasets import service as datasets_service

    repository = DatasetRepository(tmp_path)
    dataset_id = repository.reserve_id()
    folder = tmp_path / "data" / dataset_id
    (folder / "payload" / "task").mkdir(parents=True)
    (folder / "payload" / "task" / "0.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom")
    (folder / "manifest.json").write_text(json.dumps({
        "format": "egodex-v1", "clip_count": 1, "task_count": 1, "file_count": 1,
        "clips": [{"task": "task", "video": "task/0.mp4", "pose": "task/0.hdf5"}],
    }), encoding="utf-8")
    repository.register("data", dataset_id, "历史 EgoDex", "—", None,
                        reserved_id=dataset_id, dataset_family="ego",
                        manifest_digest=datasets_service.manifest_digest(folder))
    page = client.get(f"/data/raw/{dataset_id}")
    assert page.status_code == 200
    assert "任务与片段" in page.text
    assert client.post(f"/data/api/datasets/{dataset_id}/rescan").status_code == 409


def test_ego_chunk_upload_resumes_without_ten_gib_policy(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    monkeypatch.setenv("HKC_MAX_UPLOAD_BYTES", "100")
    monkeypatch.setenv("HKC_MAX_EXPANDED_BYTES", "1")
    archive = make_ego_zip()
    created = client.post("/ingestion/api/ego-sessions", json={
        "filename": "part-000.zip", "dataset_name": "分块 EGO 样例", "description": ""})
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    midpoint = len(archive) // 2
    assert client.put(f"/ingestion/api/ego-sessions/{session_id}/chunks?offset=0",
                      content=archive[:midpoint]).status_code == 200
    assert client.put(f"/ingestion/api/ego-sessions/{session_id}/chunks?offset=0",
                      content=archive[midpoint:]).status_code == 409
    assert client.put(f"/ingestion/api/ego-sessions/{session_id}/chunks?offset={midpoint}",
                      content=archive[midpoint:]).status_code == 200
    completed = client.post(f"/ingestion/api/ego-sessions/{session_id}/complete")
    assert completed.status_code == 201
    assert DatasetRepository(tmp_path).get(completed.json()["dataset_id"])["status_labels"] == ["待质检"]


def test_ego_rejects_old_or_unsafe_archive_without_publishing(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    for archive in (make_ego_zip(unsafe_path=True), make_ego_zip(missing_metadata=True)):
        response = client.post(
            "/ingestion/api/uploads",
            data={"dataset_name": "危险 EGO", "dataset_family": "ego"},
            files={"archive": ("bad.zip", archive)},
        )
        assert response.status_code == 400
    old = BytesIO()
    with ZipFile(old, "w", ZIP_DEFLATED) as archive:
        archive.writestr("ego/session/0.mp4", b"\x00\x00\x00\x18ftypisom")
        archive.writestr("ego/session/0.hdf5", b"\x89HDF\r\n\x1a\n")
    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "旧格式 EGO", "dataset_family": "ego"},
        files={"archive": ("old.zip", old.getvalue())},
    )
    assert response.status_code == 400
    assert DatasetRepository(tmp_path).list() == []
    assert not (tmp_path / "escape.txt").exists()
    assert not any((tmp_path / "staging" / "uploads").iterdir())


def test_clear_upload_records_preserves_dataset(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "保留数据", "dataset_family": "ego"},
        files={"archive": ("ego.zip", make_ego_zip())},
    )
    dataset_id = response.json()["dataset_id"]
    cleared = client.delete("/ingestion/api/uploads/records")
    assert cleared.status_code == 200
    assert cleared.json() == {"deleted": 1}
    assert not list((tmp_path / "system" / "upload-records").glob("*.json"))
    assert DatasetRepository(tmp_path).get(dataset_id) is not None
    assert (tmp_path / "data" / dataset_id / "videos/observation.images.cam_high/chunk-000/file-000.mp4").is_file()


def test_upload_page_offers_ego_and_history_clear(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "页面样例", "dataset_family": "ego"},
        files={"archive": ("ego.zip", make_ego_zip())},
    )
    assert response.status_code == 201
    page = client.get("/ingestion/")
    assert page.status_code == 200
    assert 'value="ego"' in page.text
    assert 'id="clear-upload-records"' in page.text
    assert ">全部删除</button>" in page.text
    assert 'class="refresh-link"' not in page.text
    assert "RECENT ACTIVITY" not in page.text
    assert "storage/raw/" not in page.text
    assert client.delete("/ingestion/api/uploads/records").status_code == 200
    empty_page = client.get("/ingestion/")
    assert 'id="clear-upload-records"' in empty_page.text
    assert ">全部删除</button>" in empty_page.text


def test_clear_upload_records_refuses_active_upload(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    folder = tmp_path / "system" / "upload-records"
    folder.mkdir(parents=True)
    (folder / "up-active.json").write_text('{"status":"receiving"}', encoding="utf-8")
    response = client.delete("/ingestion/api/uploads/records")
    assert response.status_code == 409
    assert (folder / "up-active.json").exists()


def test_ego_upload_rejects_duplicate_name_without_changing_source(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    first = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "Ego 样例", "dataset_family": "ego"},
        files={"archive": ("ego.zip", make_ego_zip())},
    )
    second = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": " Ego 样例 ", "dataset_family": "ego"},
        files={"archive": ("ego.zip", make_ego_zip())},
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert len(DatasetRepository(tmp_path).list()) == 1
    assert (tmp_path / "data" / first.json()["dataset_id"] / "videos/observation.images.cam_high/chunk-000/file-000.mp4").read_bytes() == b"\x00\x00\x00\x18ftypisom"
    assert not any((tmp_path / "staging" / "uploads").iterdir())


def test_upload_rejects_unknown_dataset_family(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "错误类别", "dataset_family": "other"},
        files={"archive": ("sample.zip", make_lerobot_v3_zip())},
    )
    assert response.status_code == 400
    assert DatasetRepository(tmp_path).list() == []


def test_upload_rejects_duplicate_dataset_display_name(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)
    first = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "重复数据集"},
        files={"archive": ("first.zip", make_lerobot_v3_zip())},
    )
    second = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "  重复数据集  "},
        files={"archive": ("second.zip", make_lerobot_v3_zip())},
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "dataset_name_conflict"
    assert "数据集名称已存在" in second.json()["message"]
    assert [row["name"] for row in DatasetRepository(tmp_path).list()] == ["重复数据集"]


def test_non_v3_archive_is_rejected(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "错误版本数据"},
        files={"archive": ("sample.zip", make_lerobot_v3_zip(version="v2.1"))},
    )

    assert response.status_code == 400
    assert response.json()["status"] == "rejected"
    assert "v3.0" in response.json()["message"]
    assert not (tmp_path / "raw").exists()


def test_v3_without_robot_type_requires_manual_declaration(
    monkeypatch, tmp_path: Path
) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "未声明机器人数据"},
        files={"archive": ("sample.zip", make_lerobot_v3_zip(robot_type=None))},
    )

    assert response.status_code == 400
    assert "手动填写机器人类型" in response.json()["message"]
    assert DatasetRepository(tmp_path).list() == []


def test_manual_robot_type_fills_missing_info_json(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "手动声明机器人数据", "robot_type": "so100"},
        files={"archive": ("sample.zip", make_lerobot_v3_zip(robot_type=None))},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["summary"]["robot_type"] == "so100"
    assert result["summary"]["original_robot_type"] is None
    assert result["summary"]["robot_type_source"] == "upload_form"
    dataset_root = tmp_path / result["raw_relative_path"]
    info = json.loads((dataset_root / "meta" / "info.json").read_text(encoding="utf-8"))
    assert info["robot_type"] == "so100"
    assert DatasetRepository(tmp_path).get(result["dataset_id"])["robot_type"] == "so100"


def test_manual_robot_type_overrides_archive_value(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "覆盖机器人类型", "robot_type": "aloha"},
        files={"archive": ("sample.zip", make_lerobot_v3_zip(robot_type="so100"))},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["summary"]["robot_type"] == "aloha"
    assert result["summary"]["original_robot_type"] == "so100"
    assert result["summary"]["robot_type_source"] == "upload_form"
    dataset_root = tmp_path / result["raw_relative_path"]
    info = json.loads((dataset_root / "meta" / "info.json").read_text(encoding="utf-8"))
    assert info["robot_type"] == "aloha"


def test_existing_robot_type_is_used_when_form_is_empty(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "沿用包内机器人类型", "robot_type": ""},
        files={"archive": ("sample.zip", make_lerobot_v3_zip(robot_type="so100"))},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["summary"]["robot_type"] == "so100"
    assert result["summary"]["original_robot_type"] == "so100"
    assert result["summary"]["robot_type_source"] == "meta/info.json"


def test_zip_path_traversal_is_rejected(monkeypatch, tmp_path: Path) -> None:
    use_temporary_storage(monkeypatch, tmp_path)

    response = client.post(
        "/ingestion/api/uploads",
        data={"dataset_name": "危险路径数据"},
        files={
            "archive": (
                "sample.zip",
                make_lerobot_v3_zip(unsafe_path=True),
                "application/zip",
            )
        },
    )

    assert response.status_code == 400
    assert response.json()["status"] == "rejected"
    assert "../" in response.json()["message"]
    assert not (tmp_path / "escape.txt").exists()
