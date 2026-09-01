"""声明式评测的最小执行器，复用正式 Agent、事件和快照持久化路径。"""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from yuwang.agent import AgentEngine
from yuwang.domain.models import (
    Artifact,
    EventType,
    Message,
    MessageRole,
    ModelCall,
    Run,
    RunStatus,
    TaskSpec,
    Thread,
    ToolCall,
    ToolSnapshot,
)
from yuwang.model_providers import ModelProvider, ProviderCallMetrics
from yuwang.policy import PolicyEngine
from yuwang.settings import AgentProfileInput, AgentProfileVersion, ProviderConfig
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolRegistry, ToolSpec

from .cases import BUILTIN_EVALUATION_CASES, EvaluationCase
from .results import EvaluationRecord, EvaluationStatus, FailureCategory
from .scorer import CriterionResult, CriterionStatus, EvaluationScorer


class EvaluationAssertionResult(BaseModel):
    """一个声明式断言在真实持久化记录上的检查结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assertion: str
    status: EvaluationStatus | CriterionStatus
    detail: str
    criterion_id: str | None = None
    validator_type: str | None = None
    validator_version: str | None = None
    score: float = 0
    max_score: float = 0


class EvaluationResult(BaseModel):
    """单个评测用例的结果；跳过不等同于通过。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    status: EvaluationStatus
    run_id: UUID | None = None
    thread_id: UUID | None = None
    record_id: UUID | None = None
    assertions: tuple[EvaluationAssertionResult, ...]
    criteria: tuple[EvaluationAssertionResult, ...] = ()
    score: float = 0
    max_score: float = 0
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
        self.scorer = EvaluationScorer(self.repository)

    async def run(
        self,
        cases: Iterable[EvaluationCase] = BUILTIN_EVALUATION_CASES,
        *,
        attempts: int = 1,
        completed_attempts: Iterable[tuple[str, int]] = (),
        on_attempt_completed: Callable[[EvaluationCase, int, EvaluationResult], Awaitable[None]]
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
        registered_specs = self.registry.specs()
        registered_ids = {spec.id for spec in registered_specs}
        unknown_tool_ids = sorted(set(case.allowed_tools) - registered_ids)
        if unknown_tool_ids:
            return self._configuration_error(
                case,
                f"评测用例引用了未知、已停用或不可用的工具：{'、'.join(unknown_tool_ids)}",
                attempt,
            )
        if self.provider is None:
            return self._skipped(case, "未显式注入已配置的真实 Provider", attempt)
        allowed_tool_ids = set(case.allowed_tools)
        selected_specs = [spec for spec in registered_specs if spec.id in allowed_tool_ids]
        thread = self.repository.save_thread(Thread(title=f"评测：{case.name}"))
        messages = [
            self.repository.save_message(
                Message(thread_id=thread.id, role=MessageRole.USER, content=content)
            )
            for content in case.user_messages
        ]
        run = self.repository.save_run(
            Run(
                thread_id=thread.id,
                provider=self.provider_config.name if self.provider_config else self.provider.name,
                provider_config_id=self.provider_config.id if self.provider_config else None,
            )
        )
        artifact_ids = self._materialize_input_artifacts(case, thread.id, run.id)
        task = TaskSpec(
            body=messages[-1].content,
            origin_message_id=messages[-1].id,
            scenario=f"evaluation:{case.case_id}",
            authorized_targets=list(case.authorized_targets),
            artifact_ids=artifact_ids,
            budget=case.budget,
            tool_snapshots=[self._tool_snapshot(spec) for spec in selected_specs],
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
        assertions = self._evaluate_criteria(case, persisted, task)
        statuses = {item.status for item in assertions}
        failed_required = any(
            item.status != "passed"
            for item, criterion in zip(assertions, case.criteria, strict=False)
            if criterion.required
        )
        if case.criteria:
            status: EvaluationStatus = "failed" if failed_required else "passed"
        else:
            # 旧评测文件兼容：历史断言结果本身仍保留原状态，但新内置用例不会走这里。
            status = (
                "failed"
                if "failed" in statuses
                else "skipped"
                if "skipped" in statuses
                else "passed"
            )
        score = sum(item.score for item in assertions)
        max_score = sum(item.max_score for item in assertions)
        result = EvaluationResult(
            case_id=case.case_id,
            status=status,
            run_id=run.id,
            assertions=assertions,
            criteria=assertions,
            score=score,
            max_score=max_score,
            reason=None if status == "passed" else "存在未满足或尚未映射的声明式断言",
        )
        return self._persist_result(case, result, attempt, started_at=run.created_at, run=persisted)

    def _materialize_input_artifacts(
        self, case: EvaluationCase, thread_id: UUID, run_id: UUID
    ) -> list[UUID]:
        """将任务包附件写入受控根目录并绑定当前 Thread/Run。"""

        max_bytes = 2 * 1024 * 1024
        if not case.input_artifact_files:
            return []
        root = self.artifact_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        artifact_ids: list[UUID] = []
        for filename, content in case.input_artifact_files:
            safe_name = Path(filename).name
            if (
                not safe_name
                or safe_name in {".", ".."}
                or safe_name != filename
                or len(safe_name) > 255
            ):
                raise ValueError("评测输入 Artifact 文件名必须是安全的基名")
            if not isinstance(content, bytes):
                raise ValueError("评测输入 Artifact 内容必须是 bytes")
            if len(content) > max_bytes:
                raise ValueError(f"评测输入 Artifact 超过 {max_bytes} 字节限制")
            artifact_id = uuid4()
            storage_ref = f"{thread_id}/{artifact_id}.blob"
            destination = (root / storage_ref).resolve()
            if root not in destination.parents:
                raise ValueError("评测输入 Artifact 存储路径不安全")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            preview = content[:12_000].decode("utf-8", errors="replace") or None
            artifact = self.repository.save_artifact(
                Artifact(
                    id=artifact_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    filename=safe_name,
                    kind="evaluation_input",
                    sha256=hashlib.sha256(content).hexdigest(),
                    size=len(content),
                    mime_type=mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
                    storage_ref=storage_ref,
                    source="evaluation_package",
                    trust_level="untrusted",
                    preview=preview,
                    truncated=len(content) > 12_000,
                )
            )
            artifact_ids.append(artifact.id)
        return artifact_ids

    def _evaluate_criteria(
        self, case: EvaluationCase, run: Run, task: TaskSpec
    ) -> tuple[EvaluationAssertionResult, ...]:
        """优先使用类型化评分器；旧用例走只读兼容路径，不影响新评测契约。"""

        if not case.criteria:
            return self._evaluate_assertions(case, run, task)
        results = self.scorer.score(run, task, case.criteria)
        return tuple(self._criterion_result(item) for item in results)

    @staticmethod
    def _criterion_result(item: CriterionResult) -> EvaluationAssertionResult:
        return EvaluationAssertionResult(
            assertion=item.criterion_id,
            criterion_id=item.criterion_id,
            status=item.status,
            detail=item.detail,
            score=item.score,
            max_score=item.max_score,
            validator_type=item.validator_type,
            validator_version=item.validator_version,
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

    def _configuration_error(
        self,
        case: EvaluationCase,
        reason: str,
        attempt: int,
    ) -> EvaluationResult:
        """在创建 Run 前持久化评测配置错误，禁止把无效工具白名单降级为全量授权。"""

        assertions = tuple(
            EvaluationAssertionResult(
                assertion=value,
                status="configuration_error",
                detail=reason,
            )
            for value in case.assertions
        )
        criteria = tuple(
            EvaluationAssertionResult(
                assertion=criterion.criterion_id,
                criterion_id=criterion.criterion_id,
                status="configuration_error",
                detail=reason,
                validator_type=criterion.validator_type,
                validator_version="evaluation-runner/1.0",
                score=0,
                max_score=criterion.weight,
            )
            for criterion in case.criteria
        )
        result = EvaluationResult(
            case_id=case.case_id,
            status="failed",
            assertions=assertions,
            criteria=criteria,
            score=0,
            max_score=sum(item.max_score for item in criteria),
            reason=reason,
        )
        return self._persist_result(
            case,
            result,
            attempt,
            started_at=None,
            run=None,
            failure_category="configuration_error",
        )

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
        persisted_task = self.repository.get_run_task(run.id) if run else None
        events = self.repository.list_events(run.id) if run else []
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
        # Run 收尾时会固定真实成功调用的 Provider/模型；评测索引必须复用它，
        # 不能回填用户选择的配置快照，否则备用模型会在结果页被误报。
        provider = (
            run.provider
            if run
            else (
                metrics[-1].provider
                if metrics
                else (self.provider_config.name if self.provider_config else None)
            )
        )
        model = (
            run.model
            if run
            else (
                metrics[-1].model
                if metrics
                else (self.provider_config.model if self.provider_config else None)
            )
        )
        record = EvaluationRecord(
            case_id=case.case_id,
            case_version=case.version,
            scenario=(persisted_task.scenario if persisted_task else case.category),
            category=case.category,
            difficulty=case.difficulty,
            provider=provider,
            model=model,
            attempt=attempt,
            started_at=actual_started,
            finished_at=finished_at,
            duration_ms=duration_ms,
            model_calls=(len(calls) if calls else sum(item.request_count for item in metrics)),
            provider_requests=len(calls) if calls else sum(item.request_count for item in metrics),
            tool_calls=len(tools),
            tool_failures=sum(1 for item in tools if str(item.status) == "failed"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            success=result.status == "passed",
            status=result.status,
            execution_status=str(run.status) if run else "not_executed",
            validation_status=run.validation_status if run else "not_executed",
            submitted_flag=None,
            flag_verified=bool(run and run.validation_status == "validated"),
            finish_reason=(run.error if run and run.error else result.reason or "断言全部通过"),
            failure_category=failure_category or self._failure_category(result, run, calls, tools),
            run_id=run.id if run else None,
            trace_path=trace_path or (f"/api/v1/runs/{run.id}/events" if run else None),
            report_path=f"/api/v1/runs/{run.id}/report" if report and run else None,
            score=result.score,
            max_score=result.max_score,
            criterion_results=[item.model_dump(mode="json") for item in result.criteria],
            retry_count=max(0, attempt - 1),
            retries=max(0, attempt - 1),
            replans=sum(1 for event in events if str(event.type) == str(EventType.REPLANNED)),
            manual_interventions=0,
            context_compressions=0,
        )
        self.repository.save_evaluation_record(record)
        return result.model_copy(update={"record_id": record.id})

    @staticmethod
    def _result_status(
        assertions: Sequence[EvaluationAssertionResult],
    ) -> EvaluationStatus:
        statuses = {item.status for item in assertions}
        return (
            "failed" if "failed" in statuses else "skipped" if "skipped" in statuses else "passed"
        )

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
        criterion_statuses = {item.status for item in result.criteria}
        if "configuration_error" in criterion_statuses:
            return "configuration_error"
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

        passed = EvaluationAssertionResult(
            assertion=assertion, status="passed", detail="已由持久化记录验证"
        )
        failed = EvaluationAssertionResult(
            assertion=assertion, status="failed", detail="持久化记录不满足该断言"
        )
        skipped = EvaluationAssertionResult(
            assertion=assertion,
            status="skipped",
            detail="当前最小运行器尚不能将该语义映射为确定性运行状态",
        )
        if assertion == "创建 Run":
            return passed
        if "公开任务说明" in assertion:
            return passed if has_task_snapshot else failed
        if "原始请求" in assertion or "不扩大工具权限" in assertion:
            return passed if has_task_snapshot else failed
        if "不宣称外部验证" in assertion:
            return passed if run.validation_status != "validated" else failed
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
            return passed if run.validation_status in {"unverified", "partial"} else failed
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
            consumes=spec.consumes,
            produces=spec.produces,
            prerequisites=spec.prerequisites,
            enables=spec.enables,
            fallback_capabilities=spec.fallback_capabilities,
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
