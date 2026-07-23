"""对话、消息、附件与线程记忆路由。"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from apps.api.context import ApiContext
from apps.api.schemas import MemoryToggle, MessageCreate, ThreadCreate, ThreadUpdate
from yuwang.domain.models import (
    ACTIVE_RUN_STATUSES,
    Artifact,
    MemoryRecord,
    Message,
    Thread,
    utcnow,
)


def create_thread_router(context: ApiContext) -> APIRouter:
    """创建围绕 Thread 聚合根的 HTTP 路由。"""

    router = APIRouter(prefix="/api/v1", tags=["threads"])
    repository = context.repository

    @router.post("/threads", response_model=Thread, status_code=201)
    async def create_thread(body: ThreadCreate) -> Thread:
        profile = context.profile_service.resolve(body.agent_profile_id)
        provider_config_id = body.provider_config_id or context.default_thread_provider_id()
        if body.provider_config_id:
            try:
                context.resolve_provider_chain(body.provider_config_id)
            except (KeyError, ValueError) as exc:
                raise HTTPException(409, str(exc)) from exc
        return repository.save_thread(
            Thread(
                title=body.title,
                mode=body.mode,
                interaction_mode=body.interaction_mode,
                provider_config_id=provider_config_id,
                agent_profile_id=profile.profile_id,
                agent_profile_version=profile.version,
                plan_mode=body.plan_mode,
            )
        )

    @router.get("/threads", response_model=list[Thread])
    async def list_threads() -> list[Thread]:
        return [context.reconcile_thread_provider(thread) for thread in repository.list_threads()]

    @router.get("/threads/{thread_id}")
    async def get_thread(thread_id: UUID) -> dict[str, Any]:
        thread = context.require_thread(thread_id)
        return {
            **thread.model_dump(mode="json"),
            "messages": [
                item.model_dump(mode="json") for item in repository.list_messages(thread.id)
            ],
            "runs": [
                item.model_dump(mode="json") for item in repository.list_runs(thread.id)
            ],
            "artifacts": [
                item.model_dump(mode="json") for item in repository.list_artifacts(thread.id)
            ],
        }

    @router.patch("/threads/{thread_id}/archive", response_model=Thread)
    async def archive_thread(thread_id: UUID) -> Thread:
        thread = context.require_thread(thread_id)
        thread.archived = True
        thread.updated_at = utcnow()
        return repository.save_thread(thread)

    @router.patch("/threads/{thread_id}", response_model=Thread)
    async def update_thread(thread_id: UUID, body: ThreadUpdate) -> Thread:
        thread = context.require_thread(thread_id)
        if body.title is not None:
            thread.title = body.title.strip()
        if body.archived is not None:
            thread.archived = body.archived
        if body.interaction_mode is not None:
            thread.interaction_mode = body.interaction_mode
        if "provider_config_id" in body.model_fields_set:
            if body.provider_config_id is None:
                raise HTTPException(400, "请选择一个已启用的 Provider")
            try:
                context.resolve_provider_chain(body.provider_config_id)
            except (KeyError, ValueError) as exc:
                raise HTTPException(409, str(exc)) from exc
            # 只更新对话的下一次选择；Run 已保存自己的不可变 Provider 快照。
            thread.provider_config_id = body.provider_config_id
            thread.provider_fallback_notice = None
        if body.acknowledge_provider_fallback:
            thread.provider_fallback_notice = None
        thread.updated_at = utcnow()
        return repository.save_thread(thread)

    @router.delete("/threads/{thread_id}", status_code=204)
    async def delete_thread(thread_id: UUID) -> None:
        context.require_thread(thread_id)
        active = any(
            run.status in ACTIVE_RUN_STATUSES
            for run in repository.list_runs(thread_id)
        )
        if active:
            raise HTTPException(409, "请先停止正在运行的任务")
        repository.delete_thread(thread_id)

    @router.post("/threads/{thread_id}/messages", response_model=Message, status_code=201)
    async def send_message(thread_id: UUID, body: MessageCreate) -> Message:
        return context.save_user_message(thread_id, body)

    @router.post("/threads/{thread_id}/artifacts", response_model=Artifact, status_code=201)
    async def upload_artifact(
        thread_id: UUID,
        upload: Annotated[UploadFile, File()],
    ) -> Artifact:
        context.require_thread(thread_id)
        content = await upload.read(context.config.max_request_bytes + 1)
        filename = Path(upload.filename or "").name
        try:
            context.policy.validate_upload(
                filename,
                len(content),
                len(repository.list_artifacts(thread_id)),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        artifact_id = uuid4()
        storage_ref = f"{thread_id}/{artifact_id}.blob"
        destination = context.config.artifact_root / storage_ref
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        artifact = Artifact(
            id=artifact_id,
            thread_id=thread_id,
            filename=filename,
            kind="upload",
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            mime_type=(
                upload.content_type
                or mimetypes.guess_type(filename)[0]
                or "application/octet-stream"
            ),
            storage_ref=storage_ref,
        )
        return repository.save_artifact(artifact)

    @router.get("/threads/{thread_id}/artifacts", response_model=list[Artifact])
    async def list_artifacts(thread_id: UUID) -> list[Artifact]:
        context.require_thread(thread_id)
        return repository.list_artifacts(thread_id)

    @router.get("/artifacts/{artifact_id}/download")
    async def download_artifact(artifact_id: UUID) -> FileResponse:
        artifact = repository.get_artifact(artifact_id)
        if not artifact:
            raise HTTPException(404, "产物不存在")
        path = (context.config.artifact_root / artifact.storage_ref).resolve()
        if context.config.artifact_root.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "产物数据不存在")
        return FileResponse(path, filename=artifact.filename, media_type=artifact.mime_type)

    @router.get("/threads/{thread_id}/memories", response_model=list[MemoryRecord])
    async def list_thread_memories(thread_id: UUID) -> list[MemoryRecord]:
        context.require_thread(thread_id)
        return repository.list_memories(thread_id, enabled_only=False)

    @router.delete("/threads/{thread_id}/memories", status_code=204)
    async def clear_thread_memories(thread_id: UUID) -> None:
        context.require_thread(thread_id)
        repository.clear_memories(thread_id)

    @router.delete("/threads/{thread_id}/memories/{memory_id}", status_code=204)
    async def delete_thread_memory(thread_id: UUID, memory_id: UUID) -> None:
        context.require_thread(thread_id)
        memory = next(
            (
                item
                for item in repository.list_memories(thread_id, enabled_only=False)
                if item.id == memory_id
            ),
            None,
        )
        if not memory:
            raise HTTPException(404, "记忆不存在")
        repository.delete_memory(memory_id)

    @router.patch("/threads/{thread_id}/memories", status_code=204)
    async def toggle_thread_memories(thread_id: UUID, body: MemoryToggle) -> None:
        context.require_thread(thread_id)
        repository.set_memories_enabled(thread_id, body.enabled)

    return router
