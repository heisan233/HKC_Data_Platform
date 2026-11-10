"""Replace a robot collector token and print the new full value."""

import argparse

from app.core.config import settings
from app.db.robot_tokens import RobotTokenRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重新生成机器人采集上传 Token")
    parser.add_argument("--robot-id", required=True, help="机器人编号，例如 RB000001")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args(argv)
    if not args.yes:
        answer = input(
            f"确认重新生成 {args.robot_id} 的 Token？旧 Token 会立即失效。[y/N] "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消")
            return 1
    try:
        token = RobotTokenRepository(settings.data_root).regenerate(args.robot_id)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"机器人：{token['robot_id']}")
    print(f"新 Token：{token['token']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
