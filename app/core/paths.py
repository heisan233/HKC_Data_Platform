"""集中计算项目目录，避免在业务代码中写死某台电脑的绝对路径。"""

from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]  # 项目中的 app/ 目录
PROJECT_ROOT = APP_DIR.parent  # HKC_Data_Platform_v2/ 项目根目录
TEMPLATES_DIR = APP_DIR / "templates"  # HTML 模板目录
STATIC_DIR = APP_DIR / "static"  # CSS、JavaScript、图片目录

# 不使用 Docker、直接用本机 Python 启动时，默认从项目根目录的 storage/ 读写。
# Docker 启动时，环境变量 HKC_DATA_ROOT 会把它改成容器内的 /data。
DEFAULT_DATA_ROOT = PROJECT_ROOT / "storage"
