from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from yuwang import __version__
from yuwang.agent import AgentEngine
from yuwang.domain.models import (
    Artifact,
    Message,
    MessageRole,
    Run,
    RunStatus,
    TaskSpec,
    Thread,
    ThreadMode,
    VerificationRule,
    utcnow,
)
from yuwang.model_providers import OpenAICompatibleProvider, ProviderChain, ProviderError
from yuwang.policy import PolicyEngine, SecurityConfig
from yuwang.settings import (
    AgentDefaults,
    AgentProfileExport,
    AgentProfileInput,
    AgentProfileService,
    AgentProfileVersion,
    ProviderConfig,
    ProviderConfigInput,
    ProviderConfigView,
    SafeTemplateRenderer,
    SecretCipher,
    SettingsService,
)
from yuwang.settings.models import PROVIDER_PRESETS, ProviderPreset, resolve_structured_mode
from yuwang.storage import SQLiteRepository
from yuwang.tooling import create_reference_registry


class Settings(BaseModel):
    database_path: Path = Path(os.getenv("YUWANG_DATABASE_PATH", "data/yuwang.db"))
    artifact_root: Path = Path(os.getenv("YUWANG_ARTIFACT_ROOT", "data/artifacts"))
    cors_origins: list[str] = Field(
        default_factory=lambda: os.getenv(
            "YUWANG_CORS_ORIGINS", "http://localhost:5173,http://localhost:8080"
        ).split(",")
    )
    max_request_bytes: int = 6 * 1024 * 1024
    admin_token: str = os.getenv("YUWANG_ADMIN_TOKEN", "")
    master_key: str = os.getenv("YUWANG_MASTER_KEY", "")
    allow_insecure_local_provider: bool = (
        os.getenv("YUWANG_ALLOW_INSECURE_LOCAL_PROVIDER", "false").lower() == "true"
    )


class ThreadCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    mode: ThreadMode = ThreadMode.NORMAL
    agent_profile_id: UUID | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    artifact_ids: list[UUID] = Field(default_factory=list)


class RunCreate(BaseModel):
    provider_config_id: UUID | None = None
    authorized_targets: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=lambda: ["reference_tool_succeeded"])
    verification_rules: list[VerificationRule] = Field(default_factory=list)


class ErrorBody(BaseModel):
    code: str
    message: str


