#!/usr/bin/env python3
"""Scan or accept LeRobot v3 datasets, upload them sequentially, then exit."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .hkc_client.errors import CollectorError, FatalCollectorError
    from .hkc_client.marker import matches, read_marker, write_marker
    from .hkc_client.packer import package_folder, sha256
    from .hkc_client.scanner import normalize_sources, scan_directory
    from .hkc_client.uploader import CollectorClient
    from .hkc_client.validator import dataset_info, require_matching_robot_type
else:
    from hkc_client.errors import CollectorError, FatalCollectorError
    from hkc_client.marker import matches, read_marker, write_marker
    from hkc_client.packer import package_folder, sha256
    from hkc_client.scanner import normalize_sources, scan_directory
    from hkc_client.uploader import CollectorClient
    from hkc_client.validator import dataset_info, require_matching_robot_type

SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass
class Counts:
    found: int = 0
    uploaded: int = 0
    duplicate: int = 0
    skipped: int = 0
    failed: int = 0


def read_token(token_file: str) -> str:
    if token_file:
        try:
            value = Path(token_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise FatalCollectorError(f"无法读取 Token 文件：{exc}") from exc
    else:
        value = os.getenv("HKC_ROBOT_TOKEN", "")
    token = value.strip()
    if not token:
        raise FatalCollectorError("请使用 --token-file 或 HKC_ROBOT_TOKEN 提供机器人 Token")
    return token


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="一次扫描并上传 LeRobot v3 文件夹或 ZIP，然后退出",
    )
    value.add_argument(
        "sources",
        nargs="*",
        type=Path,
        help="直接指定一个或多个数据集文件夹/.zip；省略时扫描 --scan-dir",
    )
    value.add_argument(
        "--scan-dir",
        type=Path,
        default=Path(os.getenv("HKC_AUTO_SCAN_DIR", SCRIPT_DIR / "datasets")),
        help="父目录；每个直接子文件夹或 .zip 视为独立数据集",
    )
    value.add_argument("--server", default=os.getenv("HKC_SERVER", ""))
    value.add_argument("--token-file", default=os.getenv("HKC_TOKEN_FILE", ""))
    value.add_argument(
        "--retries",
        type=int,
        default=int(os.getenv("HKC_RETRY_ATTEMPTS", "3")),
        help="网络错误或 5xx 的总尝试次数，默认 3",
    )
    return value


def run_one(
    source: Path,
    client: CollectorClient,
    robot: dict,
    temporary_dir: Path,
    counts: Counts,
) -> None:
    existing_marker = read_marker(source)
    if matches(existing_marker, client.server, robot["id"]):
        counts.skipped += 1
        print(f"[跳过] {source.name}：已上传为 {existing_marker['dataset_id']}")
        return

    archive = source
    generated_archive = False
    try:
        info = dataset_info(source)
        robot_type = require_matching_robot_type(
            info, source, str(robot["robot_type"]),
        )
        if source.is_dir():
            print(f"[打包] {source.name}")
            archive = package_folder(source, temporary_dir)
            generated_archive = True
        print(f"[检查] {source.name}")
        digest = sha256(archive)
        preflight = client.preflight(archive, digest, robot_type)
        if preflight.get("action") == "existing":
            dataset_id = str(preflight["dataset_id"])
            write_marker(source, dataset_id, robot["id"], client.server)
            counts.duplicate += 1
            print(f"[已存在] {source.name}：{dataset_id}，已写入上传标记")
            return

        print(f"[上传] {source.name}（{archive.stat().st_size} 字节）")
        result = client.upload(archive, digest, robot_type, source.stem)
        dataset_id = str(result["dataset_id"])
        write_marker(source, dataset_id, robot["id"], client.server)
        if result.get("action") == "existing":
            counts.duplicate += 1
            print(f"[已存在] {source.name}：{dataset_id}，已写入上传标记")
        else:
            counts.uploaded += 1
            print(f"[成功] {source.name} → {dataset_id}")
    finally:
        if generated_archive:
            archive.unlink(missing_ok=True)


def main() -> int:
    args = parser().parse_args()
    if not args.server.strip():
        parser().error("请使用 --server 或 HKC_SERVER 提供平台地址")
    if not 1 <= args.retries <= 10:
        parser().error("--retries 必须在 1 到 10 之间")
    try:
        token = read_token(args.token_file)
        client = CollectorClient(args.server, token, args.retries)
        robot = client.profile()
        print(f"当前机器人：{robot['id']} / {robot['name']} / {robot['robot_type']}")
        sources = (
            normalize_sources(args.sources)
            if args.sources
            else scan_directory(args.scan_dir.expanduser().resolve())
        )
    except (CollectorError, ValueError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    counts = Counts(found=len(sources))
    if not sources:
        print("没有发现待处理的数据集。")
        return 0

    for index, source in enumerate(sources, start=1):
        print(f"\n=== [{index}/{len(sources)}] {source.name} ===")
        try:
            run_one(source, client, robot, SCRIPT_DIR / "temporary", counts)
        except FatalCollectorError as exc:
            counts.failed += 1
            print(f"[终止] {source.name}：{exc}", file=sys.stderr)
            print("Token 无效或机器人已停用，剩余队列未处理。", file=sys.stderr)
            break
        except CollectorError as exc:
            counts.failed += 1
            print(f"[失败] {source.name}：{exc}", file=sys.stderr)
        except OSError as exc:
            counts.failed += 1
            print(f"[失败] {source.name}：本地文件操作失败：{exc}", file=sys.stderr)

    print("\n=== 本次上传汇总 ===")
    print(
        f"发现 {counts.found}；新上传 {counts.uploaded}；平台已存在 {counts.duplicate}；"
        f"标记跳过 {counts.skipped}；失败 {counts.failed}"
    )
    return 1 if counts.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
