from __future__ import annotations

import asyncio
import json
from typing import TypeVar

from pydantic import BaseModel, Field, ValidationError

from yuwang.agent.failure_analysis import FailureAnalysisDraft
from yuwang.agent.retrospective import RunRetrospectiveDraft
from yuwang.control import AgentPlanDraft, TaskBriefDraft
from yuwang.domain.models import AgentAction, ImportantFacts
from yuwang.model_providers import ProviderError
from yuwang.model_providers.providers import ProviderErrorCategory
from yuwang.tooling.sdk import ToolPlugin, ToolSpec

T = TypeVar("T", bound=BaseModel)


def action_draft_payload(action: AgentAction) -> dict[str, object]:
    """测试桩模拟模型动作契约中的必填公开理由。"""

    return {
        **action.model_dump(exclude={"action_reason"}),
        "reason": "依据当前测试计划和最近已记录观察，执行该受控动作。",
    }


def action_payload(action: AgentAction, output_type: type[BaseModel]) -> dict[str, object]:
    """按被请求的契约返回模型 payload，避免把 Draft 字段送入领域模型。"""

    if output_type is AgentAction:
        return action.model_dump()
    return action_draft_payload(action)


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
        if self.scenario == "empty_timeout":
            raise TimeoutError()
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
        if output_type is AgentPlanDraft:
            return output_type.model_validate(
                {
                    "summary": "基于测试工具生成计划",
                    "steps": [
                        {
                            "step_id": "step-1",
                            "goal": "执行测试工具",
                            "reason": "收集当前任务的真实观察。",
                            "expected_result": "获得工具输出。",
                            "verification_method": "检查工具调用状态。",
                            "capabilities": ["tool_call"],
                            "dependencies": [],
                            "risk": "low",
                            "status": "planned",
                        },
                        {
                            "step_id": "step-2",
                            "goal": "核对候选证据",
                            "reason": "将候选绑定到真实来源。",
                            "expected_result": "获得可引用证据。",
                            "verification_method": "检查证据来源。",
                            "capabilities": [],
                            "dependencies": ["step-1"],
                            "risk": "low",
                            "status": "planned",
                        },
                        {
                            "step_id": "step-3",
                            "goal": "提交验证",
                            "reason": "独立检查结果。",
                            "expected_result": "记录验证状态。",
                            "verification_method": "执行确定性验证。",
                            "capabilities": [],
                            "dependencies": ["step-2"],
                            "risk": "low",
                            "status": "planned",
                        },
                    ],
                }
            )
        if output_type is TaskBriefDraft:
            context = json.loads(prompt)
            # 某些 Provider 链路测试直接构造最小 Prompt；测试桩兼容该输入，
            # 生产 ContextBuilder 仍只输出分层后的上下文。
            user_input = context.get("untrusted_user_input", {})
            supplemental = user_input.get(
                "supplemental_inputs", context.get("supplemental_inputs", [])
            )
            execution_constraints = context.get("trusted_execution_constraints", {})
            needs_clarification = (
                self.scenario == "clarification" and not supplemental
            )
            return output_type.model_validate(
                {
                    "goal": "完成用户提交的安全任务",
                    "authorized_scope": execution_constraints.get(
                        "authorized_targets", context.get("authorized_targets", [])
                    ),
                    "constraints": execution_constraints.get(
                        "constraints", context.get("constraints", [])
                    ),
                    "success_criteria": execution_constraints.get(
                        "success_conditions", context.get("success_conditions", [])
                    ),
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
        if output_type is FailureAnalysisDraft:
            return output_type.model_validate(
                {
                    "summary": "任务在安全检查中止，当前未执行未经授权的动作。",
                    "causes": ["模型选择了不可继续的动作"],
                    "next_steps": ["补充允许范围或调整任务后重试"],
                }
            )
        if output_type is RunRetrospectiveDraft:
            return output_type.model_validate(
                {
                    "summary": "已根据持久化步骤整理公开复盘。",
                    "outcome_review": "复盘仅描述已记录事实，最终验证状态保持不变。",
                    "step_reviews": [{
                        "step": 1,
                        "assessment": "步骤提供了可核对的观察。",
                        "contribution": "为后续结论提供已持久化的事实。",
                    }],
                    "effective_actions": ["按计划执行已授权动作"],
                    "failed_attempts": [],
                    "lessons": ["区分候选发现与外部验证"],
                    "next_steps": ["按授权范围继续验证"],
                }
            )
        context = json.loads(prompt)
        user_input = context.get("untrusted_user_input", {})
        observations = context.get(
            "untrusted_tool_content",
            context.get("observations_untrusted", context.get("observations", [])),
        )
        supplemental = user_input.get(
            "supplemental_inputs", context.get("supplemental_inputs", [])
        )
        if self.scenario == "request_input" and not supplemental:
            return output_type.model_validate(
                action_payload(AgentAction(kind="request_input", summary="请补充目标受众"), output_type)
            )
        if self.scenario in {"request_input", "advisory"}:
            return output_type.model_validate(
                action_payload(AgentAction(
                    kind="finish",
                    summary="生成建议回答",
                    answer=f"建议：{supplemental[-1] if supplemental else '采用分阶段方案'}",
                ), output_type)
            )
        if self.scenario == "structured":
            return output_type.model_validate(
                action_payload(AgentAction(
                    kind="finish",
                    summary="生成结构化结果",
                    structured_output={"title": "validated", "priority": 1},
                ), output_type)
            )
        if self.scenario == "declared_failure":
            return output_type.model_validate(
                action_payload(AgentAction(kind="fail", summary="测试触发安全失败"), output_type)
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
            return output_type.model_validate(action_payload(value, output_type))
        fail = not observations
        value = AgentAction(
            kind="call_tool",
            summary="调用测试工具",
            tool_name="test_echo",
            tool_input={"text": "verified", "fail": fail},
        )
        return output_type.model_validate(action_payload(value, output_type))


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
