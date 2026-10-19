"""读取平台配置，目前主要确定存储根目录。

业务代码统一使用 ``settings.data_root``，不要写死开发者电脑上的绝对路径：

* 直接用本机 Python 启动时，它默认指向项目的 ``storage/``；
* Docker 启动时，``HKC_DATA_ROOT=/data``，而 Compose 把宿主机 ``./storage``
  挂载到容器的 ``/data``。

当前采用文件夹存储数据集，没有启用数据库；这个配置指向的是真实文件存储。
"""

from dataclasses import dataclass, field
import os
from pathlib import Path

from app.core.paths import DEFAULT_DATA_ROOT


@dataclass(frozen=True, slots=True)
class Settings:
    """程序运行时使用的配置值；当前只有统一的数据根目录。"""

    data_root: Path = field(
        default_factory=lambda: Path(
            os.getenv("HKC_DATA_ROOT", str(DEFAULT_DATA_ROOT))
        ).expanduser().resolve()
    )


settings = Settings()
