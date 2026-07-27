"""Agent 可替换组件：上下文、规划、验证与记忆均通过明确协议协作。"""

from __future__ import annotations

import hashlib
import json
import math
import time
import unicodedata
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from yuwang.agent.repository import AgentRepository
from yuwang.agent.verification import SuccessVerifier, VerificationResult
from yuwang.control import AgentActionDraft, AgentPlanDraft, TaskBrief
from yuwang.domain.models import (
    AgentAction,
    AgentPlan,
    MemoryRecord,
    Message,
    Observation,
    TaskSpec,
)
from yuwang.reports import ReportGenerator
from yuwang.settings.profiles import (
    PLATFORM_PROMPT,
    SECURITY_PROMPT,
    AgentProfileVersion,
    SafeTemplateRenderer,
)

T = TypeVar("T", bound=BaseModel)
INLINE_ARTIFACT_CHAR_LIMIT = 2_000
ARTIFACT_SUMMARY_CHAR_LIMIT = 600
THREAD_SUMMARY_CHAR_LIMIT = 2_400
THREAD_SUMMARY_ENTRY_CHAR_LIMIT = 180
THREAD_SUMMARY_FALLBACK_CHAR_LIMIT = 600
CONTEXT_COMPACTION_SUGGEST_RATIO = 0.75
CONTEXT_COMPACTION_FORCE_RATIO = 0.90
MIN_OUTPUT_TOKEN_RESERVE = 8_192


def estimate_tokens(value: str) -> int:
    """保守估算中英文混合文本的 Token，不把中文按四字符低估。

    该函数是可替换估算器的默认实现。中文、日文、韩文及全角字符按接近一个
    Token 计，ASCII 连续文本按约三字符计；取两种估算的较大值以留出安全余量。
    """

    if not value:
        return 0
    east_asian = sum(
        1
        for char in value
        if unicodedata.east_asian_width(char) in {"W", "F"}
        or "CJK" in unicodedata.name(char, "")
    )
    return max(math.ceil(len(value.encode("utf-8")) / 3), east_asian + math.ceil((len(value) - east_asian) / 3))


class AgentRuntimeState(Protocol):
    """组件可读取的运行状态视图；具体状态模型仍由引擎负责校验。"""

    run_id: UUID
    task: TaskSpec
    observations: list[Observation]
    supplemental_inputs: list[str]
    supplemental_artifact_ids: list[UUID]
    tool_schemas: list[dict[str, Any]]
    plan: AgentPlan | None
    task_brief: TaskBrief | None
    remaining_budget: dict[str, float | int]


class StructuredInvoker(Protocol):
    def __call__(
        self, state: AgentRuntimeState, output_type: type[T], purpose: str
    ) -> Awaitable[T]: ...


class ContextBuildResult(BaseModel):
    prompt: str
    estimated_tokens: int = Field(ge=0)
    observation_chars: int = Field(ge=0)
    truncated: bool = False
    reasons: list[str] = Field(default_factory=list)
    original_message_count: int = Field(default=0, ge=0)
    kept_message_count: int = Field(default=0, ge=0)
    original_memory_count: int = Field(default=0, ge=0)
    kept_memory_count: int = Field(default=0, ge=0)
    before_tokens: int = Field(default=0, ge=0)
    context_window_tokens: int = Field(default=0, ge=0)
    input_token_budget: int = Field(default=0, ge=0)
    compacted: bool = False
    compaction_reason: str | None = None
    compaction_duration_ms: int = Field(default=0, ge=0)
    summary_version: str | None = None
    summary_digest: str | None = None


class ContextBuilder(Protocol):
    def build(
        self, state: AgentRuntimeState, profile: AgentProfileVersion, purpose: str
    ) -> ContextBuildResult: ...

    def estimate_tokens(self, value: str) -> int: ...


class Planner(Protocol):
    async def plan(
        self, state: AgentRuntimeState, invoke: StructuredInvoker
    ) -> AgentPlan: ...


class ActionSelector(Protocol):
    async def select(
        self, state: AgentRuntimeState, invoke: StructuredInvoker
    ) -> AgentAction: ...


