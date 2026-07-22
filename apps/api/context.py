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
from uuid import UUID

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
)
from yuwang.model_providers import ModelProvider, OpenAICompatibleProvider, ProviderChain
from yuwang.policy import PolicyEngine, SecurityConfig
from yuwang.settings import (
    AgentProfileService,
    AgentProfileVersion,
    ProviderConfig,
    SecretCipher,
    SettingsService,
)
from yuwang.settings.models import ProviderPreset, resolve_structured_mode
from yuwang.storage import SQLiteRepository
from yuwang.tooling import create_reference_registry
from yuwang.tooling.sdk import ToolRegistry


class ApiContext:
    """保存单个 API 应用共享的依赖和短生命周期运行状态。"""

    def __init__(self, config: Settings) -> None:
        self.config = config
        config.artifact_root.mkdir(parents=True, exist_ok=True)
        self.repository = SQLiteRepository(config.database_path)
        self.profile_service = AgentProfileService(self.repository)
        self.profile_service.ensure_default(self.repository.get_agent_defaults().budget)
        self.policy = PolicyEngine(SecurityConfig())
        self.registry: ToolRegistry = create_reference_registry(config.artifact_root)
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        # 会话只用于单实例自托管工作台；重启即失效，避免把管理员令牌存进浏览器。
        self.admin_sessions: dict[str, tuple[float, str]] = {}

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

    def verify_session(
        self,
        request: Request,
        authorization: str | None = None,
        csrf_token: str | None = None,
    ) -> tuple[float, str] | None:
        """验证管理员 Bearer 令牌或 HttpOnly 会话，并保护写请求免受 CSRF。"""

        if not self.config.admin_token:
            raise HTTPException(503, "管理员鉴权未配置")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() == "bearer" and secrets.compare_digest(token, self.config.admin_token):
            return None
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
        authorization: Annotated[str | None, Header()] = None,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> None:
        """FastAPI 依赖入口；管理路由显式声明该安全边界。"""

        self.verify_session(request, authorization, csrf_token)

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
        return thread

    def require_run(self, run_id: UUID) -> Run:
        run = self.repository.get_run(run_id)
        if not run:
            raise HTTPException(404, "运行不存在")
        return run

    def save_user_message(self, thread_id: UUID, body: MessageCreate) -> Message:
        """校验运行模式和附件归属后保存用户消息。"""

        thread = self.require_thread(thread_id)
        active = [
            run
            for run in self.repository.list_runs(thread_id)
            if run.status in ACTIVE_RUN_STATUSES
        ]
        if thread.mode == ThreadMode.COMPETITION and active:
            raise HTTPException(409, "competition 模式运行中禁止补充提示")
        for artifact_id in body.artifact_ids:
            artifact = self.repository.get_artifact(artifact_id)
            if not artifact or artifact.thread_id != thread_id:
                raise HTTPException(400, "附件引用无效")
        return self.repository.save_message(
            Message(
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
    ) -> TaskSpec:
        """把 HTTP 输入和 Thread/Profile 快照归一化为 Agent 唯一接受的 `TaskSpec`。"""

        messages = self.repository.list_messages(thread.id)
        user_messages = [item for item in messages if item.role == MessageRole.USER]
        if not user_messages:
            raise HTTPException(409, "请先发送任务消息")
        latest = user_messages[-1]
        return TaskSpec(
            body=latest.content,
            mode=thread.mode,
            artifact_ids=latest.artifact_ids,
            authorized_targets=create.authorized_targets,
            success_conditions=create.success_conditions,
            verification_rules=create.verification_rules,
            budget=profile.budget,
        )

    async def start_run(self, thread_id: UUID, body: RunCreate) -> Run:
        """创建 Run 的完整快照并调度执行，供不同 HTTP 入口共用。

        统一消息入口和保留的旧运行接口都经过这里，避免两条路径在 Provider、
        Profile 快照或持久化顺序上逐渐产生差异。
        """

        thread = self.require_thread(thread_id)
        thread.interaction_mode = InteractionMode.AGENT
        self.repository.save_thread(thread)
        profile = self.resolve_thread_profile(thread)
        try:
            selected_id = body.provider_config_id or profile.default_provider_id
            fallback_ids = profile.fallback_provider_ids if profile.default_provider_id else None
            provider_configs, provider = self.resolve_provider_chain(selected_id, fallback_ids)
            selected = provider_configs[0]
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        task = self.build_task(thread, body, profile)
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

    def stop_run(self, run_id: UUID) -> Run:
        """停止活跃 Run；等待检查点的 Run 也能立即变为终止状态。"""

        run = self.require_run(run_id)
        if run.status not in ACTIVE_RUN_STATUSES:
            raise HTTPException(409, "运行已结束")
        stopped = self.repository.request_stop(run_id)
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
            "admin": bool(self.config.admin_token),
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
