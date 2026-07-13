"""FastAPI 应用包；外部启动器只需要导入 `app` 或 `create_app`。"""

from .main import app, create_app

__all__ = ["app", "create_app"]
