"""声明式评测的最小执行器，复用正式 Agent、事件和快照持久化路径。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from yuwang.agent import AgentEngine
from yuwang.chat import build_chat_messages
from yuwang.domain.models import (
    EventType,
    InteractionMode,
    Message,
    MessageRole,
    ModelCall,
    Run,
    RunStatus,
    TaskSpec,
    Thread,
    ToolCall,
    ToolSnapshot,
    utcnow,
)
from yuwang.model_providers import ModelProvider, ProviderCallMetrics, ProviderError
from yuwang.policy import PolicyEngine
from yuwang.settings import AgentProfileInput, AgentProfileVersion, ProviderConfig
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolRegistry, ToolSpec

from .cases import BUILTIN_EVALUATION_CASES, EvaluationCase
from .results import EvaluationRecord, EvaluationStatus, FailureCategory


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
    thread_id: UUID | None = None
    record_id: UUID | None = None
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
        self,
        cases: Iterable[EvaluationCase] = BUILTIN_EVALUATION_CASES,
        *,
        attempts: int = 1,
        completed_attempts: Iterable[tuple[str, int]] = (),
        on_attempt_completed: Callable[
            [EvaluationCase, int, EvaluationResult], Awaitable[None]
        ]
        | None = None,
    ) -> tuple[EvaluationResult, ...]:
        """顺序执行用例，避免共享 Provider 的限流掩盖单个用例结果。"""

        if attempts < 1:
            raise ValueError("评测尝试次数必须至少为 1")
        already_completed = set(completed_attempts)
        results: list[EvaluationResult] = []
        for case in cases:
            for attempt in range(1, min(attempts, case.max_attempts) + 1):
                if (case.case_id, attempt) in already_completed:
                    continue
                result = await self.run_case(case, attempt=attempt)
                results.append(result)
                if on_attempt_completed:
                    await on_attempt_completed(case, attempt, result)
        return tuple(results)

    async def run_case(self, case: EvaluationCase, *, attempt: int = 1) -> EvaluationResult:
        if not case.enabled:
            return self._skipped(case, "评测用例已停用", attempt)
        if self.provider is None:
            return self._skipped(case, "未显式注入已配置的真实 Provider", attempt)
        if case.expected_outcome == "chat":
            return await self._run_chat_case(case, attempt=attempt)
        if case.expected_outcome == "fallback":
            return self._skipped(case, "当前运行器未注入可控故障条件，不能伪造 Provider 失败", attempt)

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
            authorized_targets=list(case.authorized_targets),
            budget=case.budget,
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
            result = EvaluationResult(
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
            return self._persist_result(case, result, attempt, started_at=None, run=None)
        assertions = self._evaluate_assertions(case, persisted, task)
        statuses = {item.status for item in assertions}
        status: EvaluationStatus = (
            "failed"
            if "failed" in statuses
            else "skipped"
            if "skipped" in statuses
            else "passed"
        )
        result = EvaluationResult(
            case_id=case.case_id,
            status=status,
            run_id=run.id,
            assertions=assertions,
            reason=None if status == "passed" else "存在未满足或尚未映射的声明式断言",
        )
        return self._persist_result(case, result, attempt, started_at=run.created_at, run=persisted)

    async def _run_chat_case(
        self, case: EvaluationCase, *, attempt: int
    ) -> EvaluationResult:
        """走正式消息和 Provider 文本协议执行普通聊天，不为聊天伪造 Run。"""

        assert self.provider is not None
        started_at = utcnow()
        thread = self.repository.save_thread(
            Thread(
                title=f"评测聊天：{case.name}",
                interaction_mode=InteractionMode.CHAT,
                provider_config_id=self.provider_config.id if self.provider_config else None,
            )
        )
        defaults = self.repository.get_chat_defaults()
        metrics: list[ProviderCallMetrics] = []
        responses: list[Message] = []
        try:
            for content in case.user_messages:
                self.repository.save_message(
                    Message(thread_id=thread.id, role=MessageRole.USER, content=content)
                )
                prompt = build_chat_messages(
                    self.repository.list_messages(thread.id),
                    recent_limit=defaults.recent_message_limit,
                    token_limit=defaults.context_token_limit,
                )
                answer = await self.provider.generate_text(
                    prompt,
                    system_prompt=defaults.system_prompt,
                    timeout=self.provider_config.timeout_seconds if self.provider_config else None,
                )
                assistant = self.repository.save_message(
                    Message(thread_id=thread.id, role=MessageRole.ASSISTANT, content=answer)
                )
                responses.append(assistant)
                metric = getattr(self.provider, "last_call_metrics", None)
                if isinstance(metric, ProviderCallMetrics):
                    metrics.append(metric)
        except ProviderError:
            result = EvaluationResult(
                case_id=case.case_id,
                status="failed",
                thread_id=thread.id,
                assertions=tuple(
                    EvaluationAssertionResult(
                        assertion=value,
                        status="failed",
                        detail="正式 Provider 聊天调用失败",
                    )
                    for value in case.assertions
                ),
                reason="普通聊天的 Provider 调用失败",
            )
            return self._persist_result(
                case,
                result,
                attempt,
                started_at=started_at,
                run=None,
                metrics=metrics,
                trace_path=f"/api/v1/threads/{thread.id}",
                failure_category="provider_failure",
            )
        except Exception:
            result = EvaluationResult(
                case_id=case.case_id,
                status="failed",
                thread_id=thread.id,
                assertions=tuple(
                    EvaluationAssertionResult(
                        assertion=value,
                        status="failed",
                        detail="聊天评测内部执行失败",
                    )
                    for value in case.assertions
                ),
                reason="普通聊天评测内部执行失败",
            )
            return self._persist_result(
                case,
                result,
                attempt,
                started_at=started_at,
                run=None,
                metrics=metrics,
                trace_path=f"/api/v1/threads/{thread.id}",
                failure_category="internal_error",
            )

        assertions = self._evaluate_chat_assertions(case, thread, responses)
        status = self._result_status(assertions)
        result = EvaluationResult(
            case_id=case.case_id,
            status=status,
            thread_id=thread.id,
            assertions=assertions,
            reason=None if status == "passed" else "存在尚不能确定性验证的聊天语义断言",
        )
        return self._persist_result(
            case,
            result,
            attempt,
            started_at=started_at,
            run=None,
            metrics=metrics,
            trace_path=f"/api/v1/threads/{thread.id}",
        )

    def _skipped(self, case: EvaluationCase, reason: str, attempt: int) -> EvaluationResult:
        result = EvaluationResult(
            case_id=case.case_id,
            status="skipped",
            assertions=tuple(
                EvaluationAssertionResult(assertion=value, status="skipped", detail=reason)
                for value in case.assertions
            ),
            reason=reason,
        )
        return self._persist_result(case, result, attempt, started_at=None, run=None)

    def _persist_result(
        self,
        case: EvaluationCase,
        result: EvaluationResult,
        attempt: int,
        *,
        started_at: datetime | None,
        run: Run | None,
        metrics: Sequence[ProviderCallMetrics] = (),
        trace_path: str | None = None,
        failure_category: FailureCategory | None = None,
    ) -> EvaluationResult:
        """从正式 Run 统计指标，保存独立评测索引而不复制事件或报告正文。"""

        calls = self.repository.list_model_calls(run.id) if run else []
        tools = self.repository.list_tool_calls(run.id) if run else []
        report = self.repository.get_report(run.id) if run else None
        finished_at = run.finished_at if run and run.finished_at else datetime.now().astimezone()
        actual_started = started_at or finished_at
        duration_ms = max(0, int((finished_at - actual_started).total_seconds() * 1000))
        input_tokens = (
            sum(item.input_tokens for item in calls)
            if calls
            else sum(item.input_tokens for item in metrics)
        )
        output_tokens = (
            sum(item.output_tokens for item in calls)
            if calls
            else sum(item.output_tokens for item in metrics)
        )
        estimated_cost = (
            sum(float(item.metadata.get("cost", 0)) for item in calls)
            if calls
            else sum(item.cost for item in metrics)
        )
        provider = self.provider_config.name if self.provider_config else (
            run.provider if run else (metrics[-1].provider if metrics else None)
        )
        model = self.provider_config.model if self.provider_config else (
            metrics[-1].model if metrics else None
        )
        record = EvaluationRecord(
            case_id=case.case_id,
            category=case.category,
            difficulty=case.difficulty,
            provider=provider,
            model=model,
            attempt=attempt,
            started_at=actual_started,
            finished_at=finished_at,
            duration_ms=duration_ms,
            model_calls=(len(calls) if calls else sum(item.request_count for item in metrics)),
            tool_calls=len(tools),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            success=result.status == "passed",
            status=result.status,
            submitted_flag=None,
            flag_verified=bool(run and run.validation_status == "validated"),
            finish_reason=(run.error if run and run.error else result.reason or "断言全部通过"),
            failure_category=failure_category or self._failure_category(result, run, calls, tools),
            run_id=run.id if run else None,
            trace_path=trace_path or (f"/api/v1/runs/{run.id}/events" if run else None),
            report_path=f"/api/v1/runs/{run.id}/report" if report and run else None,
        )
        self.repository.save_evaluation_record(record)
        return result.model_copy(update={"record_id": record.id})

    @staticmethod
    def _result_status(
        assertions: Sequence[EvaluationAssertionResult],
    ) -> EvaluationStatus:
        statuses = {item.status for item in assertions}
        return "failed" if "failed" in statuses else "skipped" if "skipped" in statuses else "passed"

    def _evaluate_chat_assertions(
        self,
        case: EvaluationCase,
        thread: Thread,
        responses: Sequence[Message],
    ) -> tuple[EvaluationAssertionResult, ...]:
        """只对聊天生命周期能客观证明的声明给出通过结论。"""

        has_no_run = not self.repository.list_runs(thread.id)
        has_response = bool(responses and responses[-1].content.strip())
        results: list[EvaluationAssertionResult] = []
        for assertion in case.assertions:
            if assertion == "不创建 Run":
                results.append(
                    EvaluationAssertionResult(
                        assertion=assertion,
                        status="passed" if has_no_run else "failed",
                        detail="聊天消息未创建受控 Run" if has_no_run else "聊天意外创建了 Run",
                    )
                )
            elif "自然语言回复" in assertion:
                results.append(
                    EvaluationAssertionResult(
                        assertion=assertion,
                        status="passed" if has_response else "failed",
                        detail="已持久化非空助手回复" if has_response else "未持久化助手回复",
                    )
                )
            elif "不宣称外部验证" in assertion:
                results.append(
                    EvaluationAssertionResult(
                        assertion=assertion,
                        status="passed" if has_no_run else "failed",
                        detail="普通聊天未进入验证或报告路径",
                    )
                )
            elif "provider_config_id" in assertion:
                results.append(
                    EvaluationAssertionResult(
                        assertion=assertion,
                        status="passed" if self.provider_config else "skipped",
                        detail=(
                            "评测调用使用显式注入的 Provider 配置"
                            if self.provider_config
                            else "未注入可审计 Provider 配置"
                        ),
                    )
                )
            else:
                results.append(
                    EvaluationAssertionResult(
                        assertion=assertion,
                        status="skipped",
                        detail="当前聊天结果无法由确定性记录验证其语义",
                    )
                )
        return tuple(results)

    @staticmethod
    def _failure_category(
        result: EvaluationResult,
        run: Run | None,
        calls: Sequence[ModelCall],
        tools: Sequence[ToolCall],
    ) -> FailureCategory | None:
        if result.status == "passed":
            return None
        if result.status == "skipped":
            # 已真实执行的 Run 因断言尚未映射而跳过时，不能误报 Provider 不可用。
            return "provider_unavailable" if run is None else None
        error = (run.error if run and run.error else result.reason or "").casefold()
        if "上下文" in error:
            return "context_failure"
        if "步骤" in error:
            return "step_limit"
        if "超时" in error or "时间" in error:
            return "time_limit"
        if "预算" in error or "token" in error or "费用" in error:
            return "budget_limit"
        if "flag" in error or "候选" in error:
            return "wrong_flag"
        if run and run.status == RunStatus.STOPPED:
            return "agent_abandoned"
        if any(getattr(item, "status", None) == "failed" for item in tools):
            return "tool_failure"
        if any(getattr(item, "status", None) == "failed" for item in calls):
            return "provider_failure"
        return "assertion_failed"

    def _evaluate_assertions(
        self, case: EvaluationCase, run: Run, task: TaskSpec
    ) -> tuple[EvaluationAssertionResult, ...]:
        events = self.repository.list_events(run.id)
        event_types = {event.type for event in events}
        tool_calls = self.repository.list_tool_calls(run.id)
        task_snapshot = self.repository.get_run_task(run.id)
        profile_snapshot = self.repository.get_run_agent_profile(run.id)
        provider_snapshot = self.repository.get_provider_snapshot(run.id)
        has_task_snapshot = task_snapshot == task
        has_provider_snapshot = (
            provider_snapshot == [self.provider_config] if self.provider_config else False
        )
        # ProviderConfig 的持久化模型只保存 encrypted_api_key，不提供明文 api_key
        # 字段。这里验证快照契约，而不是读取或比较任何真实密钥。
        provider_snapshot_redacted = has_provider_snapshot and all(
            "api_key" not in value.model_dump(mode="json", exclude={"encrypted_api_key"})
            for value in provider_snapshot
        )

        return tuple(
            self._evaluate_assertion(
                assertion,
                case,
                run,
                event_types,
                bool(tool_calls),
                has_task_snapshot and bool(task_snapshot and task_snapshot.tool_snapshots),
                has_task_snapshot,
                profile_snapshot == self.profile,
                has_provider_snapshot,
                provider_snapshot_redacted,
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
        has_task_snapshot: bool,
        has_profile_snapshot: bool,
        has_provider_snapshot: bool,
        provider_snapshot_redacted: bool,
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
        if "公开任务说明" in assertion:
            return passed if has_task_snapshot else failed
        if "工具快照" in assertion:
            return passed if has_tool_snapshot else failed
        if "Agent" in assertion and "快照" in assertion:
            return passed if has_profile_snapshot else failed
        if "Provider" in assertion and "快照" in assertion:
            return passed if has_provider_snapshot else skipped
        if "快照不含明文 API Key" in assertion:
            return passed if provider_snapshot_redacted else failed
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
        if "执行完成与验证状态分离" in assertion:
            return (
                passed
                if run.status == RunStatus.COMPLETED
                and run.validation_status in {"unverified", "partial", "failed"}
                else failed
            )
        if "显示未验证或部分验证" in assertion:
            return (
                passed
                if run.validation_status in {"unverified", "partial"}
                else failed
            )
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