class ProfileCopy(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TemplatePreview(BaseModel):
    template: str = Field(min_length=1, max_length=20_000)
    values: dict[str, Any] = Field(default_factory=dict)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    config.artifact_root.mkdir(parents=True, exist_ok=True)
    repository = SQLiteRepository(config.database_path)
    profile_service = AgentProfileService(repository)
    profile_service.ensure_default(repository.get_agent_defaults().budget)
    policy = PolicyEngine(SecurityConfig())
    registry = create_reference_registry(config.artifact_root)
    tasks: dict[UUID, asyncio.Task[None]] = {}

    def cleanup_callback(run_id: UUID) -> Callable[[asyncio.Task[None]], None]:
        def cleanup(_: asyncio.Task[None]) -> None:
            tasks.pop(run_id, None)

        return cleanup

    def get_settings_service() -> SettingsService:
        if not config.master_key:
            raise HTTPException(503, "设置服务不可用：需要配置 YUWANG_MASTER_KEY")
        try:
            cipher = SecretCipher(config.master_key)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        return SettingsService(
            repository,
            cipher,
            allow_insecure_local=config.allow_insecure_local_provider,
        )

    def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        if not config.admin_token:
            raise HTTPException(503, "管理员鉴权未配置")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(token, config.admin_token):
            raise HTTPException(401, "管理员鉴权失败")

    def build_provider_chain_from_configs(provider_configs: list[ProviderConfig]) -> ProviderChain:
        service = get_settings_service()
        defaults = service.get_agent_defaults()
        return ProviderChain(
            [
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
            ],
            retry_budget=defaults.provider_retry_budget,
        )

    def resolve_provider_chain(
        provider_config_id: UUID | None,
        fallback_ids: list[UUID] | None = None,
    ) -> tuple[list[ProviderConfig], ProviderChain]:
        provider_configs = get_settings_service().resolve_chain(provider_config_id, fallback_ids)
        return provider_configs, build_provider_chain_from_configs(provider_configs)

    def resolve_thread_profile(thread: Thread) -> AgentProfileVersion:
        if thread.agent_profile_id and thread.agent_profile_version:
            return profile_service.require(thread.agent_profile_id, thread.agent_profile_version)
        profile = profile_service.resolve(None)
        thread.agent_profile_id = profile.profile_id
        thread.agent_profile_version = profile.version
        repository.save_thread(thread)
        return profile

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        for thread in repository.list_threads():
            for stale in repository.list_runs(thread.id):
                if stale.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
                    task_spec = repository.get_run_task(stale.id)
                    snapshots = repository.get_provider_snapshot(stale.id)
                    if not task_spec or not snapshots:
                        stale.transition(RunStatus.FAILED, "恢复所需快照缺失")
                        repository.save_run(stale)
                        continue
                    try:
                        provider = build_provider_chain_from_configs(snapshots)
                    except (ValueError, HTTPException):
                        stale.transition(RunStatus.FAILED, "无法解密恢复所需的 Provider 快照")
                        repository.save_run(stale)
                        continue
                    engine = AgentEngine(repository, provider, registry, policy)
                    task_handle = asyncio.create_task(engine.resume(stale.id, task_spec))
                    tasks[stale.id] = task_handle
                    task_handle.add_done_callback(cleanup_callback(stale.id))
        yield
        for task in tasks.values():
            if not task.done():
                task.cancel()

    application = FastAPI(
        title="御网智元 API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/v1/openapi.json",
    )
    application.state.repository = repository
    application.state.settings = config
    application.state.registry = registry
    application.state.tasks = tasks
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        allow_credentials=False,
    )

    @application.middleware("http")
    async def request_size_limit(request: Request, call_next: Any) -> Any:
        length = request.headers.get("content-length")
        if length and int(length) > config.max_request_bytes:
            return JSONResponse(
                status_code=413,
                content={"error": {"code": "request_too_large", "message": "请求体超过限制"}},
            )
        return await call_next(request)

    @application.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": f"http_{exc.status_code}", "message": detail}},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": "请求参数校验失败"}},
        )

    def require_thread(thread_id: UUID) -> Thread:
        thread = repository.get_thread(thread_id)
        if not thread:
            raise HTTPException(404, "对话不存在")
        return thread

    def require_run(run_id: UUID) -> Run:
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(404, "运行不存在")
        return run

    def build_task(
        thread: Thread, create: RunCreate, profile: AgentProfileVersion
    ) -> TaskSpec:
        messages = repository.list_messages(thread.id)
        user_messages = [message for message in messages if message.role == MessageRole.USER]
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

    async def execute(run: Run, task: TaskSpec, provider: ProviderChain) -> None:
        engine = AgentEngine(repository, provider, registry, policy)
        await engine.run(run.id, task)

    @application.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.post("/api/v1/threads", response_model=Thread, status_code=201)
    async def create_thread(body: ThreadCreate) -> Thread:
        profile = profile_service.resolve(body.agent_profile_id)
        return repository.save_thread(
            Thread(
                title=body.title,
                mode=body.mode,
                agent_profile_id=profile.profile_id,
                agent_profile_version=profile.version,
            )
        )

    @application.get("/api/v1/threads", response_model=list[Thread])
    async def list_threads() -> list[Thread]:
        return repository.list_threads()

    @application.get("/api/v1/threads/{thread_id}")
    async def get_thread(thread_id: UUID) -> dict[str, Any]:
        thread = require_thread(thread_id)
        return {
            **thread.model_dump(mode="json"),
            "messages": [
                item.model_dump(mode="json") for item in repository.list_messages(thread.id)
            ],
            "runs": [item.model_dump(mode="json") for item in repository.list_runs(thread.id)],
            "artifacts": [
                item.model_dump(mode="json") for item in repository.list_artifacts(thread.id)
            ],
        }

    @application.patch("/api/v1/threads/{thread_id}/archive", response_model=Thread)
    async def archive_thread(thread_id: UUID) -> Thread:
        thread = require_thread(thread_id)
        thread.archived = True
        thread.updated_at = utcnow()
        return repository.save_thread(thread)

    @application.post(
        "/api/v1/threads/{thread_id}/messages", response_model=Message, status_code=201
    )
    async def send_message(thread_id: UUID, body: MessageCreate) -> Message:
        thread = require_thread(thread_id)
        active = [
            run
            for run in repository.list_runs(thread_id)
            if run.status in {RunStatus.QUEUED, RunStatus.RUNNING}
        ]
        if thread.mode == ThreadMode.COMPETITION and active:
            raise HTTPException(409, "competition 模式运行中禁止补充提示")
        for artifact_id in body.artifact_ids:
            artifact = repository.get_artifact(artifact_id)
            if not artifact or artifact.thread_id != thread_id:
                raise HTTPException(400, "附件引用无效")
        return repository.save_message(
            Message(
                thread_id=thread_id,
                role=MessageRole.USER,
                content=body.content,
                artifact_ids=body.artifact_ids,
            )
        )

    @application.post(
        "/api/v1/threads/{thread_id}/artifacts", response_model=Artifact, status_code=201
    )
    async def upload_artifact(thread_id: UUID, upload: Annotated[UploadFile, File()]) -> Artifact:
        require_thread(thread_id)
        content = await upload.read(config.max_request_bytes + 1)
        filename = Path(upload.filename or "").name
        try:
            policy.validate_upload(
                filename, len(content), len(repository.list_artifacts(thread_id))
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        artifact_id = uuid4()
        storage_ref = f"{thread_id}/{artifact_id}.blob"
        destination = config.artifact_root / storage_ref
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        artifact = Artifact(
            id=artifact_id,
            thread_id=thread_id,
            filename=filename,
            kind="upload",
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            mime_type=upload.content_type
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream",
            storage_ref=storage_ref,
        )
        return repository.save_artifact(artifact)

    @application.get("/api/v1/threads/{thread_id}/artifacts", response_model=list[Artifact])
    async def list_artifacts(thread_id: UUID) -> list[Artifact]:
        require_thread(thread_id)
        return repository.list_artifacts(thread_id)

    @application.get("/api/v1/artifacts/{artifact_id}/download")
    async def download_artifact(artifact_id: UUID) -> FileResponse:
        artifact = repository.get_artifact(artifact_id)
        if not artifact:
            raise HTTPException(404, "产物不存在")
        path = (config.artifact_root / artifact.storage_ref).resolve()
        if config.artifact_root.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "产物数据不存在")
        return FileResponse(path, filename=artifact.filename, media_type=artifact.mime_type)

    @application.post("/api/v1/threads/{thread_id}/runs", response_model=Run, status_code=202)
    async def start_run(thread_id: UUID, body: RunCreate = Body(default_factory=RunCreate)) -> Run:
        thread = require_thread(thread_id)
        profile = resolve_thread_profile(thread)
        try:
            selected_id = body.provider_config_id or profile.default_provider_id
            fallback_ids = profile.fallback_provider_ids if profile.default_provider_id else None
            provider_configs, provider = resolve_provider_chain(selected_id, fallback_ids)
            selected = provider_configs[0]
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        task = build_task(thread, body, profile)
        run = Run(
            thread_id=thread.id,
            provider=selected.name,
            provider_config_id=selected.id,
            agent_profile_id=profile.profile_id,
            agent_profile_version=profile.version,
        )
        try:
            repository.save_run(run)
            repository.save_run_task(run.id, task)
            repository.save_provider_snapshot(run.id, provider_configs)
            repository.save_run_agent_profile(run.id, profile)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        task_handle = asyncio.create_task(execute(run, task, provider))
        tasks[run.id] = task_handle
        task_handle.add_done_callback(cleanup_callback(run.id))
        return run

    @application.get("/api/v1/runs/{run_id}", response_model=Run)
    async def get_run(run_id: UUID) -> Run:
        return require_run(run_id)

    @application.post("/api/v1/runs/{run_id}/stop", response_model=Run)
    async def stop_run(run_id: UUID) -> Run:
        run = require_run(run_id)
        if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
            raise HTTPException(409, "运行已结束")
        return repository.request_stop(run_id)

    @application.post("/api/v1/runs/{run_id}/retry", response_model=Run, status_code=202)
    async def retry_run(run_id: UUID) -> Run:
        previous = require_run(run_id)
        if previous.status not in {RunStatus.FAILED, RunStatus.STOPPED}:
            raise HTTPException(409, "仅失败或停止的运行可重试")
        thread = require_thread(previous.thread_id)
        task = repository.get_run_task(previous.id)
        if not task:
            raise HTTPException(409, "原运行缺少 TaskSpec 快照，无法安全重试")
        try:
            provider_configs = repository.get_provider_snapshot(previous.id)
            if not provider_configs:
                raise ValueError("原运行缺少 Provider 快照")
            provider = build_provider_chain_from_configs(provider_configs)
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        profile = repository.get_run_agent_profile(previous.id) or profile_service.resolve(None)
        retried = Run(
            thread_id=thread.id,
            provider=previous.provider,
            provider_config_id=previous.provider_config_id,
            agent_profile_id=profile.profile_id,
            agent_profile_version=profile.version,
            attempt=previous.attempt + 1,
        )
        repository.save_run(retried)
        repository.save_run_task(retried.id, task)
        repository.save_provider_snapshot(retried.id, provider_configs)
        repository.save_run_agent_profile(retried.id, profile)
        task_handle = asyncio.create_task(execute(retried, task, provider))
        tasks[retried.id] = task_handle
        task_handle.add_done_callback(cleanup_callback(retried.id))
        return retried

    @application.get("/api/v1/runs/{run_id}/events")
    async def list_events(run_id: UUID, after: int = Query(0, ge=0)) -> list[dict[str, Any]]:
        require_run(run_id)
        return [event.model_dump(mode="json") for event in repository.list_events(run_id, after)]

    @application.get("/api/v1/runs/{run_id}/audit")
    async def run_audit(run_id: UUID) -> dict[str, Any]:
        require_run(run_id)
        return {
            "model_calls": [
                value.model_dump(mode="json") for value in repository.list_model_calls(run_id)
            ],
            "tool_calls": [
                value.model_dump(mode="json") for value in repository.list_tool_calls(run_id)
            ],
            "evidence": [
                value.model_dump(mode="json") for value in repository.list_evidence(run_id)
            ],
            "checkpoints": [
                {
                    "checkpoint_sequence": value.checkpoint_sequence,
                    "node": value.node,
                    "state_schema_version": value.state_schema_version,
                    "elapsed_seconds": value.elapsed_seconds,
                    "created_at": value.created_at,
                }
                for value in repository.list_checkpoints(run_id)
            ],
        }

    @application.get("/api/v1/runs/{run_id}/events/stream")
    async def stream_events(
        run_id: UUID,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        after: int = Query(0, ge=0),
    ) -> StreamingResponse:
        require_run(run_id)
        cursor = max(after, int(last_event_id or 0))

        async def generate() -> AsyncIterator[str]:
            nonlocal cursor
            idle = 0
            while True:
                if await request.is_disconnected():
                    return
                events = repository.list_events(run_id, cursor)
                for event in events:
                    cursor = event.sequence
                    # Keep the domain event type in the versioned JSON payload. Using the
                    # default SSE message event lets browser EventSource.onmessage handle
                    # every version without registering a hard-coded listener list.
                    yield f"id: {event.sequence}\ndata: {event.model_dump_json()}\n\n"
                run = repository.get_run(run_id)
                if (
                    run
                    and run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}
                    and not repository.list_events(run_id, cursor)
                ):
                    return
                if not events:
                    idle += 1
                    if idle % 20 == 0:
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.1)
                else:
                    idle = 0

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.get("/api/v1/runs/{run_id}/report")
    async def report_preview(run_id: UUID) -> dict[str, Any]:
        require_run(run_id)
        report = repository.get_report(run_id)
        if not report:
            raise HTTPException(404, "报告尚未生成")
        return {"markdown": report[0], "data": report[1]}

    @application.get("/api/v1/runs/{run_id}/report.md")
    async def report_markdown(run_id: UUID) -> PlainTextResponse:
        report = repository.get_report(run_id)
        if not report:
            raise HTTPException(404, "报告尚未生成")
        return PlainTextResponse(
            report[0],
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="report-{run_id}.md"'},
        )

    @application.get("/api/v1/runs/{run_id}/report.json")
    async def report_json(run_id: UUID) -> JSONResponse:
        report = repository.get_report(run_id)
        if not report:
            raise HTTPException(404, "报告尚未生成")
        return JSONResponse(
            report[1],
            headers={"Content-Disposition": f'attachment; filename="report-{run_id}.json"'},
        )

    @application.get("/api/v1/providers")
    async def providers() -> list[ProviderConfigView]:
        if not config.master_key:
            return []
        return get_settings_service().list_providers(enabled_only=True)

    @application.get(
        "/api/v1/admin/settings/agent-profiles",
        response_model=list[AgentProfileVersion],
        dependencies=[Depends(require_admin)],
    )
    async def admin_list_agent_profiles() -> list[AgentProfileVersion]:
        return repository.list_agent_profiles()

    @application.post(
        "/api/v1/admin/settings/agent-profiles",
        response_model=AgentProfileVersion,
        status_code=201,
        dependencies=[Depends(require_admin)],
    )
    async def admin_create_agent_profile(body: AgentProfileInput) -> AgentProfileVersion:
        try:
            return profile_service.create(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.get(
        "/api/v1/admin/settings/agent-profiles/export",
        response_model=AgentProfileExport,
        dependencies=[Depends(require_admin)],
    )
    async def admin_export_agent_profiles(
        profile_id: UUID | None = None,
    ) -> AgentProfileExport:
        try:
            return profile_service.export(profile_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @application.post(
        "/api/v1/admin/settings/agent-profiles/import",
        response_model=list[AgentProfileVersion],
        dependencies=[Depends(require_admin)],
    )
    async def admin_import_agent_profiles(
        body: AgentProfileExport,
    ) -> list[AgentProfileVersion]:
        try:
            return profile_service.import_profiles(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.post(
        "/api/v1/admin/settings/agent-profiles/template-preview",
        dependencies=[Depends(require_admin)],
    )
    async def admin_preview_agent_template(body: TemplatePreview) -> dict[str, str]:
        try:
            return {"rendered": SafeTemplateRenderer.render(body.template, body.values)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.get(
        "/api/v1/admin/settings/agent-profiles/{profile_id}",
        response_model=AgentProfileVersion,
        dependencies=[Depends(require_admin)],
    )
    async def admin_get_agent_profile(
        profile_id: UUID, version: int | None = None
    ) -> AgentProfileVersion:
        try:
            return profile_service.require(profile_id, version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @application.get(
        "/api/v1/admin/settings/agent-profiles/{profile_id}/versions",
        response_model=list[AgentProfileVersion],
        dependencies=[Depends(require_admin)],
    )
    async def admin_list_agent_profile_versions(
        profile_id: UUID,
    ) -> list[AgentProfileVersion]:
        profile_service.require(profile_id)
        return repository.list_agent_profile_versions(profile_id)

    @application.put(
        "/api/v1/admin/settings/agent-profiles/{profile_id}",
        response_model=AgentProfileVersion,
        dependencies=[Depends(require_admin)],
    )
    async def admin_update_agent_profile(
        profile_id: UUID, body: AgentProfileInput
    ) -> AgentProfileVersion:
        try:
            return profile_service.update(profile_id, body)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.post(
        "/api/v1/admin/settings/agent-profiles/{profile_id}/copy",
        response_model=AgentProfileVersion,
        dependencies=[Depends(require_admin)],
    )
    async def admin_copy_agent_profile(
        profile_id: UUID, body: ProfileCopy
    ) -> AgentProfileVersion:
        try:
            return profile_service.copy(profile_id, body.name)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @application.post(
        "/api/v1/admin/settings/agent-profiles/{profile_id}/default",
        response_model=AgentProfileVersion,
        dependencies=[Depends(require_admin)],
    )
    async def admin_default_agent_profile(profile_id: UUID) -> AgentProfileVersion:
        try:
            return profile_service.set_default(profile_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @application.post(
        "/api/v1/admin/settings/agent-profiles/{profile_id}/rollback/{version}",
        response_model=AgentProfileVersion,
        dependencies=[Depends(require_admin)],
    )
    async def admin_rollback_agent_profile(
        profile_id: UUID, version: int
    ) -> AgentProfileVersion:
        try:
            return profile_service.rollback(profile_id, version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @application.delete(
        "/api/v1/admin/settings/agent-profiles/{profile_id}",
        status_code=204,
        dependencies=[Depends(require_admin)],
    )
    async def admin_delete_agent_profile(profile_id: UUID) -> None:
        try:
            profile_service.delete(profile_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @application.get("/api/v1/provider-presets")
    async def provider_presets() -> dict[str, dict[str, Any]]:
        return {key.value: value for key, value in PROVIDER_PRESETS.items()}

    @application.get(
        "/api/v1/admin/settings/providers",
        response_model=list[ProviderConfigView],
        dependencies=[Depends(require_admin)],
    )
    async def admin_list_providers() -> list[ProviderConfigView]:
        return get_settings_service().list_providers()

    @application.post(
        "/api/v1/admin/settings/providers",
        response_model=ProviderConfigView,
        status_code=201,
        dependencies=[Depends(require_admin)],
    )
    async def admin_create_provider(body: ProviderConfigInput) -> ProviderConfigView:
        try:
            return get_settings_service().create_provider(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.put(
        "/api/v1/admin/settings/providers/{provider_id}",
        response_model=ProviderConfigView,
        dependencies=[Depends(require_admin)],
    )
    async def admin_update_provider(
        provider_id: UUID, body: ProviderConfigInput
    ) -> ProviderConfigView:
        try:
            return get_settings_service().update_provider(provider_id, body)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @application.delete(
        "/api/v1/admin/settings/providers/{provider_id}",
        status_code=204,
        dependencies=[Depends(require_admin)],
    )
    async def admin_delete_provider(provider_id: UUID) -> None:
        try:
            get_settings_service().delete_provider(provider_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @application.post(
        "/api/v1/admin/settings/providers/{provider_id}/test",
        dependencies=[Depends(require_admin)],
    )
    async def admin_test_provider(provider_id: UUID) -> dict[str, Any]:
        service = get_settings_service()
        try:
            value = service.get_provider(provider_id)
            provider = OpenAICompatibleProvider(
                name=value.name,
                base_url=value.base_url,
                api_key=service.decrypt_api_key(value.id),
                model=value.model,
                timeout_seconds=value.timeout_seconds,
                max_retries=value.max_retries,
                structured_mode=resolve_structured_mode(value.preset, value.structured_mode),
                fallback_on=value.fallback_on,
                input_price_per_million=value.input_price_per_million,
                output_price_per_million=value.output_price_per_million,
                request_overrides=(
                    {"enable_thinking": False} if value.preset == ProviderPreset.QWEN else {}
                ),
            )
            metrics = await provider.test_connection()
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(502, f"连接测试失败：{exc}") from exc
        return {
            "status": "ok",
            "provider": metrics.provider,
            "model": metrics.model,
            "structured_mode": provider.structured_mode,
            "latency_ms": metrics.duration_ms,
            "usage_reported": metrics.usage_reported,
        }

    @application.get(
        "/api/v1/admin/settings/providers/{provider_id}/models",
        dependencies=[Depends(require_admin)],
    )
    async def admin_discover_provider_models(provider_id: UUID) -> dict[str, Any]:
        service = get_settings_service()
        try:
            value = service.get_provider(provider_id)
            provider = OpenAICompatibleProvider(
                name=value.name,
                base_url=value.base_url,
                api_key=service.decrypt_api_key(value.id),
                model=value.model,
                timeout_seconds=value.timeout_seconds,
                max_retries=0,
                structured_mode=resolve_structured_mode(value.preset, value.structured_mode),
            )
            models = await provider.discover_models()
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(502, f"模型发现失败：{exc}") from exc
        return {"models": models, "manual_model_supported": True}

    @application.get(
        "/api/v1/admin/settings/agent",
        response_model=AgentDefaults,
        dependencies=[Depends(require_admin)],
    )
    async def admin_get_agent_defaults() -> AgentDefaults:
        return get_settings_service().get_agent_defaults()

    @application.put(
        "/api/v1/admin/settings/agent",
        response_model=AgentDefaults,
        dependencies=[Depends(require_admin)],
    )
    async def admin_update_agent_defaults(body: AgentDefaults) -> AgentDefaults:
        return get_settings_service().save_agent_defaults(body)

    @application.get("/api/v1/tools")
    async def tools() -> list[dict[str, Any]]:
        return [spec.model_dump(mode="json") for spec in registry.specs()]

    return application


app = create_app()
