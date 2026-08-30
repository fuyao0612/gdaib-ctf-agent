"""教学友好的本地任务包读取器，不加载或执行包内代码。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from yuwang.domain.models import Budget

from .cases import EvaluationCase
from .scorer import EvaluationCriterion

MAX_TASK_PACKAGE_ARTIFACT_BYTES = 2 * 1024 * 1024


class TaskPackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9-]+$")
    version: str
    scenario: str
    title: str
    objective: str
    authorization_scope: list[str]
    input_artifacts: list[str]
    allowed_tools: list[str]
    budget: dict[str, int | float]
    timeout: int | float
    max_attempts: int = Field(ge=1)
    expected_result_schema: dict[str, Any]
    criteria: list[dict[str, Any]]
    judge: str
    tags: list[str]
    difficulty: str


def load_task_package(case_root: Path) -> TaskPackageManifest:
    """读取清单并确认输入只位于 inputs/；不读取 Judge 私有配置。"""

    root = case_root.resolve()
    manifest_path = root / "manifest.yaml"
    if not manifest_path.is_file():
        raise ValueError("任务包缺少 manifest.yaml")
    manifest = TaskPackageManifest.model_validate(
        yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    )
    inputs = (root / "inputs").resolve()
    for name in manifest.input_artifacts:
        path = (inputs / name).resolve()
        if inputs not in path.parents or not path.is_file():
            raise ValueError("任务包输入必须存在于 inputs 目录")
    verifier = (root / "verifier" / manifest.judge).resolve()
    if (root / "verifier") not in verifier.parents or not verifier.is_file():
        raise ValueError("任务包 Judge 私有配置不存在")
    return manifest


def load_task_package_case(case_root: Path) -> EvaluationCase:
    """把安全任务包转换为正式 EvaluationCase，并保留 Judge 配置在评测侧。"""

    root = case_root.resolve()
    manifest = load_task_package(root)
    input_files = tuple(
        _read_input_file(root / "inputs" / name, name) for name in manifest.input_artifacts
    )
    judge_path = root / "verifier" / manifest.judge
    judge = yaml.safe_load(judge_path.read_text(encoding="utf-8")) or {}
    criteria: list[EvaluationCriterion] = []
    assertions: list[str] = []
    for index, raw in enumerate(manifest.criteria, start=1):
        validator_type = str(raw.get("validator_type", ""))
        criterion_id = str(raw.get("criterion_id", f"{manifest.case_id}-criterion-{index}"))
        assertions.append(criterion_id)
        private_config = (
            {**judge, "result_type": raw.get("result_type")}
            if validator_type == "local_judge"
            else {}
        )
        criteria.append(
            EvaluationCriterion(
                criterion_id=criterion_id,
                description=str(raw.get("description", validator_type)),
                validator_type=validator_type,
                expected_value=raw.get("expected_value"),
                required=bool(raw.get("required", True)),
                private_config=private_config,
            )
        )
    return EvaluationCase(
        case_id=manifest.case_id,
        name=manifest.title,
        version=manifest.version,
        category=manifest.scenario,
        difficulty=manifest.difficulty,
        objective=manifest.objective,
        allowed_tools=tuple(manifest.allowed_tools),
        authorized_targets=tuple(manifest.authorization_scope),
        max_attempts=manifest.max_attempts,
        budget=Budget.model_validate(manifest.budget),
        timeout_seconds=float(manifest.timeout),
        user_messages=(manifest.objective,),
        expected_outcome="task",
        criteria=tuple(criteria),
        assertions=tuple(assertions or [manifest.case_id]),
        tags=tuple(manifest.tags),
        input_artifact_files=input_files,
    )


def _read_input_file(path: Path, name: str) -> tuple[str, bytes]:
    size = path.stat().st_size
    if size > MAX_TASK_PACKAGE_ARTIFACT_BYTES:
        raise ValueError(f"任务包输入 {name} 超过 {MAX_TASK_PACKAGE_ARTIFACT_BYTES} 字节限制")
    return name, path.read_bytes()


__all__ = [
    "MAX_TASK_PACKAGE_ARTIFACT_BYTES",
    "TaskPackageManifest",
    "load_task_package",
    "load_task_package_case",
]
