"""首批低风险 CTF 工具的公共 Plugin 基类和稳定 ToolSpec 构造。"""

from __future__ import annotations

from abc import abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec
from yuwang.tooling.plugin import ToolPlugin

from .artifacts import ArtifactAccess

I = TypeVar("I", bound=BaseModel)
O = TypeVar("O", bound=BaseModel)


class CtfArtifactTool(ToolPlugin[I, O]):
    """所有 CTF 文件工具都共享同一 Artifact 访问边界。"""

    def __init__(self, artifacts: ArtifactAccess) -> None:
        self.artifacts = artifacts

    async def execute(self, value: I) -> O:
        return await self.execute_with_request(value, None)

    @abstractmethod
    async def execute_with_request(self, value: I, request: ToolCallRequest | None) -> O: ...


def ctf_spec(
    *,
    name: str,
    display_name: str,
    description: str,
    capabilities: list[str],
    scenarios: list[str],
    permissions: list[str],
    timeout_seconds: float,
    error_codes: list[str],
    input_schema: dict[str, object],
    output_schema: dict[str, object],
    artifact_types: list[str] | None = None,
) -> ToolSpec:
    return ToolSpec(
        namespace="ctf",
        name=name,
        display_name=display_name,
        version="1.0.0",
        author="御网智元",
        source="builtin",
        source_type="builtin",
        description=description,
        capabilities=capabilities,
        scenarios=scenarios,
        risk="low",
        permissions=permissions,
        requires_network=False,
        allowed_target_types=["artifact"],
        timeout_seconds=timeout_seconds,
        error_codes=error_codes,
        idempotent=True,
        artifact_types=artifact_types or [],
        input_schema=input_schema,
        output_schema=output_schema,
        supports_cancellation=True,
    )
