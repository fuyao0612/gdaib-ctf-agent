from __future__ import annotations

import asyncio
import os
from enum import StrEnum
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from yuwang.domain.models import AgentAction

T = TypeVar("T", bound=BaseModel)


class ProviderErrorCategory(StrEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    INVALID_OUTPUT = "invalid_output"
    SERVICE = "service"


class ProviderError(RuntimeError):
    def __init__(self, category: ProviderErrorCategory, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class ModelProvider(Protocol):
    name: str

    async def generate_structured(self, prompt: str, output_type: type[T], *, timeout: float, attempt: int = 1) -> T: ...


class ProviderChain:
    """Configuration-driven fallback without vendor branches in AgentEngine."""

    name = "fallback-chain"

    def __init__(self, providers: list[ModelProvider]) -> None:
        if not providers:
            raise ValueError("provider chain cannot be empty")
        self.providers = providers

    async def generate_structured(self, prompt: str, output_type: type[T], *, timeout: float, attempt: int = 1) -> T:
        last_error: ProviderError | None = None
        for provider in self.providers:
            try:
                return await provider.generate_structured(
                    prompt, output_type, timeout=timeout, attempt=attempt
                )
            except ProviderError as exc:
                last_error = exc
        raise last_error or ProviderError(ProviderErrorCategory.SERVICE, "没有可用 Provider")


class MockModelProvider:
    name = "mock"

    def __init__(self, scenario: str = "success") -> None:
        self.scenario = scenario
        self.calls = 0

    async def generate_structured(self, prompt: str, output_type: type[T], *, timeout: float, attempt: int = 1) -> T:
        self.calls += 1
        if self.scenario == "timeout":
            await asyncio.sleep(timeout + 0.05)
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "mock timeout", True)
        if self.scenario == "refusal":
            raise ProviderError(ProviderErrorCategory.REFUSAL, "mock refusal")
        if self.scenario == "invalid" or (self.scenario == "fail_then_success" and self.calls == 1):
            try:
                return output_type.model_validate({"kind": "unknown"})
            except ValidationError as exc:
                raise ProviderError(ProviderErrorCategory.INVALID_OUTPUT, "invalid structured output", True) from exc
        fail = "tool_failures=0" in prompt
        value = AgentAction(kind="call_tool", summary="调用安全回显工具验证闭环", tool_name="mock_echo", tool_input={"text": "御网智元安全演示已验证", "fail": fail})
        return output_type.model_validate(value.model_dump())


class OpenAICompatibleProvider:
    name = "openai-compatible"

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("OPENAI_COMPATIBLE_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_COMPATIBLE_API_KEY", "")
        self.model = model or os.getenv("OPENAI_COMPATIBLE_MODEL", "")

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    async def generate_structured(self, prompt: str, output_type: type[T], *, timeout: float, attempt: int = 1) -> T:
        if not self.configured:
            raise ProviderError(ProviderErrorCategory.AUTH, "兼容 Provider 尚未配置")
        payload: dict[str, Any] = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}, "temperature": 0}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
            if response.status_code == 401:
                raise ProviderError(ProviderErrorCategory.AUTH, "Provider 鉴权失败")
            if response.status_code == 429:
                raise ProviderError(ProviderErrorCategory.RATE_LIMIT, "Provider 限流", True)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return output_type.model_validate_json(content)
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "Provider 超时", True) from exc
        except (KeyError, ValueError, ValidationError) as exc:
            raise ProviderError(ProviderErrorCategory.INVALID_OUTPUT, "Provider 返回非法结构", True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorCategory.SERVICE, "Provider 服务错误", True) from exc
