"""本地、确定性的稀疏检索 RAG 服务。"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol
from uuid import UUID, uuid4

from yuwang.domain.models import utcnow

from .models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentInput,
    KnowledgeDocumentUpdate,
    KnowledgeHit,
    KnowledgeOrigin,
)

CHUNK_CHARS = 1_600
CHUNK_OVERLAP = 160
MAX_RETRIEVAL_CHARS = 6_400
_ASCII_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{1,63}")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_COMMON_TERMS = {
    "the", "and", "for", "with", "from", "this", "that", "are", "was",
    "请", "进行", "分析", "一个", "以及", "什么", "如何", "结果",
}


class KnowledgeRepository(Protocol):
    def save_knowledge_document(
        self, document: KnowledgeDocument, chunks: list[KnowledgeChunk]
    ) -> KnowledgeDocument: ...
    def get_knowledge_document(self, document_id: UUID | str) -> KnowledgeDocument | None: ...
    def list_knowledge_documents(self) -> list[KnowledgeDocument]: ...
    def list_knowledge_chunks(self, document_id: UUID | str | None = None) -> list[KnowledgeChunk]: ...
    def update_knowledge_document(self, document: KnowledgeDocument) -> KnowledgeDocument: ...
    def delete_knowledge_document(self, document_id: UUID | str) -> None: ...


def search_terms(value: str) -> list[str]:
    """中英文混合分词：ASCII 保留安全标识符，中文生成单字和双字词。"""

    terms = {match.group(0).casefold() for match in _ASCII_TOKEN.finditer(value)}
    for run in _CJK_RUN.findall(value):
        terms.update(character for character in run if character.strip())
        terms.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return sorted(term for term in terms if term not in _COMMON_TERMS)[:1_000]


def chunk_text(value: str) -> list[str]:
    """按段落切分，超长段落使用有界重叠，结果可稳定复现。"""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", normalized) if item.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        if len(paragraph) > CHUNK_CHARS:
            flush()
            start = 0
            while start < len(paragraph):
                end = min(len(paragraph), start + CHUNK_CHARS)
                chunks.append(paragraph[start:end].strip())
                if end >= len(paragraph):
                    break
                start = end - CHUNK_OVERLAP
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) > CHUNK_CHARS:
            flush()
            current = paragraph
        else:
            current = candidate
    flush()
    return chunks or [normalized[:CHUNK_CHARS]]


class KnowledgeBaseService:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    def import_document(
        self,
        value: KnowledgeDocumentInput,
        *,
        origin: KnowledgeOrigin = "user",
        document_id: UUID | None = None,
    ) -> KnowledgeDocument:
        content = value.content.strip()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = self.repository.get_knowledge_document(document_id) if document_id else None
        pieces = chunk_text(content)
        document = KnowledgeDocument(
            id=document_id or uuid4(),
            title=value.title,
            source_uri=value.source_uri,
            tags=value.tags,
            scenarios=value.scenarios,
            enabled=existing.enabled if existing else value.enabled,
            allow_provider_context=(
                existing.allow_provider_context if existing else value.allow_provider_context
            ),
            origin=origin,
            sha256=digest,
            size_chars=len(content),
            chunk_count=len(pieces),
            created_at=existing.created_at if existing else utcnow(),
            updated_at=utcnow(),
        )
        chunks = [
            KnowledgeChunk(
                document_id=document.id,
                ordinal=index,
                content=piece,
                sha256=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                search_terms=search_terms(f"{value.title} {' '.join(value.tags)} {piece}"),
            )
            for index, piece in enumerate(pieces, 1)
        ]
        return self.repository.save_knowledge_document(document, chunks)

    def list_documents(self) -> list[KnowledgeDocument]:
        return self.repository.list_knowledge_documents()

    def update_document(
        self, document_id: UUID, value: KnowledgeDocumentUpdate
    ) -> KnowledgeDocument:
        document = self.repository.get_knowledge_document(document_id)
        if not document:
            raise KeyError("知识文档不存在")
        updates = value.model_dump(exclude_unset=True)
        for key, item in updates.items():
            setattr(document, key, item)
        document.updated_at = utcnow()
        if {"title", "tags"}.intersection(updates):
            chunks = self.repository.list_knowledge_chunks(document.id)
            for chunk in chunks:
                chunk.search_terms = search_terms(
                    f"{document.title} {' '.join(document.tags)} {chunk.content}"
                )
            return self.repository.save_knowledge_document(document, chunks)
        return self.repository.update_knowledge_document(document)

    def delete_document(self, document_id: UUID) -> None:
        document = self.repository.get_knowledge_document(document_id)
        if not document:
            raise KeyError("知识文档不存在")
        if document.origin == "builtin":
            raise ValueError("内置知识文档只能停用，不能删除")
        self.repository.delete_knowledge_document(document_id)

    def search(
        self,
        query: str,
        *,
        scenario: str = "general",
        limit: int = 4,
        require_provider_context: bool = True,
    ) -> list[KnowledgeHit]:
        documents = {
            item.id: item
            for item in self.repository.list_knowledge_documents()
            if item.enabled
            and (item.allow_provider_context or not require_provider_context)
            and (
                scenario == "general"
                or not item.scenarios
                or scenario in item.scenarios
                or "general" in item.scenarios
            )
        }
        if not documents:
            return []
        chunks = [
            item
            for item in self.repository.list_knowledge_chunks()
            if item.document_id in documents
        ]
        query_terms = set(search_terms(query)[:128])
        if not query_terms or not chunks:
            return []
        frequencies = Counter(
            term for chunk in chunks for term in query_terms.intersection(chunk.search_terms)
        )
        scored: list[tuple[float, KnowledgeChunk]] = []
        normalized_query = " ".join(query.casefold().split())[:200]
        for chunk in chunks:
            overlap = query_terms.intersection(chunk.search_terms)
            if not overlap:
                continue
            document = documents[chunk.document_id]
            score = sum(
                math.log((len(chunks) + 1) / (frequencies[term] + 1)) + 1
                for term in overlap
            )
            title_terms = set(search_terms(f"{document.title} {' '.join(document.tags)}"))
            score += 0.75 * len(overlap.intersection(title_terms))
            if len(normalized_query) >= 4 and normalized_query in chunk.content.casefold():
                score += 3.0
            scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].ordinal, str(item[1].id)))
        hits: list[KnowledgeHit] = []
        used_chars = 0
        for score, chunk in scored:
            if len(hits) >= min(limit, 8):
                break
            if hits and used_chars + len(chunk.content) > MAX_RETRIEVAL_CHARS:
                continue
            document = documents[chunk.document_id]
            hits.append(
                KnowledgeHit(
                    document_id=document.id,
                    chunk_id=chunk.id,
                    title=document.title,
                    source_uri=document.source_uri,
                    chunk_ordinal=chunk.ordinal,
                    content=chunk.content,
                    content_sha256=chunk.sha256,
                    score=round(score, 6),
                )
            )
            used_chars += len(chunk.content)
        return hits
