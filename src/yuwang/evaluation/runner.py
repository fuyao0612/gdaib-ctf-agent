"""声明式评测的最小执行器，复用正式 Agent、事件和快照持久化路径。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from yuwang.agent import AgentEngine
from yuwang.domain.models import (
    EventType,
    Message,
    MessageRole,
    Run,
    RunStatus,
    TaskSpec,
    Thread,
    ToolSnapshot,
)
from yuwang.model_providers import ModelProvider
from yuwang.policy import PolicyEngine
from yuwang.settings import AgentProfileInput, AgentProfileVersion, ProviderConfig
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolRegistry, ToolSpec

from .cases import BUILTIN_EVALUATION_CASES, EvaluationCase

EvaluationStatus = Literal["passed", "failed", "skipped"]


class EvaluationAssertionResult(BaseModel):
    """一个声明式断言在真实持久化记录上的检查结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assertion: str
    status: EvaluationStatus
    detail: str


class EvaluationResult(BaseModel):
    """单个评测用例的结果；跳过不等同于通过。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    status: EvaluationStatus
    run_id: UUID | None = None
    assertions: tuple[EvaluationAssertionResult, ...]
    reason: str | None = None


class EvaluationRunner:
    """仅在调用方注入已配置 Provider 时执行评测。

    生产代码不提供 Fake Provider，也不会为通过评测构造模型回答。测试可以显式
    注入 tests/ 中的替身，以验证该执行器确实经过正式的 Agent 与 SQLite 路径。
    """

    def __init__(
        self,
        database_path: Path,
        *,
        provider: ModelProvider | None = None,
        registry: ToolRegistry | None = None,
        policy: PolicyEngine | None = None,
        profile: AgentProfileVersion | None = None,
        provider_config: ProviderConfig | None = None,
        artifact_root: Path | None = None,
    ) -> None:
        self.repository = SQLiteRepository(database_path)
        self.provider = provider
        self.registry = registry or ToolRegistry()
        self.policy = policy or PolicyEngine()
        self.profile = profile or AgentProfileVersion(
            **AgentProfileInput(name="评测执行 Agent").model_dump(), version=1
        )
        self.provider_config = provider_config
        self.artifact_root = artifact_root or database_path.parent / "evaluation-artifacts"

    async def run(
        self, cases: Iterable[EvaluationCase] = BUILTIN_EVALUATION_CASES
    ) -> tuple[EvaluationResult, ...]:
        """顺序执行用例，避免共享 Provider 的限流掩盖单个用例结果。"""

        return tuple([await self.run_case(case) for case in cases])

    async def run_case(self, case: EvaluationCase) -> EvaluationResult:
        if not case.enabled:
            return self._skipped(case, "评测用例已停用")
        if self.provider is None:
            return self._skipped(case, "未显式注入已配置的真实 Provider")
        if case.expected_outcome in {"chat", "fallback"}:
            return self._skipped(case, "当前最小运行器只执行需要 Agent Run 的任务型用例")

        thread = self.repository.save_thread(Thread(title=f"评测：{case.name}"))
        messages = [
            self.repository.save_message(
                Message(thread_id=thread.id, role=MessageRole.USER, content=content)
            )
            for content in case.user_messages
        ]
        task = TaskSpec(
            body=messages[-1].content,
            origin_message_id=messages[-1].id,
            scenario=f"evaluation:{case.case_id}",
            budget=self.profile.budget,
            tool_snapshots=[self._tool_snapshot(spec) for spec in self.registry.specs()],
        )
        run = self.repository.save_run(
            Run(
                thread_id=thread.id,
                provider=self.provider_config.name if self.provider_config else self.provider.name,
                provider_config_id=self.provider_config.id if self.provider_config else None,
            )
        )
        self.repository.save_run_task(run.id, task)
        self.repository.save_run_agent_profile(run.id, self.profile)
        if self.provider_config:
            self.repository.save_provider_snapshot(run.id, [self.provider_config])
        engine = AgentEngine(
            self.repository,
            self.provider,
            self.registry,
            self.policy,
            profile=self.profile,
            artifact_root=self.artifact_root,
        )
        await engine.run(run.id, task)

        persisted = self.repository.get_run(run.id)
        if persisted is None:
            return EvaluationResult(
                case_id=case.case_id,
                status="failed",
                run_id=run.id,
                assertions=(
                    EvaluationAssertionResult(
                        assertion="Run 已持久化", status="failed", detail="运行记录丢失"
                    ),
                ),
                reason="正式运行记录未能读取",
            )
        assertions = self._evaluate_assertions(case, persisted, task)
        statuses = {item.status for item in assertions}
        status: EvaluationStatus = (
            "failed"
            if "failed" in statuses
            else "skipped"
            if "skipped" in statuses
            else "passed"
        )
        return EvaluationResult(
            case_id=case.case_id,
            status=status,
            run_id=run.id,
            assertions=assertions,
            reason=None if status == "passed" else "存在未满足或尚未映射的声明式断言",
        )

    def _skipped(self, case: EvaluationCase, reason: str) -> EvaluationResult:
        return EvaluationResult(
            case_id=case.case_id,
            status="skipped",
            assertions=tuple(
                EvaluationAssertionResult(assertion=value, status="skipped", detail=reason)
                for value in case.assertions
            ),
            reason=reason,
        )

    def _evaluate_assertions(
        self, case: EvaluationCase, run: Run, task: TaskSpec
    ) -> tuple[EvaluationAssertionResult, ...]:
        events = self.repository.list_events(run.id)
        event_types = {event.type for event in events}
        tool_calls = self.repository.list_tool_calls(run.id)
        task_snapshot = self.repository.get_run_task(run.id)
        profile_snapshot = self.repository.get_run_agent_profile(run.id)
        provider_snapshot = self.repository.get_provider_snapshot(run.id)

        return tuple(
            self._evaluate_assertion(
                assertion,
                case,
                run,
                event_types,
                bool(tool_calls),
                task_snapshot == task and bool(task_snapshot and task_snapshot.tool_snapshots),
                profile_snapshot == self.profile,
                provider_snapshot == [self.provider_config] if self.provider_config else False,
            )
            for assertion in case.assertions
        )

    @staticmethod
    def _evaluate_assertion(
        assertion: str,
        case: EvaluationCase,
        run: Run,
        event_types: set[EventType],
        has_tool_call: bool,
        has_tool_snapshot: bool,
        has_profile_snapshot: bool,
        has_provider_snapshot: bool,
    ) -> EvaluationAssertionResult:
        """只映射可由 Run、事件和快照客观证明的声明；其余明确标记为跳过。"""

        passed = EvaluationAssertionResult(assertion=assertion, status="passed", detail="已由持久化记录验证")
        failed = EvaluationAssertionResult(assertion=assertion, status="failed", detail="持久化记录不满足该断言")
        skipped = EvaluationAssertionResult(
            assertion=assertion,
            status="skipped",
            detail="当前最小运行器尚不能将该语义映射为确定性运行状态",
        )
        if assertion == "创建 Run":
            return passed
        if assertion == "不创建 Run":
            return failed
        if "工具快照" in assertion:
            return passed if has_tool_snapshot else failed
        if "Agent" in assertion and "快照" in assertion:
            return passed if has_profile_snapshot else failed
        if "Provider" in assertion and "快照" in assertion:
            return passed if has_provider_snapshot else skipped
        if "TOOL_STARTED" in assertion:
            return passed if EventType.TOOL_STARTED in event_types else failed
        if "TOOL_FINISHED" in assertion:
            return passed if EventType.TOOL_FINISHED in event_types else failed
        if "工具" in assertion and "执行" in assertion:
            return passed if has_tool_call else failed
        if "validation_status" in assertion:
            return passed if run.validation_status != "pending" else failed
        if "运行完成" in assertion or "状态为已完成" in assertion:
            return passed if run.status == RunStatus.COMPLETED else failed
        if "已停止" in assertion:
            return passed if run.status == RunStatus.STOPPED else failed
        if "等待澄清" in assertion:
            return passed if run.status == RunStatus.WAITING_CLARIFICATION else failed
        if "等待计划确认" in assertion:
            return passed if run.status == RunStatus.WAITING_APPROVAL else failed
        if case.expected_outcome == "rejected" and "拒绝" in assertion:
            return passed if run.status in {RunStatus.FAILED, RunStatus.STOPPED} else failed
        return skipped

    @staticmethod
    def _tool_snapshot(spec: ToolSpec) -> ToolSnapshot:
        """与 API 创建 Run 时相同地冻结可执行工具定义。"""

        return ToolSnapshot(
            tool_id=spec.id,
            namespace=spec.namespace,
            name=spec.name,
            display_name=spec.display_name or spec.name,
            version=spec.version,
            source_type=spec.source_type,
            source=spec.source,
            description=spec.description,
            capabilities=spec.capabilities,
            scenarios=spec.scenarios,
            risk=spec.risk,
            permissions=spec.permissions,
            requires_network=spec.requires_network,
            allowed_target_types=spec.allowed_target_types,
            timeout_seconds=spec.timeout_seconds,
            error_codes=spec.error_codes,
            idempotent=spec.idempotent,
            artifact_types=spec.artifact_types,
            input_schema=spec.input_schema,
            output_schema=spec.output_schema,
            config_schema=spec.config_schema,
            supports_cancellation=spec.supports_cancellation,
            supports_progress=spec.supports_progress,
        )


__all__ = [
    "EvaluationAssertionResult",
    "EvaluationResult",
    "EvaluationRunner",
    "EvaluationStatus",
]
