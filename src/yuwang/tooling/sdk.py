from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import mimetypes
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Generic, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

I = TypeVar("I", bound=BaseModel)
O = TypeVar("O", bound=BaseModel)


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    description: str
    capabilities: list[str]
    scenarios: list[str]
    risk: str
    permissions: list[str]
    requires_network: bool
    allowed_target_types: list[str]
    timeout_seconds: float = Field(gt=0, le=120)
    error_codes: list[str]
    idempotent: bool
    artifact_types: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ToolResult(BaseModel):
    success: bool
    summary: str
    output: dict[str, Any] = Field(default_factory=dict)
    error: ToolError | None = None
    duration_ms: int = 0
    artifact_ids: list[str] = Field(default_factory=list)


class ToolPlugin(ABC, Generic[I, O]):
    input_model: type[I]
    output_model: type[O]

    @property
    @abstractmethod
    def spec(self) -> ToolSpec: ...

    async def startup(self) -> None:  # lifecycle hook
        return None

    async def shutdown(self) -> None:
        return None

    @abstractmethod
    async def execute(self, value: I) -> O: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolPlugin[Any, Any]] = {}

    def register(self, tool: ToolPlugin[Any, Any]) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"duplicate tool: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> ToolPlugin[Any, Any]:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError("tool is not registered") from exc

    def names(self) -> set[str]:
        return set(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def discover(self, group: str = "yuwang.tools") -> int:
        """Load explicitly packaged plugins from one configured entry-point group."""
        discovered = importlib.metadata.entry_points().select(group=group)
        for entry_point in discovered:
            factory = entry_point.load()
            tool = factory() if callable(factory) else factory
            if not isinstance(tool, ToolPlugin):
                raise TypeError(f"plugin {entry_point.name} is not a ToolPlugin")
            self.register(tool)
        return len(discovered)


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(self, name: str, raw_input: dict[str, Any], timeout: float | None = None) -> ToolResult:
        started = time.perf_counter()
        try:
            tool = self.registry.get(name)
            value = tool.input_model.model_validate(raw_input)
            output = await asyncio.wait_for(tool.execute(value), timeout=timeout or tool.spec.timeout_seconds)
            return ToolResult(success=True, summary=f"{name} 执行成功", output=output.model_dump(mode="json"), duration_ms=int((time.perf_counter() - started) * 1000))
        except ValidationError as exc:
            return ToolResult(success=False, summary=f"{name} 输入无效", error=ToolError(code="invalid_input", message=str(exc)), duration_ms=int((time.perf_counter() - started) * 1000))
        except TimeoutError:
            return ToolResult(success=False, summary=f"{name} 执行超时", error=ToolError(code="timeout", message="tool timeout", retryable=True), duration_ms=int((time.perf_counter() - started) * 1000))
        except Exception as exc:  # plugin boundary deliberately isolates failures
            return ToolResult(success=False, summary=f"{name} 执行失败", error=ToolError(code="execution_error", message=str(exc), retryable=True), duration_ms=int((time.perf_counter() - started) * 1000))


class EchoInput(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    fail: bool = False


class EchoOutput(BaseModel):
    echoed: str


class MockEchoTool(ToolPlugin[EchoInput, EchoOutput]):
    input_model = EchoInput
    output_model = EchoOutput

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="mock_echo", version="1.0.0", description="确定性回显，用于安全闭环演示", capabilities=["demo", "text"], scenarios=["safe_demo"], risk="low", permissions=[], requires_network=False, allowed_target_types=[], timeout_seconds=3, error_codes=["simulated_failure"], idempotent=True, artifact_types=[], input_schema=self.input_model.model_json_schema(), output_schema=self.output_model.model_json_schema())

    async def execute(self, value: EchoInput) -> EchoOutput:
        if value.fail:
            raise RuntimeError("simulated first-attempt failure")
        return EchoOutput(echoed=value.text)


class FileMetadataInput(BaseModel):
    path: str


class FileMetadataOutput(BaseModel):
    sha256: str
    size: int
    mime_type: str


class FileMetadataTool(ToolPlugin[FileMetadataInput, FileMetadataOutput]):
    input_model = FileMetadataInput
    output_model = FileMetadataOutput

    def __init__(self, artifact_root: Path) -> None:
        self.root = artifact_root.resolve()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="file_metadata", version="1.0.0", description="计算受控附件的哈希、大小与 MIME，不解析内容", capabilities=["file", "metadata"], scenarios=["safe_demo", "forensics"], risk="low", permissions=["artifact:read"], requires_network=False, allowed_target_types=["artifact"], timeout_seconds=5, error_codes=["path_denied", "not_found"], idempotent=True, artifact_types=[], input_schema=self.input_model.model_json_schema(), output_schema=self.output_model.model_json_schema())

    async def execute(self, value: FileMetadataInput) -> FileMetadataOutput:
        candidate = (self.root / value.path).resolve()
        if self.root not in candidate.parents or not candidate.is_file():
            raise ValueError("path denied or missing")
        data = await asyncio.to_thread(candidate.read_bytes)
        return FileMetadataOutput(sha256=hashlib.sha256(data).hexdigest(), size=len(data), mime_type=mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")


class ProbeInput(BaseModel):
    url: str


class ProbeOutput(BaseModel):
    status_code: int
    content_type: str


class LocalhostHTTPProbeTool(ToolPlugin[ProbeInput, ProbeOutput]):
    input_model = ProbeInput
    output_model = ProbeOutput

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="localhost_http_probe", version="1.0.0", description="仅探测经策略批准的本地 HTTP 服务", capabilities=["http", "metadata"], scenarios=["safe_demo"], risk="medium", permissions=["network:localhost"], requires_network=True, allowed_target_types=["localhost"], timeout_seconds=5, error_codes=["request_failed"], idempotent=True, artifact_types=[], input_schema=self.input_model.model_json_schema(), output_schema=self.output_model.model_json_schema())

    async def execute(self, value: ProbeInput) -> ProbeOutput:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.get(value.url)
        return ProbeOutput(status_code=response.status_code, content_type=response.headers.get("content-type", ""))


def create_reference_registry(artifact_root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(MockEchoTool())
    registry.register(FileMetadataTool(artifact_root))
    registry.register(LocalhostHTTPProbeTool())
    return registry
