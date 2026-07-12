from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ProviderErrorCategory(StrEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    INVALID_OUTPUT = "invalid_output"
    SERVICE = "service"


class ProviderError(RuntimeError):
    def __init__(
        self, category: ProviderErrorCategory, message: str, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class ModelProvider(Protocol):
    name: str

    async def generate_structured(
        self,
        prompt: str,
        output_type: type[T],
        *,
        timeout: float | None = None,
        attempt: int = 1,
    ) -> T: ...


class ProviderChain:
    """Ordered fallback chain; AgentEngine contains no vendor-specific branches."""

    name = "provider-chain"

    def __init__(self, providers: list[ModelProvider]) -> None:
        if not providers:
            raise ValueError("Provider 链不能为空")
        self.providers = providers

    async def generate_structured(
        self,
        prompt: str,
        output_type: type[T],
        *,
        timeout: float | None = None,
        attempt: int = 1,
    ) -> T:
        last_error: ProviderError | None = None
        for provider in self.providers:
            try:
                return await provider.generate_structured(
                    prompt, output_type, timeout=timeout, attempt=attempt
                )
            except ProviderError as exc:
                last_error = exc
        raise last_error or ProviderError(ProviderErrorCategory.SERVICE, "没有可用 Provider")


class ConnectionProbe(BaseModel):
    status: str


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        structured_mode: str = "json_schema",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Provider API Key 不能为空")
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.structured_mode = structured_mode
        self._transport = transport

    async def test_connection(self) -> None:
        result = await self.generate_structured(
            '仅返回 JSON：{"status":"ok"}', ConnectionProbe, timeout=self.timeout_seconds
        )
        if result.status.lower() != "ok":
            raise ProviderError(ProviderErrorCategory.INVALID_OUTPUT, "Provider 连接测试返回异常")

    async def generate_structured(
        self,
        prompt: str,
        output_type: type[T],
        *,
        timeout: float | None = None,
        attempt: int = 1,
    ) -> T:
        del attempt
        effective_timeout = timeout or self.timeout_seconds
        last_error: ProviderError | None = None
        for retry_index in range(self.max_retries + 1):
            try:
                return await self._request(prompt, output_type, effective_timeout)
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or retry_index >= self.max_retries:
                    raise
                await asyncio.sleep(min(0.25 * (2**retry_index), 4.0))
        raise last_error or ProviderError(ProviderErrorCategory.SERVICE, "Provider 调用失败")

    async def _request(self, prompt: str, output_type: type[T], timeout: float) -> T:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "严格按照所给 JSON Schema 返回 JSON，不要输出解释或思维链。",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": self._response_format(output_type),
        }
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                verify=True,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "Provider 请求超时", True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorCategory.SERVICE, "Provider 网络请求失败") from exc

        self._raise_for_status(response.status_code)
        try:
            message = response.json()["choices"][0]["message"]
            if message.get("refusal"):
                raise ProviderError(ProviderErrorCategory.REFUSAL, "Provider 拒绝请求")
            content = message["content"]
            if not isinstance(content, str):
                raise TypeError("content is not text")
            return output_type.model_validate_json(content)
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderError(
                ProviderErrorCategory.INVALID_OUTPUT, "Provider 返回非法结构"
            ) from exc

    def _response_format(self, output_type: type[BaseModel]) -> dict[str, Any]:
        if self.structured_mode == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": output_type.__name__.lower(),
                "strict": True,
                "schema": output_type.model_json_schema(),
            },
        }

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code in {401, 403}:
            raise ProviderError(ProviderErrorCategory.AUTH, "Provider 鉴权失败")
        if status_code == 429:
            raise ProviderError(ProviderErrorCategory.RATE_LIMIT, "Provider 请求限流", True)
        if status_code in {408, 504}:
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "Provider 请求超时", True)
        if status_code in {500, 502, 503}:
            raise ProviderError(ProviderErrorCategory.SERVICE, "Provider 暂时不可用", True)
        raise ProviderError(ProviderErrorCategory.SERVICE, "Provider 服务拒绝请求")
