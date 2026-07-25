"""工具执行期间的进度回调上下文，不把回调状态存到 Plugin 实例上。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from inspect import isawaitable
from typing import TypeAlias
from uuid import UUID

from .contracts import ToolProgress

ProgressReporter: TypeAlias = Callable[[ToolProgress], Awaitable[None] | None]

_reporter: ContextVar[ProgressReporter | None] = ContextVar("tool_progress_reporter", default=None)
_call_id: ContextVar[UUID | None] = ContextVar("tool_progress_call_id", default=None)


def bind_progress(
    call_id: UUID, reporter: ProgressReporter | None
) -> tuple[Token[ProgressReporter | None], Token[UUID | None]]:
    """为单次调用绑定回调；ContextVar 可避免并发调用互相串写进度。"""

    return _reporter.set(reporter), _call_id.set(call_id)


def reset_progress(tokens: tuple[Token[ProgressReporter | None], Token[UUID | None]]) -> None:
    reporter_token, call_id_token = tokens
    _reporter.reset(reporter_token)
    _call_id.reset(call_id_token)


async def report_progress(percent: float, message: str) -> None:
    """向当前调用的观察者发送已校验的结构化进度；没有观察者时安全地忽略。"""

    reporter = _reporter.get()
    call_id = _call_id.get()
    if reporter is None or call_id is None:
        return
    result = reporter(ToolProgress(call_id=call_id, percent=percent, message=message))
    if isawaitable(result):
        await result
