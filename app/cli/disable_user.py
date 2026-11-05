"""Disable a platform user and revoke all of that user's sessions."""

import argparse

from app.core.config import settings
from app.db.users import UserRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="停用 HKC 数采平台用户")
    parser.add_argument("--username", required=True, help="要停用的用户名")
    args = parser.parse_args(argv)
    try:
        user = UserRepository(settings.data_root).set_active(args.username, False)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"用户已停用并注销全部会话：{user['username']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