class Memory(Protocol):
    def list_memories(
        self, thread_id: UUID | str, enabled_only: bool = True
    ) -> list[MemoryRecord]: ...
    def save_memory(self, value: MemoryRecord) -> MemoryRecord: ...
    def clear_memories(self, thread_id: UUID | str) -> None: ...
    def delete_memory(self, memory_id: UUID | str) -> None: ...


class Verifier(Protocol):
    def verify(
        self, task: TaskSpec, candidate: Any, observations: list[Observation]
    ) -> VerificationResult: ...


class ReportRenderer(Protocol):
    def generate(
        self, run: Any, task: TaskSpec, events: list[Any], metrics: dict[str, Any]
    ) -> Any: ...


class WorkflowNode(Protocol):
    name: str

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]: ...


class DefaultPlanner:
    async def plan(
        self, state: AgentRuntimeState, invoke: StructuredInvoker
    ) -> AgentPlan:
        draft = await invoke(
            state,
            AgentPlanDraft,
            "根据 Task Brief 生成动态计划骨架；只输出 summary 和非空 steps，步骤必须在授权范围内。",
        )
        return draft.to_agent_plan()


class DefaultActionSelector:
    async def select(
        self, state: AgentRuntimeState, invoke: StructuredInvoker
    ) -> AgentAction:
        draft = await invoke(
            state,
            AgentActionDraft,
            (
                "选择下一动作：call_tool、replan、finish、fail 或 request_input。"
                "当用户已给出工具所需的完整受限输入，且工具 Schema 支持该输入时，"
                "优先 call_tool；只有缺少 Schema 必填数据时才 request_input。"
            ),
        )
        return draft.to_agent_action(state.observations)


