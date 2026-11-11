#!/usr/bin/env python3
"""Clear local upload markers so datasets can be submitted again in tests."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

if __package__:
    from .hkc_client.marker import clear_markers
else:
    from hkc_client.marker import clear_markers

SCRIPT_DIR = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只清除本地 HKC 上传标记，不删除数据集文件",
    )
    parser.add_argument(
        "scan_dir",
        nargs="?",
        type=Path,
        default=Path(os.getenv("HKC_AUTO_SCAN_DIR", SCRIPT_DIR / "datasets")),
        help="要清理的扫描目录；默认使用 HKC_AUTO_SCAN_DIR 或 tools/auto_update/datasets",
    )
    args = parser.parse_args()
    try:
        removed = clear_markers(args.scan_dir)
    except (OSError, ValueError) as exc:
        print(f"清理失败：{exc}")
        return 2
    for path in removed:
        print(f"[已删除标记] {path}")
    print(f"清理完成：共删除 {len(removed)} 个上传标记；数据集文件未修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
