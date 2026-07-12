from __future__ import annotations

import asyncio
from typing import TypeVar

from pydantic import BaseModel, Field, ValidationError

from yuwang.domain.models import AgentAction
from yuwang.model_providers import ProviderError
from yuwang.model_providers.providers import ProviderErrorCategory
from yuwang.tooling.sdk import ToolPlugin, ToolSpec

T = TypeVar("T", bound=BaseModel)


class FakeModelProvider:
    name = "test-provider"

    def __init__(self, scenario: str = "success") -> None:
        self.scenario = scenario
        self.calls = 0

    async def generate_structured(
        self,
        prompt: str,
        output_type: type[T],
        *,
        timeout: float | None = None,
        attempt: int = 1,
    ) -> T:
        del attempt
        self.calls += 1
        if self.scenario == "timeout":
            await asyncio.sleep((timeout or 0.001) + 0.01)
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "test timeout", True)
        if self.scenario == "refusal":
            raise ProviderError(ProviderErrorCategory.REFUSAL, "test refusal")
        if self.scenario == "invalid" or (
            self.scenario == "fail_then_success" and self.calls == 1
        ):
            try:
                return output_type.model_validate({"kind": "unknown"})
            except ValidationError as exc:
                raise ProviderError(
                    ProviderErrorCategory.INVALID_OUTPUT, "invalid structured output", True
                ) from exc
        fail = "tool_failures=0" in prompt
        value = AgentAction(
            kind="call_tool",
            summary="调用测试工具",
            tool_name="test_echo",
            tool_input={"text": "verified", "fail": fail},
        )
        return output_type.model_validate(value.model_dump())


class FakeEchoInput(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    fail: bool = False


class FakeEchoOutput(BaseModel):
    echoed: str


class FakeEchoTool(ToolPlugin[FakeEchoInput, FakeEchoOutput]):
    input_model = FakeEchoInput
    output_model = FakeEchoOutput

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="test_echo",
            version="1.0.0",
            description="测试专用回显工具",
            capabilities=["test"],
            scenarios=["test"],
            risk="low",
            permissions=[],
            requires_network=False,
            allowed_target_types=[],
            timeout_seconds=1,
            error_codes=["test_failure"],
            idempotent=True,
            artifact_types=[],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute(self, value: FakeEchoInput) -> FakeEchoOutput:
        if value.fail:
            raise RuntimeError("simulated test failure")
        return FakeEchoOutput(echoed=value.text)
