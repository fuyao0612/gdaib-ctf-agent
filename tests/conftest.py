"""测试进程必须在导入默认 FastAPI app 前隔离生产数据库与附件目录。"""

from __future__ import annotations

import os
from tempfile import TemporaryDirectory

_default_app_data = TemporaryDirectory(prefix="yuwang-pytest-")
os.environ["YUWANG_DATABASE_PATH"] = os.path.join(_default_app_data.name, "default.db")
os.environ["YUWANG_ARTIFACT_ROOT"] = os.path.join(_default_app_data.name, "artifacts")
