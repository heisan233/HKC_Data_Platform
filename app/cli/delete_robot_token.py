"""Delete a robot collector token after an explicit terminal confirmation."""

import argparse

from app.core.config import settings
from app.db.robot_tokens import RobotTokenRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="删除机器人的采集上传 Token")
    parser.add_argument("--robot-id", required=True, help="机器人编号，例如 RB000001")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args(argv)
    if not args.yes:
        answer = input(
            f"确认删除 {args.robot_id} 的 Token？删除后采集端会立即无法上传。[y/N] "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消")
            return 1
    try:
        deleted = RobotTokenRepository(settings.data_root).delete(args.robot_id)
    except ValueError as exc:
        parser.error(str(exc))
    if not deleted:
        parser.error("该机器人尚未配置上传 Token")
    print(f"已删除 {args.robot_id} 的 Token")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
