"""ToolPlugin 的稳定抽象，具体运行时不应进入插件接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

from .contracts import ToolSpec

I = TypeVar("I", bound=BaseModel)
O = TypeVar("O", bound=BaseModel)


class ToolPlugin(ABC, Generic[I, O]):
    input_model: type[I]
    output_model: type[O]

    @property
    @abstractmethod
    def spec(self) -> ToolSpec: ...

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @abstractmethod
    async def execute(self, value: I) -> O: ...
