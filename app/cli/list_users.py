"""List platform accounts without exposing authentication secrets."""

from app.core.config import settings
from app.db.users import UserRepository


def main() -> int:
    users = UserRepository(settings.data_root).list_users()
    if not users:
        print("尚未创建用户")
        return 0

    print("ID\t用户名\t状态\t创建时间\t最近登录")
    for user in users:
        status = "启用" if user["is_active"] else "停用"
        last_login = user["last_login_at"] or "从未登录"
        print(
            f"{user['id']}\t{user['username']}\t{status}\t"
            f"{user['created_at']}\t{last_login}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
