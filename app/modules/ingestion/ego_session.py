"""Resumable browser transfer for large LeRobot v3 EGO ZIP archives."""

from __future__ import annotations

import fcntl
import json
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.modules.ingestion.service import (
    MIB,
    UploadValidationError,
    _display_filename,
    _validate_form_text,
    receive_dataset_upload,
)

MAX_CHUNK_BYTES = 16 * MIB
SESSION_ID = re.compile(r"^ego-[0-9a-f]{32}$")


def _folder(root: Path, session_id: str) -> Path:
    if not SESSION_ID.fullmatch(session_id):
        raise UploadValidationError("EGO 上传会话编号无效。", status_code=404)
    folder = root / "staging" / "ego-sessions" / session_id
    if folder.is_symlink() or not folder.is_dir():
        raise UploadValidationError("EGO 上传会话不存在。", status_code=404)
    return folder


@contextmanager
def _locked(folder: Path):
    with (folder / "lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _state(folder: Path) -> dict:
    return json.loads((folder / "session.json").read_text(encoding="utf-8"))


def create(root: Path, *, filename: str, dataset_name: str,
           description: str, robot_type: str = "") -> dict:
    clean_name, clean_description, _ = _validate_form_text(dataset_name, description, robot_type)
    clean_filename = _display_filename(filename)
    parent = root / "staging" / "ego-sessions"
    if root.is_symlink() or parent.is_symlink():
        raise UploadValidationError("上传临时目录不安全。")
    parent.mkdir(parents=True, exist_ok=True)
    for old in parent.iterdir():
        if not SESSION_ID.fullmatch(old.name) or old.is_symlink() or not old.is_dir():
            continue
        archive = old / "archive.zip"
        if not archive.is_file() or time.time() - archive.stat().st_mtime < 7 * 24 * 3600:
            continue
        try:
            with (old / "lock").open("a+b") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                shutil.rmtree(old)
        except OSError:
            continue
    session_id = f"ego-{uuid4().hex}"
    folder = parent / session_id
    folder.mkdir()
    state = {"id": session_id, "filename": clean_filename, "dataset_name": clean_name,
             "description": clean_description, "complete": False}
    (folder / "session.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    (folder / "archive.zip").touch()
    return {"session_id": session_id, "received_bytes": 0}


def status(root: Path, session_id: str) -> dict:
    folder = _folder(root, session_id)
    with _locked(folder):
        state = _state(folder)
        return {"session_id": session_id, "received_bytes": (folder / "archive.zip").stat().st_size,
                "complete": state["complete"], "filename": state["filename"]}


def append(root: Path, session_id: str, offset: int, chunk: bytes) -> dict:
    if not chunk or len(chunk) > MAX_CHUNK_BYTES:
        raise UploadValidationError("EGO 分块须为 1–16 MiB。")
    folder = _folder(root, session_id)
    with _locked(folder):
        state = _state(folder)
        archive = folder / "archive.zip"
        actual = archive.stat().st_size
        if state["complete"] or offset != actual:
            raise UploadValidationError("上传位置已变化，请读取会话进度后重试。", status_code=409)
        if shutil.disk_usage(folder).free < len(chunk) + 512 * MIB:
            raise UploadValidationError("服务器剩余空间不足，无法继续接收 EGO 数据集。")
        with archive.open("ab") as target:
            target.write(chunk)
        return {"session_id": session_id, "received_bytes": actual + len(chunk)}


async def finish(root: Path, session_id: str):
    folder = _folder(root, session_id)
    with _locked(folder):
        state = _state(folder)
        if state["complete"]:
            raise UploadValidationError("上传会话已结束。", status_code=409)
        state["complete"] = True
        (folder / "session.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    try:
        return await receive_dataset_upload(
            data_root=root, upload=None, source_archive=folder / "archive.zip",
            source_filename=state["filename"], dataset_name=state["dataset_name"],
            description=state["description"], dataset_family="ego",
        )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
