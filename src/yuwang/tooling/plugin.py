"""ToolPlugin 的稳定抽象，具体运行时不应进入插件接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

from .contracts import ToolCallRequest, ToolSpec

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

    async def execute_with_request(
        self, value: I, request: ToolCallRequest | None
    ) -> O:
        """需要 Run 上下文的工具可覆写此方法，旧插件继续只实现 ``execute``。"""

        del request
        return await self.execute(value)

    @abstractmethod
    async def execute(self, value: I) -> O: ...