class DefaultContextBuilder:
    def __init__(self, repository: AgentRepository, artifact_root: Path) -> None:
        self.repository = repository
        self.artifact_root = artifact_root.resolve()

    estimate_tokens = staticmethod(estimate_tokens)

    def build(
        self, state: AgentRuntimeState, profile: AgentProfileVersion, purpose: str
    ) -> ContextBuildResult:
        run = self.repository.get_run(state.run_id)
        messages = self.repository.list_messages(run.thread_id) if run else []
        policy = profile.context_policy
        # 未达到 Token 阈值时保留完整历史；消息条数仅用于压缩后的最近窗口，
        # 不能再成为压缩的主触发条件。
        selected_messages = list(messages)
        reasons: list[str] = []
        truncated = False

        observations: list[dict[str, Any]] = []
        observation_chars = 0
        observation_limit = self.repository.get_agent_defaults().observation_char_budget
        for observation in reversed(state.observations):
            value = self._compact_observation(observation)
            encoded = json.dumps(value, ensure_ascii=False, default=str)
            if observation_chars + len(encoded) > observation_limit:
                truncated = True
                reasons.append("observation_char_budget")
                break
            observations.insert(0, value)
            observation_chars += len(encoded)

        all_memories = (
            self.repository.list_memories(run.thread_id)
            if run and profile.memory_policy.enabled
            else []
        )
        memories = [
            item
            for item in all_memories
            if (item.kind == "thread_summary" and policy.include_thread_summary)
            or (item.kind == "run_summary" and policy.include_run_summaries)
            or (item.kind in {"important_fact", "user_input"} and policy.include_memories)
        ]
        memory_payload = [item.model_dump(mode="json") for item in memories]
        # 首次任务附件与运行中由统一输入框追加的附件都保持不可信上下文；后者
        # 不修改不可变 TaskSpec，而是随检查点恢复。
        attachment_ids = [
            *state.task.artifact_ids,
            *[
                value
                for value in state.supplemental_artifact_ids
                if value not in state.task.artifact_ids
            ],
        ]
        attachment_context = [
            self._attachment_context(artifact_id, policy.text_attachment_char_limit)
            for artifact_id in attachment_ids
        ]
        latest_user_instruction = self._latest_user_instruction(messages, state)
        task_context = self._task_context(
            state,
            attachment_context,
            latest_user_instruction,
        )
        context: dict[str, Any] = {
            "system_policy_layer": {
                "security": SECURITY_PROMPT,
                "platform": PLATFORM_PROMPT,
                "immutable": True,
            },
            "purpose": purpose,
            "untrusted_user_input": {
                "task": state.task.body,
                "scenario": state.task.scenario,
                # 最新用户补充独立于滚动摘要，避免较早摘要覆盖纠偏后的约束。
                "latest_instruction": latest_user_instruction,
                "supplemental_inputs": state.supplemental_inputs,
            },
            # 包含用户和模型回复，二者都不能提升为系统指令或授权事实。
            "untrusted_conversation": [
                item.model_dump(mode="json") for item in selected_messages
            ],
            "untrusted_model_content": {
                "task_context": task_context,
                "memory": memory_payload,
                "current_plan": state.plan.model_dump(mode="json") if state.plan else None,
                "task_brief": (
                    state.task_brief.model_dump(mode="json") if state.task_brief else None
                ),
            },
            "untrusted_attachment_content": attachment_context,
            "untrusted_tool_content": observations,
            "trusted_execution_constraints": {
                "authorized_targets": state.task.authorized_targets,
                "constraints": state.task.constraints,
                "success_conditions": state.task.success_conditions,
                "verification_rules": [
                    rule.model_dump(mode="json") for rule in state.task.verification_rules
                ],
                # Skills 是设置中心创建的声明式任务模板快照；它们不能增加工具、
                # 授权目标或权限，只能提供提示、步骤和检查清单。
                "skills": [item.model_dump(mode="json") for item in state.task.skills],
                "tools": state.tool_schemas,
                "validation_policy": profile.validation_policy.model_dump(mode="json"),
                "completion_mode": profile.completion_mode,
            },
            "completion_mode": profile.completion_mode,
            "remaining_budget": state.remaining_budget,
        }
        context["user_instruction"] = self._render_user_instruction(
            profile, state, memory_payload, observations, context
        )
        prompt = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        before_tokens = self.estimate_tokens(prompt)
        defaults = self.repository.get_agent_defaults()
        provider_limits = [
            value.context_window_tokens
            for value in self.repository.get_provider_snapshot(state.run_id)
            if value.context_window_tokens
        ]
        context_window = min([defaults.context_token_budget, *provider_limits])
        output_reserve = max(MIN_OUTPUT_TOKEN_RESERVE, math.ceil(context_window * 0.10))
        input_budget = max(1024, context_window - output_reserve)
        compacted = False
        compaction_reason: str | None = None
        summary_version: str | None = None
        summary_digest: str | None = None
        compaction_started = time.perf_counter()
        if before_tokens >= math.ceil(input_budget * CONTEXT_COMPACTION_SUGGEST_RATIO):
            recent_count = max(1, min(policy.recent_message_limit, 16))
            older = messages[:-recent_count]
            if older and run and policy.include_thread_summary:
                previous = [
                    item
                    for item in self.repository.list_memories(run.thread_id, enabled_only=False)
                    if item.kind == "thread_summary"
                ]
                saved = previous[-1] if previous else None
                cursor = str(saved.metadata.get("cursor_message_id", "")) if saved else ""
                cursor_index = next(
                    (index for index, item in enumerate(older) if str(item.id) == cursor), -1
                )
                additions = older[cursor_index + 1 :] if cursor_index >= 0 else older
                if saved and not additions:
                    summary = saved.content
                else:
                    summary = self._merge_thread_summary(
                        saved.content if saved and cursor_index >= 0 else None,
                        additions,
                    )
                summary_digest = hashlib.sha256(summary.encode()).hexdigest()
                summary_version = summary_digest[:12]
                if not previous or previous[-1].content != summary:
                    for previous_memory in previous:
                        self.repository.delete_memory(previous_memory.id)
                    saved_summary = self.repository.save_memory(
                        MemoryRecord(
                            thread_id=run.thread_id,
                            kind="thread_summary",
                            content=summary,
                            metadata={
                                "cursor_message_id": str(older[-1].id),
                                "summary_digest": summary_digest,
                                "summary_version": summary_version,
                            },
                        )
                    )
                    all_memories = [
                        item for item in all_memories if item.kind != "thread_summary"
                    ] + [saved_summary]
                selected_messages = messages[-recent_count:]
                memories = [
                    item
                    for item in all_memories
                    if (item.kind == "thread_summary" and policy.include_thread_summary)
                    or (item.kind == "run_summary" and policy.include_run_summaries)
                    or (item.kind in {"important_fact", "user_input"} and policy.include_memories)
                ]
                memory_payload = [item.model_dump(mode="json") for item in memories]
                context["untrusted_conversation"] = [
                    item.model_dump(mode="json") for item in selected_messages
                ]
                context["untrusted_model_content"]["memory"] = memory_payload
                context["user_instruction"] = self._render_user_instruction(
                    profile, state, memory_payload, observations, context
                )
                compacted = True
                truncated = True
                compaction_reason = "threshold_75_percent"
                reasons.append(compaction_reason)
                prompt = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        if self.estimate_tokens(prompt) >= math.ceil(input_budget * CONTEXT_COMPACTION_FORCE_RATIO):
            # 强制降级仍保留系统规则、当前任务、最新纠偏、授权范围和最近消息。
            context["untrusted_conversation"] = context["untrusted_conversation"][-4:]
            context["untrusted_model_content"]["memory"] = self._compact_memory_payload(
                context["untrusted_model_content"]["memory"][-10:],
                THREAD_SUMMARY_FALLBACK_CHAR_LIMIT,
            )
            context["untrusted_attachment_content"] = [
                {key: value for key, value in item.items() if key not in {"text", "summary_excerpt"}}
                for item in attachment_context
            ]
            compacted = True
            truncated = True
            compaction_reason = "forced_90_percent"
            reasons.append(compaction_reason)
            context["user_instruction"] = self._render_user_instruction(
                profile,
                state,
                context["untrusted_model_content"]["memory"],
                observations,
                context,
            )
            prompt = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        if self.estimate_tokens(prompt) > input_budget:
            # 语义压缩不可用或仍不足时采用确定性安全裁剪；绝不删除 SQLite
            # 中的原始消息、事件或 Artifact，也不让 Run 因上下文过长直接损坏。
            context["untrusted_conversation"] = context["untrusted_conversation"][-3:]
            context["untrusted_model_content"]["memory"] = self._compact_memory_payload(
                memory_payload[-10:], THREAD_SUMMARY_FALLBACK_CHAR_LIMIT
            )
            context["untrusted_attachment_content"] = [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"text", "summary_excerpt"}
                }
                for item in attachment_context
            ]
            context["user_instruction"] = self._render_user_instruction(
                profile,
                state,
                context["untrusted_model_content"]["memory"],
                observations,
                context,
            )
            prompt = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            compacted = True
            truncated = True
            compaction_reason = "deterministic_safety_clip"
            reasons.append(compaction_reason)
        return ContextBuildResult(
            prompt=prompt,
            estimated_tokens=max(1, self.estimate_tokens(prompt)),
            observation_chars=observation_chars,
            truncated=truncated,
            reasons=sorted(set(reasons)),
            original_message_count=len(messages),
            kept_message_count=len(context["untrusted_conversation"]),
            original_memory_count=len(all_memories),
            kept_memory_count=len(memories),
            before_tokens=before_tokens,
            context_window_tokens=context_window,
            input_token_budget=input_budget,
            compacted=compacted,
            compaction_reason=compaction_reason,
            compaction_duration_ms=(
                int((time.perf_counter() - compaction_started) * 1000) if compacted else 0
            ),
            summary_version=summary_version,
            summary_digest=summary_digest,
        )

    @staticmethod
    def _render_user_instruction(
        profile: AgentProfileVersion,
        state: AgentRuntimeState,
        memories: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> str:
        """模板使用与模型可见内容一致的摘要，避免裁剪后仍由模板重复旧消息。"""

        thread_summary = "\n".join(
            str(item["content"])
            for item in memories
            if item.get("kind") == "thread_summary"
        )
        return SafeTemplateRenderer.render(
            profile.user_prompt_template,
            {
                "task": state.task.body,
                "scenario": state.task.scenario,
                "thread_summary": thread_summary,
                "current_plan": context["untrusted_model_content"]["current_plan"] or "",
                "observations": observations,
                "remaining_budget": state.remaining_budget,
            },
        )

    @classmethod
    def _compact_thread_summary(cls, messages: list[Message]) -> str:
        """把较早消息压成有界的公开事实摘要，不再复制整段原文。"""

        sections: dict[str, list[str]] = {
            "目标与约束": [],
            "已完成操作": [],
            "失败尝试": [],
            "重要发现": [],
            "待办事项": [],
            "证据引用": [],
        }
        for message in messages:
            content = cls._bounded_text(message.content, THREAD_SUMMARY_ENTRY_CHAR_LIMIT)
            if not content:
                continue
            bucket = cls._summary_bucket(message, content)
            sections[bucket].append(f"{message.role}: {content}")

        original_chars = sum(len(message.content) for message in messages)
        lines = [
            "较早对话确定性摘要（因消息窗口限制生成；原始记录未覆盖）。",
            (
                f"审计范围：{messages[0].id} 至 {messages[-1].id}，共 {len(messages)} 条，"
                f"原始字符 {original_chars}，摘要上限 {THREAD_SUMMARY_CHAR_LIMIT}。"
            ),
        ]
        for label, entries in sections.items():
            representative = cls._representative_entries(entries)
            lines.append(f"{label}：")
            if representative:
                lines.extend(f"- {entry}" for entry in representative)
            else:
                lines.append("- 无可确认的公开事实")
        return cls._bounded_text("\n".join(lines), THREAD_SUMMARY_CHAR_LIMIT)

    @classmethod
    def _merge_thread_summary(
        cls, previous: str | None, additions: list[Message]
    ) -> str:
        """只汇总游标后的旧消息，已有摘要不再回读原始历史。"""

        update = cls._compact_thread_summary(additions)
        if not previous:
            return update
        # 为新增约束预留独立空间，不能在最终首尾裁剪时被旧摘要吞没。
        retained = cls._bounded_text(previous, 1_000)
        increment = cls._bounded_text(update, THREAD_SUMMARY_CHAR_LIMIT - 1_060)
        return f"{retained}\n\n增量更新：\n{increment}"

    @staticmethod
    def _summary_bucket(message: Message, content: str) -> str:
        normalized = content.casefold()
        if str(message.role) == "user":
            return "目标与约束"
        if any(token in normalized for token in ("artifact", "证据", "sha256", "路径", "http")):
            return "证据引用"
        if any(token in normalized for token in ("失败", "错误", "超时", "拒绝", "阻塞", "failure", "error")):
            return "失败尝试"
        if any(token in normalized for token in ("待办", "下一步", "需要", "todo", "next step")):
            return "待办事项"
        if any(token in normalized for token in ("完成", "成功", "已", "finished", "succeeded")):
            return "已完成操作"
        return "重要发现"

    @staticmethod
    def _representative_entries(entries: list[str]) -> list[str]:
        """每类保留开头和结尾的代表项，避免活跃线程继续线性增长。"""

        if len(entries) <= 3:
            return entries
        middle = entries[len(entries) // 2]
        return list(dict.fromkeys([entries[0], middle, entries[-1]]))

    @staticmethod
    def _bounded_text(value: str, limit: int) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        head = max(1, (limit - 36) // 2)
        tail = max(1, limit - 36 - head)
        omitted = len(normalized) - head - tail
        return f"{normalized[:head].rstrip()} ... [已截断 {omitted} 字符] ... {normalized[-tail:].lstrip()}"

    @classmethod
    def _compact_memory_payload(
        cls, values: list[dict[str, Any]], content_limit: int
    ) -> list[dict[str, Any]]:
        """预算降级只缩短模型侧副本，持久化记忆仍保留完整可审计摘要。"""

        return [
            {
                **value,
                "content": cls._bounded_text(str(value["content"]), content_limit),
            }
            for value in values
        ]

    @classmethod
    def _compact_observation(cls, observation: Observation) -> dict[str, Any]:
        """工具原文留在 Artifact/审计，模型上下文只保留可追溯的摘要。"""

        value = observation.model_dump(mode="json")
        output = value.get("output")
        if output:
            encoded = json.dumps(output, ensure_ascii=False, default=str)
            if len(encoded) > ARTIFACT_SUMMARY_CHAR_LIMIT:
                value["output"] = {
                    "summary": cls._bounded_text(encoded, ARTIFACT_SUMMARY_CHAR_LIMIT),
                    "full_output": "保留在工具调用审计或关联 Artifact",
                }
        return value

    @staticmethod
    def _latest_user_instruction(
        messages: list[Message], state: AgentRuntimeState
    ) -> str:
        """运行中补充优先于历史对话；两者都保留在持久化数据里。"""

        if state.supplemental_inputs:
            return state.supplemental_inputs[-1]
        for message in reversed(messages):
            if str(message.role) == "user":
                return message.content
        return state.task.body

    @staticmethod
    def _task_context(
        state: AgentRuntimeState,
        attachments: list[dict[str, Any]],
        latest_user_instruction: str,
    ) -> dict[str, Any]:
        """以稳定、可审计字段保存运行摘要，而不是让模型猜测历史含义。"""

        completed_steps = [
            observation.summary for observation in state.observations if observation.success
        ][-8:]
        blockers = [
            observation.error or observation.summary
            for observation in state.observations
            if not observation.success
        ][-5:]
        decisions: list[str] = []
        if state.task_brief:
            decisions.append(f"Task Brief：{state.task_brief.goal}")
        if state.plan:
            decisions.append(f"当前计划：{state.plan.summary}")
        return {
            "task_summary": state.task_brief.goal if state.task_brief else state.task.body[:1000],
            "latest_goal_or_correction_untrusted": latest_user_instruction,
            "constraints": state.task.constraints,
            "completed_steps": completed_steps,
            "blockers": blockers,
            "key_decisions": decisions,
            "artifact_references": [
                {
                    key: value
                    for key, value in artifact.items()
                    if key not in {"text", "summary_excerpt"}
                }
                for artifact in attachments
            ],
        }

    def _attachment_context(self, artifact_id: UUID, char_limit: int) -> dict[str, Any]:
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
            "trust": "untrusted",
        }
        if Path(artifact.filename).suffix.lower() not in {".txt", ".md", ".json", ".log"}:
            result["content_in_artifact"] = True
            return result
        path = (self.artifact_root / artifact.storage_ref).resolve()
        if self.artifact_root not in path.parents or not path.is_file():
            return result
        inline_limit = min(char_limit, INLINE_ARTIFACT_CHAR_LIMIT)
        raw = path.read_bytes()[: min(max(inline_limit, ARTIFACT_SUMMARY_CHAR_LIMIT) * 4, 16_000)]
        text = raw.decode("utf-8", errors="replace")
        normalized = "\n".join(text.splitlines()[:2000])
        if artifact.size <= inline_limit * 4:
            result["text"] = normalized[:inline_limit]
            result["summary"] = "小型文本附件，内容已随 Artifact 引用提供"
            return result
        result["content_in_artifact"] = True
        result["summary"] = (
            f"大型文本附件，共 {artifact.size} 字节；正文保留在 Artifact。"
        )
        result["summary_excerpt"] = normalized[:ARTIFACT_SUMMARY_CHAR_LIMIT]
        return result


@dataclass(slots=True)
class AgentComponents:
    """一次运行使用的可替换组件，字段名称就是完整装配说明。"""

    planner: Planner
    action_selector: ActionSelector
    context_builder: ContextBuilder
    memory: Memory
    verifier: Verifier
    report_renderer: ReportRenderer


def default_components(repository: AgentRepository, artifact_root: Path) -> AgentComponents:
    """创建默认组件集合；测试或扩展只需替换其中一个字段。"""

    return AgentComponents(
        planner=DefaultPlanner(),
        action_selector=DefaultActionSelector(),
        context_builder=DefaultContextBuilder(repository, artifact_root),
        memory=repository,
        # 默认实现本身已是完整、无状态组件，无需再包一层空子类。
        verifier=SuccessVerifier(),
        report_renderer=ReportGenerator(),
    )
