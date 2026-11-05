"""Interactively reset a user password and require changing it at next login."""

import argparse
import getpass

from app.core.config import settings
from app.db.users import UserRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重置 HKC 数采平台用户密码")
    parser.add_argument("--username", required=True, help="要重置密码的用户名")
    args = parser.parse_args(argv)

    password = getpass.getpass("新的初始密码：")
    confirmation = getpass.getpass("再次输入新的初始密码：")
    if password != confirmation:
        parser.error("两次输入的密码不一致")

    try:
        user = UserRepository(settings.data_root).reset_password(args.username, password)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"密码已重置：{user['username']}（下次登录必须修改）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
