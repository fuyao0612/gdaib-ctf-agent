"""API 路由共享的运行上下文。

路由只做 HTTP 输入输出转换；仓储、Provider 解密、Agent 调度和会话校验集中
在这里。这个对象不是服务定位器：它仅对应一个 FastAPI 应用实例，测试创建的
每个应用都有独立数据库、任务表和管理员会话。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Request

from apps.api.config import Settings
from apps.api.schemas import MessageCreate, RunCreate
from yuwang.agent import AgentEngine, AgentStateModel
from yuwang.domain.models import (
    ACTIVE_RUN_STATUSES,
    EventType,
    InteractionMode,
    Message,
    MessageRole,
    Run,
    RunStatus,
    TaskSpec,
    Thread,
    ThreadMode,
    ToolSnapshot,
)
from yuwang.model_providers import ModelProvider, OpenAICompatibleProvider, ProviderChain
from yuwang.policy import PolicyEngine, SecurityConfig
from yuwang.settings import (
    AgentProfileService,
    AgentProfileVersion,
    ProviderConfig,
    SecretCipher,
    SettingsService,
    SkillService,
)
from yuwang.settings.models import ProviderPreset, resolve_structured_mode
from yuwang.storage import SQLiteRepository
from yuwang.tooling import create_reference_registry
from yuwang.tooling.ctf import register_ctf_tools
from yuwang.tooling.mcp import McpService
from yuwang.tooling.mcp.client import McpClient
from yuwang.tooling.sdk import ToolRegistry


class ApiContext:
    """保存单个 API 应用共享的依赖和短生命周期运行状态。"""

    def __init__(self, config: Settings) -> None:
        self.config = config
        config.artifact_root.mkdir(parents=True, exist_ok=True)
        self.repository = SQLiteRepository(config.database_path)
        self.profile_service = AgentProfileService(self.repository)
        self.skill_service = SkillService(self.repository)
        self.profile_service.ensure_default(self.repository.get_agent_defaults().budget)
        self.policy = PolicyEngine(SecurityConfig())
        self.registry: ToolRegistry = create_reference_registry(config.artifact_root)
        register_ctf_tools(self.registry, self.repository, config.artifact_root)
        self.mcp_client = McpClient(
            allowed_commands={self._normalized_mcp_command(value) for value in config.mcp_stdio_allowed_commands},
            allow_insecure_local=config.allow_insecure_local_mcp,
        )
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        # 会话只用于单实例自托管工作台；重启即失效，浏览器仅保存 HttpOnly Cookie。
        self.admin_sessions: dict[str, tuple[float, str]] = {}

    @staticmethod
    def _normalized_mcp_command(value: str) -> str:
        from pathlib import Path

        return str(Path(value).resolve()).casefold()

    def cleanup_callback(self, run_id: UUID) -> Callable[[asyncio.Task[None]], None]:
        """后台运行结束后从内存索引移除，数据库记录仍完整保留。"""

        def cleanup(_: asyncio.Task[None]) -> None:
            self.tasks.pop(run_id, None)

        return cleanup

    def get_settings_service(self) -> SettingsService:
        """仅在主密钥有效时构造可解密 Provider 凭据的设置服务。"""

        if not self.config.master_key:
            raise HTTPException(503, "设置服务不可用：需要配置 YUWANG_MASTER_KEY")
        try:
            cipher = SecretCipher(self.config.master_key)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        return SettingsService(
            self.repository,
            cipher,
            allow_insecure_local=self.config.allow_insecure_local_provider,
        )

    def get_mcp_service(self) -> McpService:
        """MCP 认证与 Provider 密钥复用同一主密钥，但不向路由暴露明文。"""

        return McpService(self.repository, self.get_settings_service().cipher, self.mcp_client)

    def verify_session(
        self,
        request: Request,
        csrf_token: str | None = None,
    ) -> tuple[float, str]:
        """验证本机管理会话，并保护写请求免受 CSRF。"""

        session_id = request.cookies.get("yuwang_admin_session", "")
        session = self.admin_sessions.get(session_id)
        if not session or session[0] <= time.time():
            self.admin_sessions.pop(session_id, None)
            raise HTTPException(401, "管理员会话无效或已过期")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
            csrf_token or "", session[1]
        ):
            raise HTTPException(403, "管理员会话 CSRF 校验失败")
        return session

    def require_admin(
        self,
        request: Request,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> None:
        """FastAPI 依赖入口；管理路由显式声明该安全边界。"""

        self.verify_session(request, csrf_token)

    def build_provider_chain(self, provider_configs: list[ProviderConfig]) -> ProviderChain:
        """从已固化配置构造真实 Provider 链，恢复时也走同一条路径。"""

        service = self.get_settings_service()
        defaults = service.get_agent_defaults()
        providers: list[ModelProvider] = [
            OpenAICompatibleProvider(
                name=value.name,
                base_url=value.base_url,
                api_key=service.cipher.decrypt(value.encrypted_api_key),
                model=value.model,
                timeout_seconds=value.timeout_seconds,
                max_retries=min(value.max_retries, defaults.provider_retry_budget),
                structured_mode=resolve_structured_mode(value.preset, value.structured_mode),
                tool_call_mode=value.tool_call_mode,
                fallback_on=value.fallback_on,
                input_price_per_million=value.input_price_per_million,
                output_price_per_million=value.output_price_per_million,
                request_overrides=(
                    {"enable_thinking": False} if value.preset == ProviderPreset.QWEN else {}
                ),
            )
            for value in provider_configs
        ]
        return ProviderChain(providers, retry_budget=defaults.provider_retry_budget)

    def resolve_provider_chain(
        self,
        provider_config_id: UUID | None,
        fallback_ids: list[UUID] | None = None,
    ) -> tuple[list[ProviderConfig], ProviderChain]:
        configs = self.get_settings_service().resolve_chain(provider_config_id, fallback_ids)
        return configs, self.build_provider_chain(configs)

    def default_thread_provider_id(self) -> UUID | None:
        """解析新会话的全局默认模型，避免 ChatDefaults 改变会话级选择语义。"""

        service = self.get_settings_service()
        try:
            return service.resolve_chain()[0].id
        except ValueError:
            return None

    def reconcile_thread_provider(self, thread: Thread) -> Thread:
        """将已停用或已删除的会话选择安全回退，并保存一次性用户提示。"""

        try:
            service = self.get_settings_service()
        except HTTPException:
            return thread
        selected = None
        if thread.provider_config_id:
            try:
                selected = service.get_provider(thread.provider_config_id)
            except KeyError:
                selected = None
        if selected and selected.enabled:
            return thread
        fallback_id = self.default_thread_provider_id()
        if not fallback_id or fallback_id == thread.provider_config_id:
            return thread
        if thread.provider_config_id:
            thread.provider_fallback_notice = "原选择的模型不可用，已回退到全局默认模型。"
        thread.provider_config_id = fallback_id
        self.repository.save_thread(thread)
        return thread

    def resolve_thread_profile(self, thread: Thread) -> AgentProfileVersion:
        """旧 Thread 首次运行时绑定当前默认版本，之后始终使用该不可变版本。"""

        if thread.agent_profile_id and thread.agent_profile_version:
            return self.profile_service.require(
                thread.agent_profile_id, thread.agent_profile_version
            )
        profile = self.profile_service.resolve(None)
        thread.agent_profile_id = profile.profile_id
        thread.agent_profile_version = profile.version
        self.repository.save_thread(thread)
        return profile

    def require_thread(self, thread_id: UUID) -> Thread:
        thread = self.repository.get_thread(thread_id)
        if not thread:
            raise HTTPException(404, "对话不存在")
        return self.reconcile_thread_provider(thread)

    def require_run(self, run_id: UUID) -> Run:
        run = self.repository.get_run(run_id)
        if not run:
            raise HTTPException(404, "运行不存在")
        return run

    def validate_user_message_artifacts(
        self, thread_id: UUID, artifact_ids: list[UUID]
    ) -> Thread:
        """校验会话运行边界和附件归属，供统一消息的多个分支复用。"""

        thread = self.require_thread(thread_id)
        active = [
            run
            for run in self.repository.list_runs(thread_id)
            if run.status in ACTIVE_RUN_STATUSES
        ]
        if thread.mode == ThreadMode.COMPETITION and active:
            raise HTTPException(409, "competition 模式运行中禁止补充提示")
        for artifact_id in artifact_ids:
            artifact = self.repository.get_artifact(artifact_id)
            if not artifact or artifact.thread_id != thread_id:
                raise HTTPException(400, "附件引用无效")
        return thread

    def save_user_message(
        self,
        thread_id: UUID,
        body: MessageCreate,
        *,
        message_id: UUID | None = None,
        allow_active_competition: bool = False,
    ) -> Message:
        """校验运行模式和附件归属后保存用户消息。

        统一输入会把浏览器生成的 request_id 作为消息 ID。重连重发时先返回
        同一条消息，而不是依赖 SQLite 主键异常来判断重复请求。
        """

        if allow_active_competition:
            thread = self.require_thread(thread_id)
            for artifact_id in body.artifact_ids:
                artifact = self.repository.get_artifact(artifact_id)
                if not artifact or artifact.thread_id != thread.id:
                    raise HTTPException(400, "附件引用无效")
        else:
            self.validate_user_message_artifacts(thread_id, body.artifact_ids)
        if message_id is not None:
            existing = self.repository.get_message(message_id)
            if existing:
                if (
                    existing.thread_id != thread_id
                    or existing.role != MessageRole.USER
                    or existing.content != body.content
                    or existing.artifact_ids != body.artifact_ids
                ):
                    raise HTTPException(409, "请求 ID 已用于不同的消息内容")
                return existing
        return self.repository.save_message(
            Message(
                id=message_id or uuid4(),
                thread_id=thread_id,
                role=MessageRole.USER,
                content=body.content,
                artifact_ids=body.artifact_ids,
            )
        )

    def build_task(
        self,
        thread: Thread,
        create: RunCreate,
        profile: AgentProfileVersion,
        *,
        origin_message: Message | None = None,
    ) -> TaskSpec:
        """把 HTTP 输入和 Thread/Profile 快照归一化为 Agent 唯一接受的 `TaskSpec`。"""

        if origin_message is None:
            # 兼容旧的“先保存消息、再创建 Run”接口。统一消息入口必须显式传入
            # 自己刚持久化的消息，不能依赖线程中恰好排在最后的一条用户消息。
            messages = self.repository.list_messages(thread.id)
            user_messages = [item for item in messages if item.role == MessageRole.USER]
            if not user_messages:
                raise HTTPException(409, "请先发送任务消息")
            origin_message = user_messages[-1]
        if origin_message.thread_id != thread.id or origin_message.role != MessageRole.USER:
            raise HTTPException(409, "任务来源消息无效")
        # 旧兼容入口可传入更严格的临时规则；日常统一消息使用已版本化的
        # Profile 默认规则。两者都在 HTTP 模型层拒绝了万能正则。
        verification_rules = (
            create.verification_rules or profile.validation_policy.evidence_rules
        )
        tool_snapshots = [
            ToolSnapshot(
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
            for spec in self.registry.specs()
        ]
        return TaskSpec(
            body=origin_message.content,
            origin_message_id=origin_message.id,
            mode=thread.mode,
            artifact_ids=origin_message.artifact_ids,
            authorized_targets=create.authorized_targets,
            success_conditions=create.success_conditions,
            verification_rules=verification_rules,
            budget=profile.budget,
            skills=self.skill_service.snapshots_for(thread.skill_ids),
            tool_snapshots=tool_snapshots,
        )

    async def start_run(
        self,
        thread_id: UUID,
        body: RunCreate,
        *,
        origin_message: Message | None = None,
    ) -> Run:
        """创建 Run 的完整快照并调度执行，供不同 HTTP 入口共用。

        统一消息入口和保留的旧运行接口都经过这里，避免两条路径在 Provider、
        Profile 快照或持久化顺序上逐渐产生差异。
        """

        thread = self.require_thread(thread_id)
        thread.interaction_mode = InteractionMode.AGENT
        self.repository.save_thread(thread)
        profile = self.resolve_thread_profile(thread)
        try:
            selected_id = body.provider_config_id or thread.provider_config_id or profile.default_provider_id
            # 备用链只能来自 Agent Profile 的明确配置；对话选择不会隐式加入
            # 其他已启用 Provider，避免意外把任务发送给未选择的模型服务。
            fallback_ids = profile.fallback_provider_ids
            provider_configs, provider = self.resolve_provider_chain(selected_id, fallback_ids)
            selected = provider_configs[0]
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        try:
            task = self.build_task(thread, body, profile, origin_message=origin_message)
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        run = Run(
            thread_id=thread.id,
            provider=selected.name,
            provider_config_id=selected.id,
            agent_profile_id=profile.profile_id,
            agent_profile_version=profile.version,
            plan_mode=body.plan_mode or thread.plan_mode,
        )
        try:
            self.repository.save_run(run)
            self.repository.save_run_task(run.id, task)
            self.repository.save_provider_snapshot(run.id, provider_configs)
            self.repository.save_run_agent_profile(run.id, profile)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        self.schedule(run.id, self.execute(run, task, provider, profile))
        return run

    def stop_run(self, run_id: UUID, *, request_id: UUID | None = None) -> Run:
        """停止活跃 Run；等待检查点的 Run 也能立即变为终止状态。"""

        run = self.require_run(run_id)
        if run.status not in ACTIVE_RUN_STATUSES:
            if request_id is not None and run.stop_request_id == request_id:
                return run
            raise HTTPException(409, "运行已结束")
        if run.stop_requested:
            return run
        stopped = self.repository.request_stop(run_id, request_id=request_id)
        task = self.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        elif stopped.status in {
            RunStatus.WAITING_INPUT,
            RunStatus.WAITING_CLARIFICATION,
            RunStatus.WAITING_APPROVAL,
            RunStatus.PAUSED,
        }:
            stopped.transition(RunStatus.STOPPED, "用户在等待状态终止运行")
            self.repository.save_run(stopped)
            self.repository.create_event(
                stopped.id,
                EventType.RUN_STOPPED,
                "运行已由用户终止",
                {"from_waiting_state": True},
            )
        return stopped

    async def execute(
        self,
        run: Run,
        task: TaskSpec,
        provider: ProviderChain,
        profile: AgentProfileVersion,
        initial_state: AgentStateModel | None = None,
    ) -> None:
        """组装一次真实 Agent 运行并等待结束；结果、事件和报告由 Engine 持久化。"""

        engine = AgentEngine(
            self.repository,
            provider,
            self.registry,
            self.policy,
            profile=profile,
            artifact_root=self.config.artifact_root,
        )
        await engine.run(run.id, task, initial_state)

    def schedule(self, run_id: UUID, coroutine: Coroutine[object, object, None]) -> None:
        """登记 Agent 后台任务，使停止接口和应用退出都能找到它。"""

        task = asyncio.create_task(coroutine)
        self.tasks[run_id] = task
        task.add_done_callback(self.cleanup_callback(run_id))

    def deployment_checks(self) -> dict[str, bool]:
        """返回启动向导和 readiness 共用的真实依赖状态。"""

        try:
            self.repository.list_threads()
            database_ok = True
        except Exception:  # pragma: no cover - readiness 的最后防线
            database_ok = False
        master_key_ok = False
        if self.config.master_key:
            try:
                SecretCipher(self.config.master_key)
                master_key_ok = True
            except ValueError:
                # 就绪端点只公开布尔状态，避免把密钥格式或内部异常泄露给调用方。
                master_key_ok = False
        providers = self.repository.list_provider_configs()
        provider_ok = any(
            item.enabled and item.connection_status == "ok" for item in providers
        )
        agent_ok = False
        try:
            default_profile = self.profile_service.resolve(None)
            selected = (
                next(
                    (item for item in providers if item.id == default_profile.default_provider_id),
                    None,
                )
                if default_profile.default_provider_id
                else next((item for item in providers if item.is_default), None)
            )
            agent_ok = bool(selected and selected.enabled and selected.connection_status == "ok")
        except (KeyError, ValueError):
            agent_ok = False
        return {
            "database": database_ok,
            "master_key": master_key_ok,
            "admin": True,
            "provider": provider_ok,
            "agent": agent_ok,
        }

    @asynccontextmanager
    async def lifespan(self, _: FastAPI) -> AsyncIterator[None]:
        """启动时恢复可安全恢复的 Run，退出时取消仍在执行的协程。"""

        for thread in self.repository.list_threads():
            for stale in self.repository.list_runs(thread.id):
                if stale.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                    continue
                task_spec = self.repository.get_run_task(stale.id)
                snapshots = self.repository.get_provider_snapshot(stale.id)
                if not task_spec or not snapshots:
                    stale.transition(RunStatus.FAILED, "恢复所需快照缺失")
                    self.repository.save_run(stale)
                    continue
                try:
                    provider = self.build_provider_chain(snapshots)
                except (ValueError, HTTPException):
                    stale.transition(RunStatus.FAILED, "无法解密恢复所需的 Provider 快照")
                    self.repository.save_run(stale)
                    continue
                profile = self.repository.get_run_agent_profile(
                    stale.id
                ) or self.profile_service.resolve(None)
                engine = AgentEngine(
                    self.repository,
                    provider,
                    self.registry,
                    self.policy,
                    profile=profile,
                    artifact_root=self.config.artifact_root,
                )
                self.schedule(stale.id, engine.resume(stale.id, task_spec))
        yield
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
