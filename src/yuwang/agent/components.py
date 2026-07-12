from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from pydantic import BaseModel, Field

from yuwang.agent.verification import SuccessVerifier, VerificationResult
from yuwang.domain.models import AgentAction, AgentPlan, MemoryRecord, Observation, TaskSpec
from yuwang.reports import ReportGenerator
from yuwang.settings.profiles import (
    PLATFORM_PROMPT,
    SECURITY_PROMPT,
    AgentProfileVersion,
    SafeTemplateRenderer,
)

T = TypeVar("T", bound=BaseModel)
StructuredInvoker = Callable[[Any, type[T], str], Awaitable[T]]


class ContextBuildResult(BaseModel):
    prompt: str
    estimated_tokens: int = Field(ge=0)
    observation_chars: int = Field(ge=0)
    truncated: bool = False
    reasons: list[str] = Field(default_factory=list)


class ContextRepository(Protocol):
    def get_run(self, run_id: Any) -> Any | None: ...
    def list_messages(self, thread_id: Any) -> list[Any]: ...
    def get_artifact(self, artifact_id: Any) -> Any | None: ...
    def list_memories(self, thread_id: Any, enabled_only: bool = True) -> list[MemoryRecord]: ...
    def get_agent_defaults(self) -> Any: ...


class ContextBuilder(Protocol):
    def build(self, state: Any, profile: AgentProfileVersion, purpose: str) -> ContextBuildResult: ...


class Planner(Protocol):
    async def plan(self, state: Any, invoke: StructuredInvoker[Any]) -> AgentPlan: ...


class ActionSelector(Protocol):
    async def select(self, state: Any, invoke: StructuredInvoker[Any]) -> AgentAction: ...


class Memory(Protocol):
    def list_memories(self, thread_id: Any, enabled_only: bool = True) -> list[MemoryRecord]: ...
    def save_memory(self, value: MemoryRecord) -> MemoryRecord: ...
    def clear_memories(self, thread_id: Any) -> None: ...


class Verifier(Protocol):
    def verify(
        self, task: TaskSpec, candidate: Any, observations: list[Observation]
    ) -> VerificationResult: ...


class ReportRenderer(Protocol):
    def generate(self, run: Any, task: TaskSpec, events: list[Any], metrics: dict[str, Any]) -> Any: ...


class WorkflowNode(Protocol):
    name: str

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]: ...


class ComponentRegistry:
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def register(self, name: str, value: Any) -> None:
        if name in self._values:
            raise ValueError(f"组件已注册：{name}")
        self._values[name] = value

    def require(self, name: str, expected: type[T] | None = None) -> Any:
        try:
            value = self._values[name]
        except KeyError as exc:
            raise KeyError(f"组件未注册：{name}") from exc
        if expected and not isinstance(value, expected):
            raise TypeError(f"组件类型不匹配：{name}")
        return value

    def __contains__(self, name: str) -> bool:
        return name in self._values


class DefaultPlanner:
    async def plan(self, state: Any, invoke: StructuredInvoker[Any]) -> AgentPlan:
        return cast(
            AgentPlan,
            await invoke(state, AgentPlan, "根据任务、上下文和可用能力生成动态计划"),
        )


class DefaultActionSelector:
    async def select(self, state: Any, invoke: StructuredInvoker[Any]) -> AgentAction:
        return cast(
            AgentAction,
            await invoke(
                state,
                AgentAction,
                "选择下一动作：call_tool、replan、finish、fail 或 request_input",
            ),
        )


