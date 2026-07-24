"""统一工具执行器：在插件边界校验 Schema、隔离异常与超时。"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from jsonschema import validate as validate_json_schema
from pydantic import ValidationError

from .contracts import ToolCallError, ToolCallRequest, ToolCallResult
from .registry import ToolRegistry


def _now() -> datetime:
    return datetime.now(UTC)


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(
        self,
        name: str,
        raw_input: dict[str, Any],
        timeout: float | None = None,
        *,
        run_id: Any = None,
        target_scope: list[str] | None = None,
        approval_fingerprint: str | None = None,
    ) -> ToolCallResult:
        """旧调用入口的兼容门面，内部仍创建唯一调用契约。"""

        try:
            tool = self.registry.get(name)
            spec = tool.spec
        except Exception as exc:
            return self._failure(
                call_id=None,
                tool_id=name,
                tool_version="unknown",
                summary=f"{name} 执行失败",
                # 旧 SDK 将查找失败统一暴露为 execution_error；保留这个稳定错误码，
                # 具体“未注册”原因仍在脱敏消息中，调用方无需为升级增加分支。
                code="execution_error",
                message=str(exc),
                started_at=_now(),
            )
        request = ToolCallRequest(
            run_id=run_id,
            tool_id=spec.id,
            tool_version=spec.version,
            arguments=raw_input,
            target_scope=target_scope or [],
            approval_fingerprint=approval_fingerprint,
        )
        return await self.execute_call(request, timeout=timeout)

    async def execute_call(
        self, request: ToolCallRequest, timeout: float | None = None
    ) -> ToolCallResult:
        started_at = _now()
        started = time.perf_counter()
        try:
            tool = self.registry.get(request.tool_id)
            spec = tool.spec
            if request.tool_version != spec.version:
                raise ValueError("工具版本与 Run 快照不一致")
            validate_json_schema(instance=request.arguments, schema=spec.input_schema)
            value = tool.input_model.model_validate(request.arguments)
            output = await asyncio.wait_for(
                tool.execute(value), timeout=timeout or spec.timeout_seconds
            )
            structured_output = output.model_dump(mode="json")
            validate_json_schema(instance=structured_output, schema=spec.output_schema)
            finished_at = _now()
            return ToolCallResult(
                call_id=request.call_id,
                success=True,
                status="succeeded",
                summary=f"{spec.display_name} 执行成功",
                structured_output=structured_output,
                duration_ms=int((time.perf_counter() - started) * 1000),
                started_at=started_at,
                finished_at=finished_at,
                executed_tool_id=spec.id,
                executed_tool_version=spec.version,
            )
        except (ValidationError, JsonSchemaValidationError) as exc:
            return self._failure(
                call_id=request.call_id,
                tool_id=request.tool_id,
                tool_version=request.tool_version,
                summary=f"{request.tool_id} 输入无效",
                code="invalid_input",
                message=str(exc),
                started_at=started_at,
                started=started,
            )
        except TimeoutError:
            return self._failure(
                call_id=request.call_id,
                tool_id=request.tool_id,
                tool_version=request.tool_version,
                summary=f"{request.tool_id} 执行超时",
                code="timeout",
                message="工具执行超时",
                retryable=True,
                timed_out=True,
                started_at=started_at,
                started=started,
            )
        except Exception as exc:  # 插件边界必须隔离任何实现异常。
            return self._failure(
                call_id=request.call_id,
                tool_id=request.tool_id,
                tool_version=request.tool_version,
                summary=f"{request.tool_id} 执行失败",
                code="execution_error",
                message=str(exc),
                retryable=True,
                started_at=started_at,
                started=started,
            )

    @staticmethod
    def _failure(
        *,
        call_id: Any,
        tool_id: str,
        tool_version: str,
        summary: str,
        code: str,
        message: str,
        started_at: datetime,
        started: float | None = None,
        retryable: bool = False,
        timed_out: bool = False,
    ) -> ToolCallResult:
        return ToolCallResult(
            call_id=call_id or ToolCallRequest(tool_id=tool_id, tool_version=tool_version).call_id,
            success=False,
            status="timed_out" if timed_out else "failed",
            summary=summary,
            error=ToolCallError(code=code, message=message[:500], retryable=retryable),
            duration_ms=int((time.perf_counter() - started) * 1000) if started else 0,
            started_at=started_at,
            finished_at=_now(),
            timed_out=timed_out,
            executed_tool_id=tool_id,
            executed_tool_version=tool_version,
        )
