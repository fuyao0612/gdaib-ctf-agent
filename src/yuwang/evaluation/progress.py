"""评测 CLI 的可恢复进度文件。"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yuwang.domain.models import utcnow

from .cases import EvaluationCase
from .results import EvaluationStatus
from .runner import EvaluationResult


class EvaluationProgressEntry(BaseModel):
    """一次已经持久化的评测尝试，不保存模型输出或任何敏感值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    attempt: int = Field(ge=1)
    status: EvaluationStatus
    record_id: UUID
    completed_at: datetime


class EvaluationProgress(BaseModel):
    """用于恢复同一批评测的最小、可校验状态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(default=1, frozen=True)
    provider_id: UUID
    case_ids: tuple[str, ...]
    attempts: int = Field(ge=1)
    completed: tuple[EvaluationProgressEntry, ...] = ()
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def validate_entries(self) -> EvaluationProgress:
        if self.version != 1:
            raise ValueError("不支持的评测进度文件版本")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("评测进度文件包含重复用例")
        keys = {(entry.case_id, entry.attempt) for entry in self.completed}
        if len(keys) != len(self.completed):
            raise ValueError("评测进度文件包含重复尝试")
        if any(entry.case_id not in self.case_ids for entry in self.completed):
            raise ValueError("评测进度文件包含未选中的用例")
        if any(entry.attempt > self.attempts for entry in self.completed):
            raise ValueError("评测进度文件包含超出范围的尝试次数")
        return self

    @classmethod
    def create(
        cls,
        *,
        provider_id: UUID,
        cases: Iterable[EvaluationCase],
        attempts: int,
    ) -> EvaluationProgress:
        return cls(
            provider_id=provider_id,
            case_ids=tuple(case.case_id for case in cases),
            attempts=attempts,
        )

    @property
    def completed_keys(self) -> set[tuple[str, int]]:
        return {(entry.case_id, entry.attempt) for entry in self.completed}

    def ensure_compatible(
        self,
        *,
        provider_id: UUID,
        cases: Iterable[EvaluationCase],
        attempts: int,
    ) -> None:
        case_ids = tuple(case.case_id for case in cases)
        if self.provider_id != provider_id:
            raise ValueError("恢复文件的 Provider 与本次执行不一致")
        if self.case_ids != case_ids:
            raise ValueError("恢复文件的评测用例与本次执行不一致")
        if self.attempts != attempts:
            raise ValueError("恢复文件的尝试次数与本次执行不一致")

    def add_result(
        self,
        *,
        case: EvaluationCase,
        attempt: int,
        result: EvaluationResult,
    ) -> EvaluationProgress:
        if result.record_id is None:
            raise ValueError("评测结果尚未持久化，不能写入恢复进度")
        key = (case.case_id, attempt)
        if key in self.completed_keys:
            raise ValueError("评测尝试已经存在于恢复进度中")
        entry = EvaluationProgressEntry(
            case_id=case.case_id,
            attempt=attempt,
            status=result.status,
            record_id=result.record_id,
            completed_at=utcnow(),
        )
        return self.model_copy(
            update={"completed": (*self.completed, entry), "updated_at": utcnow()}
        )


class EvaluationProgressStore:
    """以原子替换方式写入进度，避免中断产生半截 JSON。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> EvaluationProgress:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"恢复文件不存在：{self.path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取评测恢复文件：{self.path}") from exc
        try:
            return EvaluationProgress.model_validate(payload)
        except ValueError as exc:
            raise ValueError("评测恢复文件格式无效") from exc

    def save(self, progress: EvaluationProgress) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            progress.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
        )
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)


__all__ = ["EvaluationProgress", "EvaluationProgressEntry", "EvaluationProgressStore"]