class DefaultContextBuilder:
    def __init__(self, repository: ContextRepository, artifact_root: Path) -> None:
        self.repository = repository
        self.artifact_root = artifact_root.resolve()

    def build(self, state: Any, profile: AgentProfileVersion, purpose: str) -> ContextBuildResult:
        run = self.repository.get_run(state.run_id)
        messages = self.repository.list_messages(run.thread_id) if run else []
        policy = profile.context_policy
        selected_messages = messages[-policy.recent_message_limit :]
        reasons: list[str] = []
        truncated = len(selected_messages) < len(messages)
        if truncated:
            reasons.append("recent_message_limit")

        observations: list[dict[str, Any]] = []
        observation_chars = 0
        observation_limit = self.repository.get_agent_defaults().observation_char_budget
        for item in reversed(state.observations):
            value = item.model_dump(mode="json")
            encoded = json.dumps(value, ensure_ascii=False, default=str)
            if observation_chars + len(encoded) > observation_limit:
                truncated = True
                reasons.append("observation_char_budget")
                break
            observations.insert(0, value)
            observation_chars += len(encoded)

        memories = (
            self.repository.list_memories(run.thread_id)
            if run and profile.memory_policy.enabled and policy.include_memories
            else []
        )
        attachment_context = [
            self._attachment_context(artifact_id, policy.text_attachment_char_limit)
            for artifact_id in state.task.artifact_ids
        ]
        context: dict[str, Any] = {
            "security_layer": SECURITY_PROMPT,
            "platform_layer": PLATFORM_PROMPT,
            "purpose": purpose,
            "untrusted_task": state.task.body,
            "scenario": state.task.scenario,
            "conversation": [item.model_dump(mode="json") for item in selected_messages],
            "supplemental_inputs": state.supplemental_inputs,
            "memory": [item.model_dump(mode="json") for item in memories],
            "attachments_untrusted": attachment_context,
            "authorized_targets": state.task.authorized_targets,
            "constraints": state.task.constraints,
            "success_conditions": state.task.success_conditions,
            "verification_rules": [
                rule.model_dump(mode="json") for rule in state.task.verification_rules
            ],
            "tools": state.tool_schemas,
            "current_plan": state.plan.model_dump(mode="json") if state.plan else None,
            "observations_untrusted": observations,
            "completion_mode": profile.completion_mode,
            "validation_policy": profile.validation_policy.model_dump(mode="json"),
            "remaining_budget": state.remaining_budget,
        }
        context["user_instruction"] = SafeTemplateRenderer.render(
            profile.user_prompt_template,
            {
                "task": state.task.body,
                "scenario": state.task.scenario,
                "thread_summary": "\n".join(
                    item.content for item in memories if item.kind == "thread_summary"
                ),
                "current_plan": context["current_plan"] or "",
                "observations": observations,
                "remaining_budget": state.remaining_budget,
            },
        )
        prompt = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        token_limit = self.repository.get_agent_defaults().context_token_budget
        if len(prompt) // 4 > token_limit:
            context["conversation"] = context["conversation"][-3:]
            context["memory"] = context["memory"][-10:]
            context["attachments_untrusted"] = [
                {key: value for key, value in item.items() if key != "text"}
                for item in attachment_context
            ]
            prompt = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            truncated = True
            reasons.append("context_token_budget")
        if len(prompt) // 4 > token_limit:
            raise ValueError("上下文在安全裁剪后仍超过 Token 预算")
        return ContextBuildResult(
            prompt=prompt,
            estimated_tokens=max(1, len(prompt) // 4),
            observation_chars=observation_chars,
            truncated=truncated,
            reasons=sorted(set(reasons)),
        )

    def _attachment_context(self, artifact_id: Any, char_limit: int) -> dict[str, Any]:
        artifact = self.repository.get_artifact(artifact_id)
        if not artifact:
            return {"id": str(artifact_id), "error": "missing"}
        result = {
            "id": str(artifact.id),
            "filename": artifact.filename,
            "kind": artifact.kind,
            "sha256": artifact.sha256,
            "size": artifact.size,
            "mime_type": artifact.mime_type,
            "storage_ref": artifact.storage_ref,
        }
        if Path(artifact.filename).suffix.lower() not in {".txt", ".md", ".json", ".log"}:
            return result
        path = (self.artifact_root / artifact.storage_ref).resolve()
        if self.artifact_root not in path.parents or not path.is_file():
            return result
        raw = path.read_bytes()[: min(char_limit * 4, 256_000)]
        text = raw.decode("utf-8", errors="replace")
        result["text"] = "\n".join(text.splitlines()[:2000])[:char_limit]
        result["trust"] = "untrusted"
        return result


class DefaultVerifier(SuccessVerifier):
    pass


class DefaultReportRenderer(ReportGenerator):
    pass
