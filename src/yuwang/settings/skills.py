"""声明式 Skills：只保存任务模板，不上传或执行任意代码。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuwang.domain.models import SkillSnapshot, Thread, utcnow


class SkillInput(BaseModel):
    """设置中心可编辑的最小声明式 Skill 契约。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    prompt: str = Field(min_length=1, max_length=10_000)
    steps: list[str] = Field(default_factory=list, max_length=30)
    checklist: list[str] = Field(default_factory=list, max_length=30)
    enabled: bool = True

    @field_validator("name", "description", "prompt")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if "```" in normalized:
            raise ValueError("Skill 不支持代码块或可执行脚本")
        return normalized

    @field_validator("steps", "checklist")
    @classmethod
    def normalize_items(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            normalized = " ".join(value.split()).strip()
            if not normalized:
                continue
            if "```" in normalized:
                raise ValueError("Skill 不支持代码块或可执行脚本")
            if normalized not in cleaned:
                cleaned.append(normalized[:1000])
        return cleaned


class SkillDefinition(SkillInput):
    """持久化后的 Skill；版本通过 Run 快照保证历史运行可复现。"""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def snapshot(self) -> SkillSnapshot:
        return SkillSnapshot(
            skill_id=self.id,
            name=self.name,
            description=self.description,
            prompt=self.prompt,
            steps=self.steps,
            checklist=self.checklist,
        )


class SkillRepository(Protocol):
    def save_skill(self, value: SkillDefinition) -> SkillDefinition: ...
    def get_skill(self, skill_id: UUID) -> SkillDefinition | None: ...
    def list_skills(self) -> list[SkillDefinition]: ...
    def delete_skill_with_thread_cleanup(self, skill_id: UUID) -> int: ...
    def list_threads(self) -> list[Thread]: ...


class SkillService:
    """Skills 的 CRUD 与 Run 快照解析，始终拒绝停用或未知的会话选择。"""

    def __init__(self, repository: SkillRepository) -> None:
        self.repository = repository

    def list_skills(self, *, enabled_only: bool = False) -> list[SkillDefinition]:
        values = self.repository.list_skills()
        return [value for value in values if value.enabled] if enabled_only else values

    def get(self, skill_id: UUID) -> SkillDefinition:
        value = self.repository.get_skill(skill_id)
        if not value:
            raise KeyError("Skill 不存在")
        return value

    def create(self, value: SkillInput) -> SkillDefinition:
        skill = SkillDefinition(**value.model_dump())
        return self.repository.save_skill(skill)

    def update(self, skill_id: UUID, value: SkillInput) -> SkillDefinition:
        current = self.get(skill_id)
        updated = SkillDefinition(
            **value.model_dump(),
            id=current.id,
            created_at=current.created_at,
        )
        return self.repository.save_skill(updated)

    def delete(self, skill_id: UUID) -> int:
        self.get(skill_id)
        return self.repository.delete_skill_with_thread_cleanup(skill_id)

    def snapshots_for(self, skill_ids: list[UUID]) -> list[SkillSnapshot]:
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("Skill 选择不能重复")
        snapshots: list[SkillSnapshot] = []
        for skill_id in skill_ids:
            skill = self.get(skill_id)
            if not skill.enabled:
                raise ValueError(f"Skill“{skill.name}”已停用，请重新选择")
            snapshots.append(skill.snapshot())
        return snapshots
