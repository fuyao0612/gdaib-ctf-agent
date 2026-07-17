from __future__ import annotations

import asyncio
import json
from typing import TypeVar

from pydantic import BaseModel, Field, ValidationError

from yuwang.control import TaskBriefDraft
from yuwang.domain.models import AgentAction, AgentPlan, ImportantFacts
from yuwang.model_providers import ProviderError
from yuwang.model_providers.providers import ProviderErrorCategory
from yuwang.tooling.sdk import ToolPlugin, ToolSpec

T = TypeVar("T", bound=BaseModel)


class FakeModelProvider:
    name = "test-provider"
    fallback_on = ["rate_limit", "timeout", "service"]

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
        request_budget: int | None = None,
    ) -> T:
        del attempt, request_budget
        self.calls += 1
        if self.scenario == "timeout":
            await asyncio.sleep((timeout or 0.001) + 0.01)
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "test timeout", True)
        if self.scenario == "refusal":
            raise ProviderError(ProviderErrorCategory.REFUSAL, "test refusal")
        if self.scenario == "service":
            raise ProviderError(ProviderErrorCategory.SERVICE, "test service", True)
        if self.scenario == "invalid" or (self.scenario == "fail_then_success" and self.calls == 1):
            try:
                return output_type.model_validate({"kind": "unknown"})
            except ValidationError as exc:
                raise ProviderError(
                    ProviderErrorCategory.INVALID_OUTPUT, "invalid structured output", True
                ) from exc
        if output_type is AgentPlan:
            return output_type.model_validate(
                AgentPlan(
                    summary="基于测试工具生成计划",
                    steps=["执行测试工具", "核对候选证据", "提交验证"],
                    success_approach="从工具输出提取候选并交由确定性验证器",
                ).model_dump()
            )
        if output_type is TaskBriefDraft:
            context = json.loads(prompt)
            needs_clarification = (
                self.scenario == "clarification" and not context.get("supplemental_inputs")
            )
            return output_type.model_validate(
                {
                    "goal": "完成用户提交的安全任务",
                    "authorized_scope": context.get("authorized_targets", []),
                    "constraints": context.get("constraints", []),
                    "success_criteria": context.get("success_conditions", []),
                    "expected_output": "可审核结果",
                    "known_information": ["已保存原始任务"],
                    "assumptions": [],
                    "risks": ["不得扩大授权范围"],
                    "needs_clarification": needs_clarification,
                    "clarification_questions": (
                        ["请补充目标受众"] if needs_clarification else []
                    ),
                }
            )
        if output_type is ImportantFacts:
            return output_type.model_validate(
                {"facts": ["用户希望获得中文回答", "用户希望获得中文回答"]}
            )
        context = json.loads(prompt)
        observations = context.get("observations_untrusted", context.get("observations", []))
        supplemental = context.get("supplemental_inputs", [])
        if self.scenario == "request_input" and not supplemental:
            return output_type.model_validate(
                AgentAction(kind="request_input", summary="请补充目标受众").model_dump()
            )
        if self.scenario in {"request_input", "advisory"}:
            return output_type.model_validate(
                AgentAction(
                    kind="finish",
                    summary="生成建议回答",
                    answer=f"建议：{supplemental[-1] if supplemental else '采用分阶段方案'}",
                ).model_dump()
            )
        if self.scenario == "structured":
            return output_type.model_validate(
                AgentAction(
                    kind="finish",
                    summary="生成结构化结果",
                    structured_output={"title": "validated", "priority": 1},
                ).model_dump()
            )
        if observations and observations[-1]["success"]:
            latest = observations[-1]
            value = AgentAction(
                kind="finish",
                summary="提出有工具来源的候选答案",
                candidate={
                    "value": latest["output"]["echoed"],
                    "source_call_id": latest["call_id"],
                    "location": "/echoed",
                },
            )
            return output_type.model_validate(value.model_dump())
        fail = not observations
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
