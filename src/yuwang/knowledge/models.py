"""本地 RAG 知识库的领域契约。

知识文档始终是不可信输入。即使管理员将文档标记为可用，它也只能作为
模型的参考资料，不能修改平台策略、授权边界或验证规则。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuwang.domain.models import utcnow

KnowledgeOrigin = Literal["builtin", "user"]


def _clean_list(values: list[str], *, limit: int) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if not item or item in cleaned:
            continue
        cleaned.append(item[:limit])
    return cleaned


class KnowledgeDocumentInput(BaseModel):
    """新建文档输入；不接受本机路径或远程抓取，避免隐式扩权。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=500_000)
    source_uri: str | None = Field(default=None, max_length=2_048)
    tags: list[str] = Field(default_factory=list, max_length=30)
    scenarios: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    # 勾选后文档片段可能被发送给已选 Provider，因此必须显式保存。
    allow_provider_context: bool = False

    @field_validator("title", "content")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("内容不能为空")
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values: list[str]) -> list[str]:
        return _clean_list(values, limit=80)

    @field_validator("scenarios")
    @classmethod
    def clean_scenarios(cls, values: list[str]) -> list[str]:
        return _clean_list(values, limit=80)


class KnowledgeDocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    source_uri: str | None = Field(default=None, max_length=2_048)
    tags: list[str] | None = Field(default=None, max_length=30)
    scenarios: list[str] | None = Field(default=None, max_length=20)
    enabled: bool | None = None
    allow_provider_context: bool | None = None

    @field_validator("title")
    @classmethod
    def strip_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("标题不能为空")
        return value

    @field_validator("tags")
    @classmethod
    def clean_optional_tags(cls, values: list[str] | None) -> list[str] | None:
        return _clean_list(values, limit=80) if values is not None else None

    @field_validator("scenarios")
    @classmethod
    def clean_optional_scenarios(cls, values: list[str] | None) -> list[str] | None:
        return _clean_list(values, limit=80) if values is not None else None


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200)
    source_uri: str | None = Field(default=None, max_length=2_048)
    tags: list[str] = Field(default_factory=list, max_length=30)
    scenarios: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    allow_provider_context: bool = False
    origin: KnowledgeOrigin = "user"
    sha256: str = Field(min_length=64, max_length=64)
    size_chars: int = Field(ge=1, le=500_000)
    chunk_count: int = Field(ge=1, le=2_000)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    ordinal: int = Field(ge=1, le=2_000)
    content: str = Field(min_length=1, max_length=2_400)
    sha256: str = Field(min_length=64, max_length=64)
    search_terms: list[str] = Field(default_factory=list, max_length=1_000)


class KnowledgeHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    chunk_id: UUID
    title: str
    source_uri: str | None = None
    chunk_ordinal: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=2_400)
    content_sha256: str = Field(min_length=64, max_length=64)
    score: float = Field(ge=0)


class KnowledgeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=20_000)
    scenario: str = Field(default="general", min_length=1, max_length=80)
    limit: int = Field(default=4, ge=1, le=8)
