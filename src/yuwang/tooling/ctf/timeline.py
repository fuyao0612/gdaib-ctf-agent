"""应急响应日志时间线与事件归纳工具。"""

from __future__ import annotations

import re
from collections import Counter
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yuwang.policy import redact
from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec

MAX_TIMELINE_READ_BYTES = 4 * 1024 * 1024
_ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
_SYSLOG_TS = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b")
_SEVERITY = re.compile(r"\b(critical|fatal|error|err|warning|warn|notice|info|debug)\b", re.I)


class TimelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    max_events: int = Field(default=200, ge=1, le=500)


class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_number: int = Field(ge=1)
    timestamp: str = Field(min_length=1, max_length=64)
    severity: Literal["critical", "error", "warning", "notice", "info", "debug"]
    category: Literal["authentication", "network", "process", "file", "configuration", "other"]
    excerpt: str = Field(min_length=1, max_length=500)


class SeverityCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    critical: int = 0
    error: int = 0
    warning: int = 0
    notice: int = 0
    info: int = 0
    debug: int = 0


class CategoryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authentication: int = 0
    network: int = 0
    process: int = 0
    file: int = 0
    configuration: int = 0
    other: int = 0


class TimelineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    event_count: int = Field(ge=0)
    earliest_timestamp: str | None = None
    latest_timestamp: str | None = None
    severity_counts: SeverityCounts
    category_counts: CategoryCounts
    events: list[TimelineEvent] = Field(default_factory=list, max_length=500)
    input_truncated: bool = False


class TimelineAnalyzeTool(CtfArtifactTool[TimelineInput, TimelineOutput]):
    input_model = TimelineInput
    output_model = TimelineOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="incident_timeline_analyze",
            display_name="应急响应时间线分析",
            description=(
                "从日志 Artifact 提取带时间戳的事件、严重级别和安全类别，生成可审计时间线；"
                "只读、脱敏，不替代人工取证结论"
            ),
            capabilities=["forensics", "incident_response", "timeline", "log_analysis"],
            scenarios=["incident_response", "forensics", "ctf"],
            permissions=["artifact:read"],
            timeout_seconds=15,
            error_codes=["artifact_not_found", "file_too_large", "decode_error", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: TimelineInput, request: ToolCallRequest | None
    ) -> TimelineOutput:
        artifact, content = self.artifacts.read(
            value.artifact_id, request, max_bytes=MAX_TIMELINE_READ_BYTES
        )
        events: list[TimelineEvent] = []
        for line_number, raw_line in enumerate(content.decode("utf-8", errors="replace").splitlines(), 1):
            match = _ISO_TS.search(raw_line)
            if match is None:
                match = _SYSLOG_TS.search(raw_line)
            if match is None:
                continue
            timestamp = match.group(0)
            severity = _parse_severity(raw_line)
            category = _parse_category(raw_line)
            events.append(
                TimelineEvent(
                    line_number=line_number,
                    timestamp=timestamp,
                    severity=severity,
                    category=category,
                    excerpt=redact(" ".join(raw_line.split()))[:500] or "日志行为空",
                )
            )
            if len(events) >= value.max_events:
                break
        severity_counts: dict[str, int] = dict(Counter(event.severity for event in events))
        category_counts: dict[str, int] = dict(Counter(event.category for event in events))
        return TimelineOutput(
            artifact_id=artifact.id,
            event_count=len(events),
            earliest_timestamp=events[0].timestamp if events else None,
            latest_timestamp=events[-1].timestamp if events else None,
            severity_counts=SeverityCounts(**severity_counts),
            category_counts=CategoryCounts(**category_counts),
            events=events,
            input_truncated=len(content) >= MAX_TIMELINE_READ_BYTES,
        )


def _parse_severity(line: str) -> Literal["critical", "error", "warning", "notice", "info", "debug"]:
    match = _SEVERITY.search(line)
    value = match.group(1).casefold() if match is not None else "info"
    return {"fatal": "critical", "err": "error", "warn": "warning"}.get(value, value)  # type: ignore[return-value]


def _parse_category(line: str) -> Literal["authentication", "network", "process", "file", "configuration", "other"]:
    lowered = line.casefold()
    for category, keywords in (
        ("authentication", ("login", "auth", "password", "credential", "ssh")),
        ("network", ("http", "tcp", "udp", "dns", "connection", "socket", "ip=")),
        ("process", ("process", "exec", "command", "pid", "powershell", "cmd.exe")),
        ("file", ("file", "opened", "created", "deleted", "path=")),
        ("configuration", ("config", "policy", "permission", "sudo")),
    ):
        if any(keyword in lowered for keyword in keywords):
            return category  # type: ignore[return-value]
    return "other"
