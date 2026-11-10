"""Display a robot's full collector token from the deployment terminal."""

import argparse

from app.core.config import settings
from app.db.robot_tokens import RobotTokenRepository
from app.db.robots import RobotRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="查看机器人的完整采集上传 Token")
    parser.add_argument("--robot-id", required=True, help="机器人编号，例如 RB000001")
    args = parser.parse_args(argv)
    if RobotRepository(settings.data_root).get(args.robot_id) is None:
        parser.error("机器人不存在")
    token = RobotTokenRepository(settings.data_root).get(args.robot_id, reveal=True)
    if token is None:
        parser.error("该机器人尚未配置上传 Token")
    print(f"机器人：{token['robot_id']}")
    print(f"Token：{token['token']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
