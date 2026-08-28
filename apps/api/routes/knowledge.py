"""本地 RAG 知识库的管理与检索预览接口。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from apps.api.context import ApiContext
from yuwang.knowledge import (
    KnowledgeDocument,
    KnowledgeDocumentInput,
    KnowledgeDocumentUpdate,
    KnowledgeHit,
    KnowledgeSearchInput,
)


def create_knowledge_router(context: ApiContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin/knowledge", tags=["knowledge"])
    dependencies = [Depends(context.require_admin)]

    @router.get("/documents", response_model=list[KnowledgeDocument], dependencies=dependencies)
    async def list_documents() -> list[KnowledgeDocument]:
        return context.knowledge_service.list_documents()

    @router.post(
        "/documents",
        response_model=KnowledgeDocument,
        status_code=201,
        dependencies=dependencies,
    )
    async def create_document(body: KnowledgeDocumentInput) -> KnowledgeDocument:
        return context.knowledge_service.import_document(body)

    @router.put(
        "/documents/{document_id}",
        response_model=KnowledgeDocument,
        dependencies=dependencies,
    )
    async def update_document(
        document_id: UUID, body: KnowledgeDocumentUpdate
    ) -> KnowledgeDocument:
        try:
            return context.knowledge_service.update_document(document_id, body)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.delete(
        "/documents/{document_id}", status_code=204, dependencies=dependencies
    )
    async def delete_document(document_id: UUID) -> None:
        try:
            context.knowledge_service.delete_document(document_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/search", response_model=list[KnowledgeHit], dependencies=dependencies)
    async def preview_search(body: KnowledgeSearchInput) -> list[KnowledgeHit]:
        return context.knowledge_service.search(
            body.query,
            scenario=body.scenario,
            limit=body.limit,
            require_provider_context=False,
        )

    return router
