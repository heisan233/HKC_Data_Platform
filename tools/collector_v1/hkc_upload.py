#!/usr/bin/env python3
"""Upload one complete LeRobot v3 ZIP to HKC Data Platform.

Only Python's standard library is required. ``curl`` performs the actual
multipart transfer so multi-gigabyte archives are streamed from disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

CHUNK_SIZE = 8 * 1024 * 1024


def _token(args: argparse.Namespace) -> str:
    if args.token_file:
        try:
            value = Path(args.token_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"无法读取 Token 文件：{exc}") from exc
    else:
        value = os.getenv("HKC_ROBOT_TOKEN", "")
    token = value.strip()
    if not token:
        raise SystemExit("请使用 --token-file 或 HKC_ROBOT_TOKEN 提供机器人 Token。")
    return token


def _request_json(
    method: str,
    url: str,
    token: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    except urllib.error.URLError as exc:
        raise SystemExit(f"无法连接平台：{exc.reason}") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"平台返回了无法解析的响应（HTTP {status}）。") from exc
    return status, result


def _dataset_info(archive_path: Path) -> dict:
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"无法读取 ZIP：{exc}") from exc
    with archive:
        candidates = []
        for name in archive.namelist():
            path = PurePosixPath(name.replace("\\", "/"))
            if path.parts[-2:] == ("meta", "info.json"):
                candidates.append(name)
        if len(candidates) != 1:
            raise SystemExit("ZIP 必须且只能包含一个 meta/info.json。")
        try:
            info = json.loads(archive.read(candidates[0]).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SystemExit("meta/info.json 不是有效的 UTF-8 JSON。") from exc
    if not isinstance(info, dict):
        raise SystemExit("meta/info.json 顶层必须是 JSON 对象。")
    return info


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    completed = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            completed += len(chunk)
            percent = completed * 100 / total
            print(f"\r计算 SHA-256：{percent:5.1f}%", end="", flush=True)
    print()
    return digest.hexdigest()


def _yes(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().casefold() in {"y", "yes"}


def _message(payload: dict) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(payload.get("message") or detail or payload)


def _upload_with_curl(
    server: str,
    token: str,
    archive: Path,
    digest: str,
    robot_type: str,
    confirmation: str,
    dataset_name: str,
    description: str,
) -> tuple[int, dict]:
    if shutil.which("curl") is None:
        raise SystemExit("未找到 curl，请先安装 curl。")
    command = [
        "curl",
        "--config",
        "-",
        "--progress-bar",
        "--show-error",
        "--write-out",
        "\n%{http_code}",
        "--request",
        "POST",
        f"{server}/api/v1/collector/uploads",
        "--form",
        f"archive=@{archive};type=application/zip",
        "--form",
        f"archive_sha256={digest}",
        "--form",
        f"archive_size={archive.stat().st_size}",
        "--form",
        f"archive_robot_type={robot_type}",
        "--form",
        f"robot_type_confirmation={confirmation}",
    ]
    if dataset_name:
        command.extend(("--form", f"dataset_name={dataset_name}"))
    if description:
        command.extend(("--form", f"description={description}"))
    completed = subprocess.run(
        command,
        input=f'header = "Authorization: Bearer {token}"\n',
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise SystemExit(f"curl 上传失败，退出码 {completed.returncode}。")
    body, separator, status_text = completed.stdout.rpartition("\n")
    if not separator or not status_text.isdigit():
        raise SystemExit("无法识别平台上传响应。")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"平台返回了无法解析的响应（HTTP {status_text}）。") from exc
    return int(status_text), payload


def main() -> int:
    parser = argparse.ArgumentParser(description="上传一个完整 LeRobot v3 ZIP")
    parser.add_argument("archive", type=Path, help="待上传的 .zip 文件")
    parser.add_argument(
        "--server",
        default=os.getenv("HKC_SERVER", ""),
        help="平台地址；也可使用 HKC_SERVER",
    )
    parser.add_argument(
        "--token-file",
        default=os.getenv("HKC_TOKEN_FILE", ""),
        help="完整 Token 文件；也可使用 HKC_TOKEN_FILE",
    )
    parser.add_argument("--name", default="", help="数据集显示名称；省略时使用 ZIP 文件名")
    parser.add_argument("--description", default="", help="上传备注")
    args = parser.parse_args()

    archive = args.archive.expanduser().resolve()
    if not archive.is_file() or archive.suffix.casefold() != ".zip":
        parser.error("archive 必须是存在的 .zip 文件")
    if not args.server.strip():
        parser.error("请使用 --server 或 HKC_SERVER 提供平台地址")
    server = args.server.rstrip("/")
    token = _token(args)

    status, profile = _request_json("GET", f"{server}/api/v1/collector/profile", token)
    if status != 200 or not profile.get("ok"):
        raise SystemExit(f"Token 验证失败：{_message(profile)}")
    robot = profile["robot"]
    print(f"当前机器人：{robot['id']} / {robot['name']} / {robot['robot_type']}")

    info = _dataset_info(archive)
    archive_robot_type = str(info.get("robot_type") or "").strip()
    confirmation = ""
    if archive_robot_type:
        if archive_robot_type.casefold() != robot["robot_type"].strip().casefold():
            raise SystemExit(
                "机器人类型不一致："
                f"ZIP={archive_robot_type}，Token={robot['robot_type']}。"
            )
    else:
        if not _yes(
            "ZIP 缺少 robot_type，是否确认该数据属于 "
            f"{robot['id']}（类型 {robot['robot_type']}）？"
        ):
            print("已取消。")
            return 1
        confirmation = robot["robot_type"]

    digest = _sha256(archive)
    preflight_payload = {
        "archive_sha256": digest,
        "archive_size": archive.stat().st_size,
        "filename": archive.name,
        "archive_robot_type": archive_robot_type or None,
        "robot_type_confirmation": confirmation or None,
    }
    status, preflight = _request_json(
        "POST", f"{server}/api/v1/collector/uploads/preflight", token, preflight_payload
    )
    if status == 200 and preflight.get("action") == "existing":
        print(preflight["message"])
        return 0
    if preflight.get("code") == "duplicate_rebind_confirmation_required":
        print(_message(preflight))
        if not _yes(
            f"是否确认 {preflight['dataset_id']} 实际属于当前机器人 {robot['id']}？"
        ):
            print("已取消；已有数据集及关联关系未改变。")
            return 1
        rebind_status, rebound = _request_json(
            "POST",
            f"{server}/api/v1/collector/uploads/rebind",
            token,
            {
                "archive_sha256": digest,
                "dataset_id": preflight["dataset_id"],
                "confirm": True,
            },
        )
        if rebind_status != 200:
            raise SystemExit(f"改绑失败：{_message(rebound)}")
        print(rebound["message"])
        return 0
    if status != 200 or preflight.get("action") != "upload":
        raise SystemExit(f"上传前检查失败：{_message(preflight)}")

    print(f"开始上传 {archive.name}（{archive.stat().st_size} 字节）……")
    upload_status, result = _upload_with_curl(
        server,
        token,
        archive,
        digest,
        archive_robot_type,
        confirmation,
        args.name,
        args.description,
    )
    if upload_status not in {200, 201} or not result.get("ok"):
        raise SystemExit(f"上传失败：{_message(result)}")
    print(f"上传成功：{result['dataset_id']}（采集机器人 {robot['id']}）")
    print("状态：不可用、待质检；平台不会自动启动质检。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
