"""工具调用前的确定性安全策略，默认拒绝未授权目标和高风险参数。"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from yuwang.domain.models import TaskSpec
from yuwang.tooling.sdk import ToolSpec


class SecurityConfig(BaseModel):
    max_upload_bytes: int = 5 * 1024 * 1024
    max_uploads_per_thread: int = 8
    allowed_http_hosts: set[str] = Field(
        default_factory=lambda: {"localhost", "127.0.0.1", "::1", "api"}
    )
    allowed_extensions: set[str] = Field(
        default_factory=lambda: {".txt", ".json", ".md", ".log", ".bin"}
    )


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
]


def redact(value: str) -> str:
    result = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            result = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result


class PolicyEngine:
    def __init__(self, config: SecurityConfig | None = None) -> None:
        self.config = config or SecurityConfig()

    def check_tool(
        self, task: TaskSpec, tool: ToolSpec, tool_input: dict[str, object]
    ) -> PolicyDecision:
        if tool.requires_network:
            raw_target = next(
                (str(tool_input[key]) for key in ("url", "target", "host") if key in tool_input),
                "",
            )
            parsed = urlparse(raw_target if "://" in raw_target else f"//{raw_target}")
            hostname = parsed.hostname
            if not hostname:
                return PolicyDecision(allowed=False, reason="网络工具缺少有效目标")
            if not task.authorized_targets:
                return PolicyDecision(allowed=False, reason="任务未声明网络授权目标")
            if (
                hostname not in task.authorized_targets
                and raw_target not in task.authorized_targets
            ):
                return PolicyDecision(allowed=False, reason="目标不在任务授权范围")
            if "localhost" in tool.allowed_target_types and not self.is_local_address(hostname):
                return PolicyDecision(allowed=False, reason="工具仅允许本地测试目标")
        return PolicyDecision(allowed=True, reason="工具与目标符合授权策略")

    def validate_upload(self, filename: str, size: int, existing_count: int) -> None:
        safe = Path(filename).name
        if safe != filename or safe in {"", ".", ".."}:
            raise ValueError("不安全的文件名")
        if size > self.config.max_upload_bytes:
            raise ValueError("文件超过大小限制")
        if existing_count >= self.config.max_uploads_per_thread:
            raise ValueError("附件数量超过限制")
        if Path(safe).suffix.lower() not in self.config.allowed_extensions:
            raise ValueError("文件类型不允许")

    def is_local_address(self, host: str) -> bool:
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host in self.config.allowed_http_hosts
