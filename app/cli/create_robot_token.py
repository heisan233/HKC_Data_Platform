"""Create the upload token missing from an existing robot record."""

import argparse

from app.core.config import settings
from app.db.robot_tokens import RobotTokenRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="为已有机器人生成采集上传 Token")
    parser.add_argument("--robot-id", required=True, help="机器人编号，例如 RB000001")
    args = parser.parse_args(argv)
    try:
        token = RobotTokenRepository(settings.data_root).create(args.robot_id)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"机器人：{token['robot_id']}")
    print(f"Token：{token['token']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
