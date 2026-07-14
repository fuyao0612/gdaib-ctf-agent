"""SQLite 分区仓储共用的连接、锁和领域模型序列化。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class SQLiteStore:
    """为同一仓储的领域分区提供共享基础设施，不暴露给业务层。"""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.migrate()

    def migrate(self) -> None:
        raise NotImplementedError

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @staticmethod
    def _dump(model: BaseModel) -> str:
        return model.model_dump_json()

    @staticmethod
    def _load(model: type[T], raw: str) -> T:
        return model.model_validate_json(raw)
