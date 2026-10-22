"""Interactively create a platform user without exposing a password in shell history."""

import argparse
import getpass

from app.core.config import settings
from app.db.users import UserRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创建 HKC 数采平台用户")
    parser.add_argument("--username", help="用户名；省略时交互输入")
    args = parser.parse_args(argv)

    username = args.username or input("用户名：").strip()
    password = getpass.getpass("密码：")
    confirmation = getpass.getpass("再次输入密码：")
    if password != confirmation:
        parser.error("两次输入的密码不一致")

    try:
        user = UserRepository(settings.data_root).create_user(username, password)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"用户已创建：{user['username']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
