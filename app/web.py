"""多个页面共用的 HTML 模板加载器。

Jinja2 会读取 ``app/templates`` 中的 HTML，并把路由传入的数据填进模板。
例如模板中的 ``{{ datasets }}`` 会被替换为 Python 路由提供的数据。这里创建
一次 ``templates`` 对象供所有模块共用；它不是一个网页，也不保存业务数据。
"""

from datetime import UTC, datetime

from fastapi.templating import Jinja2Templates

from app.core.paths import TEMPLATES_DIR


# 各模块通过 ``from app.web import templates`` 使用同一个模板目录。
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def local_datetime(value: str | None) -> str:
    """Show stored UTC timestamps in the server's local system timezone."""
    if not value:
        return "—"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M")


templates.env.filters["local_datetime"] = local_datetime
