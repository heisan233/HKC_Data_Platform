"""Local-only tests for the one-shot collector client."""

import json
from pathlib import Path
from zipfile import ZipFile

from tools.auto_update.hkc_client.marker import matches, read_marker, write_marker
from tools.auto_update.hkc_client.packer import package_folder
from tools.auto_update.hkc_client.scanner import scan_directory
from tools.auto_update.upload_once import Counts, run_one


def make_dataset(path: Path, robot_type: str = "so100") -> Path:
    (path / "meta").mkdir(parents=True)
    (path / "data" / "chunk-000").mkdir(parents=True)
    (path / "meta" / "info.json").write_text(
        json.dumps({"codebase_version": "v3.0", "robot_type": robot_type}),
        encoding="utf-8",
    )
    (path / "data" / "chunk-000" / "file-000.parquet").write_bytes(b"PAR1")
    return path


def test_scanner_only_returns_immediate_folders_and_zip_files(tmp_path: Path) -> None:
    folder = make_dataset(tmp_path / "folder")
    archive = tmp_path / "archive.ZIP"
    archive.write_bytes(b"zip")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()

    assert scan_directory(tmp_path) == [archive.resolve(), folder.resolve()]


def test_marker_matches_only_same_server_and_robot(tmp_path: Path) -> None:
    source = make_dataset(tmp_path / "dataset")
    path = write_marker(source, "DS000123", "RB000001", "http://localhost:18001/")
    value = read_marker(source)

    assert path == source / ".hkc-uploaded.json"
    assert matches(value, "http://localhost:18001", "RB000001") is True
    assert matches(value, "http://localhost:18002", "RB000001") is False
    assert matches(value, "http://localhost:18001", "RB000002") is False


def test_folder_packaging_excludes_upload_marker(tmp_path: Path) -> None:
    source = make_dataset(tmp_path / "dataset")
    write_marker(source, "DS000123", "RB000001", "http://localhost:18001")

    archive = package_folder(source, tmp_path / "temporary")

    with ZipFile(archive) as packaged:
        names = packaged.namelist()
    assert "dataset/meta/info.json" in names
    assert "dataset/data/chunk-000/file-000.parquet" in names
    assert all(not name.endswith(".hkc-uploaded.json") for name in names)


class FakeClient:
    server = "http://localhost:18001"

    def __init__(self, action: str):
        self.action = action
        self.upload_calls = 0

    def preflight(self, _archive, _digest, _robot_type):
        if self.action == "existing":
            return {"action": "existing", "dataset_id": "DS000009"}
        return {"action": "upload"}

    def upload(self, _archive, _digest, _robot_type, _dataset_name):
        self.upload_calls += 1
        return {"ok": True, "action": "created", "dataset_id": "DS000010"}


def test_existing_dataset_writes_marker_without_uploading_again(tmp_path: Path) -> None:
    source = make_dataset(tmp_path / "dataset")
    client = FakeClient("existing")
    counts = Counts(found=1)

    run_one(
        source,
        client,
        {"id": "RB000001", "robot_type": "so100"},
        tmp_path / "temporary",
        counts,
    )

    assert client.upload_calls == 0
    assert counts.duplicate == 1
    assert read_marker(source)["dataset_id"] == "DS000009"
    assert list((tmp_path / "temporary").glob("*.zip")) == []


def test_new_folder_upload_writes_marker_and_removes_temporary_zip(tmp_path: Path) -> None:
    source = make_dataset(tmp_path / "dataset")
    client = FakeClient("upload")
    counts = Counts(found=1)

    run_one(
        source,
        client,
        {"id": "RB000001", "robot_type": "so100"},
        tmp_path / "temporary",
        counts,
    )

    assert client.upload_calls == 1
    assert counts.uploaded == 1
    assert read_marker(source)["dataset_id"] == "DS000010"
    assert list((tmp_path / "temporary").glob("*.zip")) == []
