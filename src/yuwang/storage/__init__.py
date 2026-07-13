"""持久化实现的组合入口；Agent 核心依赖仓储协议而不是本包。"""

from .sqlite import SQLiteRepository

__all__ = ["SQLiteRepository"]
