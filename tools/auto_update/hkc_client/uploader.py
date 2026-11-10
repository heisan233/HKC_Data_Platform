"""HTTP and streaming-upload client for one-shot collection runs."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .errors import CollectorError, FatalCollectorError, TransientCollectorError

T = TypeVar("T")


def message(payload: dict) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(payload.get("message") or detail or payload)


def _classify_http(status: int, payload: dict, context: str) -> CollectorError:
    text = message(payload)
    code = str(payload.get("code") or "")
    if status in {401, 403} or code in {"invalid_robot_token", "robot_inactive"}:
        return FatalCollectorError(f"{context}：{text}")
    if status >= 500 or status in {408, 429}:
        return TransientCollectorError(f"{context}：HTTP {status}，{text}")
    return CollectorError(f"{context}：{text}")


def request_json(
    method: str,
    url: str,
    token: str,
    payload: dict | None = None,
    *,
    timeout: int = 30,
) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise TransientCollectorError(f"无法连接平台：{reason}") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        error = (
            TransientCollectorError
            if status >= 500
            else CollectorError
        )
        raise error(f"平台返回无法解析的响应（HTTP {status}）") from exc
    return status, result


def with_retry(operation: Callable[[], T], attempts: int, label: str) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except TransientCollectorError as exc:
            if attempt >= attempts:
                raise CollectorError(f"{label}重试 {attempts} 次后仍失败：{exc}") from exc
            print(f"  {label}暂时失败（{attempt}/{attempts}）：{exc}；2 秒后重试……")
            time.sleep(2)
    raise AssertionError("retry loop did not return")


class CollectorClient:
    def __init__(self, server: str, token: str, attempts: int = 3):
        self.server = server.rstrip("/")
        self.token = token
        self.attempts = attempts

    def profile(self) -> dict:
        def operation() -> dict:
            status, payload = request_json(
                "GET", f"{self.server}/api/v1/collector/profile", self.token
            )
            if status != 200 or not payload.get("ok"):
                raise _classify_http(status, payload, "Token 验证失败")
            return payload["robot"]

        return with_retry(operation, self.attempts, "Token 验证")

    def preflight(
        self,
        archive: Path,
        digest: str,
        robot_type: str,
    ) -> dict:
        body = {
            "archive_sha256": digest,
            "archive_size": archive.stat().st_size,
            "filename": archive.name,
            "archive_robot_type": robot_type,
            "robot_type_confirmation": None,
        }

        def operation() -> dict:
            status, payload = request_json(
                "POST",
                f"{self.server}/api/v1/collector/uploads/preflight",
                self.token,
                body,
            )
            if status == 200 and payload.get("action") in {"upload", "existing"}:
                return payload
            raise _classify_http(status, payload, "上传前检查失败")

        return with_retry(operation, self.attempts, "上传前检查")

    def upload(
        self,
        archive: Path,
        digest: str,
        robot_type: str,
        dataset_name: str,
    ) -> dict:
        if shutil.which("curl") is None:
            raise FatalCollectorError("未找到 curl，请先安装 curl")

        def operation() -> dict:
            command = [
                "curl", "--config", "-", "--progress-bar", "--show-error",
                "--connect-timeout", "30", "--max-time", "0",
                "--write-out", "\n%{http_code}", "--request", "POST",
                f"{self.server}/api/v1/collector/uploads",
                "--form", f"archive=@{archive};type=application/zip",
                "--form", f"archive_sha256={digest}",
                "--form", f"archive_size={archive.stat().st_size}",
                "--form", f"archive_robot_type={robot_type}",
                "--form", "robot_type_confirmation=",
                "--form", f"dataset_name={dataset_name}",
            ]
            completed = subprocess.run(
                command,
                input=f'header = "Authorization: Bearer {self.token}"\n',
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            if completed.returncode:
                raise TransientCollectorError(
                    f"curl 上传失败（退出码 {completed.returncode}）"
                )
            body, separator, status_text = completed.stdout.rpartition("\n")
            if not separator or not status_text.isdigit():
                raise TransientCollectorError("无法识别平台上传响应")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise TransientCollectorError(
                    f"平台返回无法解析的上传响应（HTTP {status_text}）"
                ) from exc
            status = int(status_text)
            if status in {200, 201} and payload.get("ok"):
                return payload
            raise _classify_http(status, payload, "上传失败")

        return with_retry(operation, self.attempts, "上传")
