"""OpenAI 兼容模型适配器与受预算约束的故障转移链。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

T = TypeVar("T", bound=BaseModel)


class ProviderErrorCategory(StrEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    INVALID_OUTPUT = "invalid_output"
    SERVICE = "service"


class ProviderCallMetrics(BaseModel):
    provider: str
    model: str
    request_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)
    usage_reported: bool
    input_price_per_million: float = Field(default=0, ge=0)
    output_price_per_million: float = Field(default=0, ge=0)


class ProviderError(RuntimeError):
    def __init__(
        self,
        category: ProviderErrorCategory,
        message: str,
        retryable: bool = False,
        *,
        metrics: ProviderCallMetrics | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.metrics = metrics


class ModelProvider(Protocol):
    """模型能力同时覆盖自由文本聊天与 Agent 结构化输出。"""

    name: str

    async def generate_structured(
        self,
        prompt: str,
        output_type: type[T],
        *,
        timeout: float | None = None,
        attempt: int = 1,
        request_budget: int | None = None,
    ) -> T: ...

    async def generate_text(
        self,
        messages: list[dict[str, str]],
        *,
        system_prompt: str,
        timeout: float | None = None,
    ) -> str: ...

    def stream_text(
        self,
        messages: list[dict[str, str]],
        *,
        system_prompt: str,
        timeout: float | None = None,
    ) -> AsyncIterator[str]: ...


class ProviderChain:
    """按顺序尝试 Provider，并同时限制错误类别和整条链的重试预算。"""

    name = "provider-chain"
    model = "provider-chain"

    def __init__(self, providers: list[ModelProvider], *, retry_budget: int = 0) -> None:
        if not providers:
            raise ValueError("Provider 链不能为空")
        self.providers = providers
        self.retry_budget = retry_budget
        self.last_call_metrics: ProviderCallMetrics | None = None

    async def generate_structured(
        self,
        prompt: str,
        output_type: type[T],
        *,
        timeout: float | None = None,
        attempt: int = 1,
        request_budget: int | None = None,
    ) -> T:
        del request_budget
        last_error: ProviderError | None = None
        remaining_retries = self.retry_budget
        aggregate_requests = 0
        aggregate_retries = 0
        aggregate_duration = 0
        for provider in self.providers:
            try:
                result = await provider.generate_structured(
                    prompt,
                    output_type,
                    timeout=timeout,
                    attempt=attempt,
                    request_budget=remaining_retries + 1,
                )
                metrics = getattr(provider, "last_call_metrics", None)
                if metrics:
                    aggregate_requests += metrics.request_count
                    aggregate_retries += metrics.retry_count
                    aggregate_duration += metrics.duration_ms
                    self.last_call_metrics = metrics.model_copy(
                        update={
                            "request_count": aggregate_requests,
                            "retry_count": aggregate_retries,
                            "duration_ms": aggregate_duration,
                        }
                    )
                return result
            except ProviderError as exc:
                last_error = exc
                metrics = exc.metrics or getattr(provider, "last_call_metrics", None)
                if metrics:
                    aggregate_requests += metrics.request_count
                    aggregate_retries += metrics.retry_count
                    aggregate_duration += metrics.duration_ms
                    remaining_retries = max(0, remaining_retries - metrics.retry_count)
                fallback_on = set(getattr(provider, "fallback_on", []))
                if exc.category.value not in fallback_on:
                    break
        if last_error:
            base = last_error.metrics or ProviderCallMetrics(
                provider=self.name,
                model=self.model,
                request_count=0,
                retry_count=0,
                duration_ms=0,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                cost=0,
                usage_reported=False,
            )
            metrics = base.model_copy(
                update={
                    "request_count": aggregate_requests,
                    "retry_count": aggregate_retries,
                    "duration_ms": aggregate_duration,
                }
            )
            self.last_call_metrics = metrics
            last_error.metrics = metrics
            raise last_error
        raise ProviderError(ProviderErrorCategory.SERVICE, "没有可用 Provider")

    async def generate_text(
        self,
        messages: list[dict[str, str]],
        *,
        system_prompt: str,
        timeout: float | None = None,
    ) -> str:
        last_error: ProviderError | None = None
        for provider in self.providers:
            try:
                result = await provider.generate_text(
                    messages, system_prompt=system_prompt, timeout=timeout
                )
                self.last_call_metrics = getattr(provider, "last_call_metrics", None)
                return result
            except ProviderError as exc:
                last_error = exc
                if exc.category.value not in set(getattr(provider, "fallback_on", [])):
                    break
        raise last_error or ProviderError(ProviderErrorCategory.SERVICE, "没有可用 Provider")

    async def stream_text(
        self,
        messages: list[dict[str, str]],
        *,
        system_prompt: str,
        timeout: float | None = None,
    ) -> AsyncIterator[str]:
        last_error: ProviderError | None = None
        for provider in self.providers:
            emitted = False
            try:
                async for chunk in provider.stream_text(
                    messages, system_prompt=system_prompt, timeout=timeout
                ):
                    emitted = True
                    yield chunk
                self.last_call_metrics = getattr(provider, "last_call_metrics", None)
                return
            except ProviderError as exc:
                last_error = exc
                if emitted or exc.category.value not in set(
                    getattr(provider, "fallback_on", [])
                ):
                    break
        raise last_error or ProviderError(ProviderErrorCategory.SERVICE, "没有可用 Provider")


class ConnectionProbe(BaseModel):
    status: str


class OpenAICompatibleProvider:
    """真实 OpenAI 兼容 HTTP 客户端。

    输入为已校验配置和目标 Pydantic 类型，输出为结构化对象；新增兼容厂商
    通常只需增加预设，只有 HTTP 协议不兼容时才新增实现。
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        max_retries: int = 2,
        structured_mode: str = "json_object",
        fallback_on: Sequence[str] | None = None,
        request_overrides: dict[str, Any] | None = None,
        input_price_per_million: float = 0,
        output_price_per_million: float = 0,
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
        self.fallback_on = list(fallback_on or ["rate_limit", "timeout", "service"])
        self.request_overrides = request_overrides or {}
        self.input_price_per_million = input_price_per_million
        self.output_price_per_million = output_price_per_million
        self._transport = transport
        self.last_call_metrics: ProviderCallMetrics | None = None

    async def test_connection(self) -> ProviderCallMetrics:
        result = await self.generate_structured(
            '仅返回 JSON：{"status":"ok"}', ConnectionProbe, timeout=self.timeout_seconds
        )
        if result.status.lower() != "ok":
            raise ProviderError(
                ProviderErrorCategory.INVALID_OUTPUT, "模型响应解析失败：status 不是 ok"
            )
        assert self.last_call_metrics is not None
        return self.last_call_metrics

    async def discover_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                verify=True,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "模型列表请求超时", True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorCategory.SERVICE, "模型列表网络请求失败") from exc
        self._raise_for_status(response.status_code)
        try:
            values = response.json()["data"]
            return sorted({str(value["id"]) for value in values if value.get("id")})
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                ProviderErrorCategory.INVALID_OUTPUT, "模型列表响应格式不兼容，请手动填写模型名称"
            ) from exc

    async def generate_structured(
        self,
        prompt: str,
        output_type: type[T],
        *,
        timeout: float | None = None,
        attempt: int = 1,
        request_budget: int | None = None,
    ) -> T:
        del attempt
        effective_timeout = timeout or self.timeout_seconds
        allowed_requests = min(self.max_retries + 1, request_budget or self.max_retries + 1)
        started = time.perf_counter()
        last_error: ProviderError | None = None
        for request_index in range(allowed_requests):
            try:
                result, usage = await self._request(prompt, output_type, effective_timeout)
                self.last_call_metrics = self._metrics(request_index + 1, started, usage)
                return result
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or request_index + 1 >= allowed_requests:
                    metrics = self._metrics(request_index + 1, started, None)
                    self.last_call_metrics = metrics
                    exc.metrics = metrics
                    raise
                await asyncio.sleep(min(0.25 * (2**request_index), 4.0))
        raise last_error or ProviderError(ProviderErrorCategory.SERVICE, "Provider 调用失败")

    async def generate_text(
        self,
        messages: list[dict[str, str]],
        *,
        system_prompt: str,
        timeout: float | None = None,
    ) -> str:
        """自由文本聊天不发送 response_format，兼容国内 OpenAI 协议服务。"""

        effective_timeout = timeout or self.timeout_seconds
        started = time.perf_counter()
        last_error: ProviderError | None = None
        for request_index in range(self.max_retries + 1):
            try:
                content, usage = await self._request_text(
                    messages, system_prompt, effective_timeout
                )
                self.last_call_metrics = self._metrics(request_index + 1, started, usage)
                return content
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or request_index >= self.max_retries:
                    metrics = self._metrics(request_index + 1, started, None)
                    self.last_call_metrics = metrics
                    exc.metrics = metrics
                    raise
                await asyncio.sleep(min(0.25 * (2**request_index), 4.0))
        raise last_error or ProviderError(ProviderErrorCategory.SERVICE, "Provider 调用失败")

    async def stream_text(
        self,
        messages: list[dict[str, str]],
        *,
        system_prompt: str,
        timeout: float | None = None,
    ) -> AsyncIterator[str]:
        """优先读取标准 SSE；厂商忽略 stream 时自动接受其完整 JSON 响应。"""

        effective_timeout = timeout or self.timeout_seconds
        started = time.perf_counter()
        self.last_call_metrics = None
        emitted = False
        try:
            async for chunk, usage in self._request_stream(
                messages, system_prompt, effective_timeout
            ):
                if chunk:
                    emitted = True
                    yield chunk
                if usage is not None:
                    self.last_call_metrics = self._metrics(1, started, usage)
            if self.last_call_metrics is None:
                self.last_call_metrics = self._metrics(1, started, None)
        except ProviderError:
            if emitted:
                raise
            # 部分兼容接口不接受 stream 参数；只在尚未输出文本时安全降级。
            content = await self.generate_text(
                messages, system_prompt=system_prompt, timeout=effective_timeout
            )
            yield content

    def _chat_payload(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "stream": stream,
            **self.request_overrides,
        }

    async def _request_text(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        timeout: float,
    ) -> tuple[str, dict[str, int] | None]:
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
                    json=self._chat_payload(messages, system_prompt, stream=False),
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "模型请求超时", True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorCategory.SERVICE, "模型网络请求失败") from exc
        self._raise_for_status(response.status_code)
        return self._parse_text_response(response.json())

    async def _request_stream(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        timeout: float,
    ) -> AsyncIterator[tuple[str, dict[str, int] | None]]:
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                verify=True,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=self._chat_payload(messages, system_prompt, stream=True),
                ) as response:
                    self._raise_for_status(response.status_code)
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        body = json.loads((await response.aread()).decode())
                        content, complete_usage = self._parse_text_response(body)
                        yield content, complete_usage
                        return
                    usage: dict[str, int] | None = None
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            item = json.loads(data)
                            usage = self._parse_usage(item.get("usage")) or usage
                            choices = item.get("choices")
                            # 一些 OpenAI 兼容服务会在流结束前发送只携带 usage 的
                            # 尾包（choices 为空数组）。这不是回复失败，直接保留
                            # 用量并继续等待 [DONE]，不能对空数组取下标。
                            if choices == []:
                                continue
                            if not isinstance(choices, list) or not choices:
                                raise ValueError("choices is missing")
                            first_choice = choices[0]
                            if not isinstance(first_choice, dict):
                                raise TypeError("choice is not an object")
                            delta = first_choice.get("delta", {}).get("content", "")
                        except (KeyError, TypeError, ValueError, IndexError) as exc:
                            raise ProviderError(
                                ProviderErrorCategory.INVALID_OUTPUT,
                                "模型流式响应格式不兼容",
                            ) from exc
                        if isinstance(delta, str) and delta:
                            yield delta, None
                    yield "", usage
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "模型请求超时", True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorCategory.SERVICE, "模型网络请求失败") from exc

    @classmethod
    def _parse_text_response(
        cls, body: Any
    ) -> tuple[str, dict[str, int] | None]:
        try:
            message = body["choices"][0]["message"]
            if message.get("refusal"):
                raise ProviderError(ProviderErrorCategory.REFUSAL, "模型出于安全策略拒绝请求")
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise TypeError("content is empty")
            return content, cls._parse_usage(body.get("usage"))
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                ProviderErrorCategory.INVALID_OUTPUT, "模型未返回可用的自然语言文本"
            ) from exc

    async def _request(
        self, prompt: str, output_type: type[T], timeout: float
    ) -> tuple[T, dict[str, int] | None]:
        schema = output_type.model_json_schema()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "只返回符合下列约束的 JSON，不要输出解释或思维链。JSON Schema："
                        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            **self.request_overrides,
        }
        response_format = self._response_format(output_type)
        if response_format:
            payload["response_format"] = response_format
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
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "模型请求超时", True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ProviderErrorCategory.SERVICE, "模型网络请求失败") from exc

        self._raise_for_status(response.status_code)
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            if message.get("refusal"):
                raise ProviderError(ProviderErrorCategory.REFUSAL, "模型出于安全策略拒绝请求")
            content = message["content"]
            if not isinstance(content, str):
                raise TypeError("content is not text")
            usage = self._parse_usage(body.get("usage"))
            return output_type.model_validate_json(content), usage
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderError(
                ProviderErrorCategory.INVALID_OUTPUT, "模型未返回符合配置的结构化 JSON"
            ) from exc

    def _response_format(self, output_type: type[BaseModel]) -> dict[str, Any] | None:
        if self.structured_mode == "prompt_json":
            return None
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

    def _metrics(
        self, request_count: int, started: float, usage: dict[str, int] | None
    ) -> ProviderCallMetrics:
        usage = usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return ProviderCallMetrics(
            provider=self.name,
            model=self.model,
            request_count=request_count,
            retry_count=max(0, request_count - 1),
            duration_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
            cost=(
                usage["input_tokens"] * self.input_price_per_million
                + usage["output_tokens"] * self.output_price_per_million
            )
            / 1_000_000,
            usage_reported=bool(usage["total_tokens"]),
            input_price_per_million=self.input_price_per_million,
            output_price_per_million=self.output_price_per_million,
        )

    @staticmethod
    def _parse_usage(value: Any) -> dict[str, int] | None:
        if not isinstance(value, dict):
            return None
        input_tokens = int(value.get("prompt_tokens", value.get("input_tokens", 0)) or 0)
        output_tokens = int(value.get("completion_tokens", value.get("output_tokens", 0)) or 0)
        total_tokens = int(value.get("total_tokens", input_tokens + output_tokens) or 0)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code in {401, 403}:
            raise ProviderError(ProviderErrorCategory.AUTH, "模型鉴权失败，请检查 API Key")
        if status_code == 404:
            raise ProviderError(
                ProviderErrorCategory.SERVICE, "模型或接口不存在，请检查模型名称与 Base URL"
            )
        if status_code == 429:
            raise ProviderError(ProviderErrorCategory.RATE_LIMIT, "模型服务限流或额度不足", True)
        if status_code in {408, 504}:
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "模型服务响应超时", True)
        if status_code in {500, 502, 503}:
            raise ProviderError(ProviderErrorCategory.SERVICE, "模型服务暂时不可用", True)
        raise ProviderError(
            ProviderErrorCategory.SERVICE, f"模型服务拒绝请求（HTTP {status_code}）"
        )
