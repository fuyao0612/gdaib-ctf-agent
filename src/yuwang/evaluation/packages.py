"""教学友好的本地任务包读取器，不加载或执行包内代码。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field


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
    manifest = TaskPackageManifest.model_validate(yaml.safe_load(manifest_path.read_text(encoding="utf-8")))
    inputs = (root / "inputs").resolve()
    for name in manifest.input_artifacts:
        path = (inputs / name).resolve()
        if inputs not in path.parents or not path.is_file():
            raise ValueError("任务包输入必须存在于 inputs 目录")
    verifier = (root / "verifier" / manifest.judge).resolve()
    if (root / "verifier") not in verifier.parents or not verifier.is_file():
        raise ValueError("任务包 Judge 私有配置不存在")
    return manifest


__all__ = ["TaskPackageManifest", "load_task_package"]
