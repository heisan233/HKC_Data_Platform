"""读取平台配置，目前主要确定存储根目录。

业务代码统一使用 ``settings.data_root``，不要写死开发者电脑上的绝对路径：

* 直接用本机 Python 启动时，它默认指向项目的 ``storage/``；
* Docker 启动时，``HKC_DATA_ROOT=/data``；Compose 默认把宿主机 ``./storage``
  挂载到 ``/data``，部署机可用 ``HKC_STORAGE_PATH`` 改变宿主机位置。

数据集文件与 SQLite 登记库都位于这个存储根目录；数据库文件在 system/ 下。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.core.paths import DEFAULT_DATA_ROOT


@dataclass(frozen=True, slots=True)
class Settings:
    """程序运行时使用的数据根目录、VLM 地址和 Doctor 超时配置。"""

    data_root: Path = field(
        default_factory=lambda: Path(
            os.getenv("HKC_DATA_ROOT", str(DEFAULT_DATA_ROOT))
        ).expanduser().resolve()
    )
    vlm_base_url: str = field(default_factory=lambda: os.getenv("VLM_BASE_URL", "").rstrip("/"))
    vlm_api_token: str = field(default_factory=lambda: os.getenv("VLM_API_TOKEN", "EMPTY"))
    doctor_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("QC_DOCTOR_TIMEOUT_SECONDS", "1800"))
    )


settings = Settings()
