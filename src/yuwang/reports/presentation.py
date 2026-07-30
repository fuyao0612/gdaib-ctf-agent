"""把已持久化的工具输出转换为可展示的确定性观察。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yuwang.domain.models import VerificationRule
from yuwang.flag_candidates import is_flag_candidate
from yuwang.policy import redact, redact_data


@dataclass(frozen=True)
class ToolObservationPresentation:
    summary: str
    facts: list[str]
    status_details: dict[str, Any]
    reproduction_hint: str | None = None


def _values(value: Any, limit: int = 3) -> list[str]:
    if not isinstance(value, list):
        return []
    return [redact(str(item)) for item in value[:limit] if str(item).strip()]


def present_tool_observation(
    tool_id: str,
    *,
    success: bool,
    output: dict[str, Any],
    error: str | None = None,
    artifact_count: int = 0,
    arguments: dict[str, Any] | None = None,
    verification_rules: list[VerificationRule] | None = None,
) -> ToolObservationPresentation:
    """从工具事实提取公开摘要，避免把“执行成功”当作观察结果。"""

    safe_output = redact_data(output)
    assert isinstance(safe_output, dict)
    if not success:
        detail = redact(error or str(safe_output.get("message") or "工具未返回结果"))
        return ToolObservationPresentation(
            summary=f"工具未成功完成：{detail}",
            facts=[detail],
            status_details={"success": False, "artifact_count": artifact_count},
        )

    if tool_id == "builtin.localhost_http_probe":
        status = safe_output.get("status_code", "未知")
        content_type = safe_output.get("content_type", "未知类型")
        robots = _values(safe_output.get("robots_paths"))
        links = _values(safe_output.get("explicit_links"))
        facts = [f"HTTP {status}", f"内容类型：{content_type}"]
        if robots:
            facts.append(f"robots.txt 暴露路径：{', '.join(robots)}")
        if links:
            facts.append(f"页面显式链接：{', '.join(links)}")
        excerpt = str(safe_output.get("body_excerpt", ""))
        if "flag_b64" in excerpt:
            facts.append("响应正文包含 flag_b64 字段")
        parameters = arguments or {}
        url = str(parameters.get("url", ""))
        if url:
            from urllib.parse import parse_qsl, urlsplit

            parsed = urlsplit(url)
            facts.append(f"请求路径：{parsed.path or '/'}")
            query = parse_qsl(parsed.query, keep_blank_values=True)
            if query:
                facts.append("查询参数：" + "、".join(key for key, _ in query))
        header = parameters.get("ctf_header")
        if isinstance(header, dict) and header.get("name"):
            facts.append(f"CTF 请求头：{header['name']}")
        return ToolObservationPresentation(
            summary="；".join(facts), facts=facts,
            status_details={"status_code": status, "content_type": content_type, "artifact_count": artifact_count},
            reproduction_hint="请求相同的已授权本机 URL，并检查响应中的公开路径和字段。",
        )

    if tool_id == "ctf.encoding_decode":
        candidates = safe_output.get("candidates")
        values = candidates if isinstance(candidates, list) else []
        chains = [
            " -> ".join(str(item) for item in value.get("decode_chain", []))
            for value in values[:3] if isinstance(value, dict)
        ]
        flags = [
            str(value.get("value")) for value in values[:3]
            if isinstance(value, dict) and is_flag_candidate(value.get("value"), verification_rules or ())
        ]
        facts = [f"解码得到 {len(values)} 个候选"]
        if chains:
            facts.append(f"编码链：{', '.join(chains)}")
        if flags:
            facts.append(f"发现 {len(flags)} 个高置信 Flag 候选")
        return ToolObservationPresentation("；".join(facts), facts, {"candidate_count": len(values), "artifact_count": artifact_count})

    if tool_id == "ctf.flag_candidate_verify":
        candidate = redact(str(safe_output.get("candidate", "候选值未返回")))
        status = str(safe_output.get("validation_status", "unknown"))
        summary = f"候选 Flag {candidate}；格式校验状态：{status}；尚未经过赛题平台验证"
        return ToolObservationPresentation(summary, [summary], {"validation_status": status, "artifact_count": artifact_count})

    if tool_id == "ctf.strings_extract":
        values = _values(safe_output.get("strings") or safe_output.get("items"))
        count = safe_output.get("count", len(values))
        facts = [f"提取到 {count} 项可打印字符串"]
        if values:
            facts.append(f"相关片段：{', '.join(values)}")
        return ToolObservationPresentation("；".join(facts), facts, {"count": count, "artifact_count": artifact_count})

    if tool_id == "builtin.file_metadata":
        size = safe_output.get("size", "未知")
        mime_type = safe_output.get("mime_type", "未知类型")
        digest = str(safe_output.get("sha256", ""))[:12]
        facts = [f"文件大小：{size} B", f"类型：{mime_type}"]
        if digest:
            facts.append(f"SHA-256 摘要：{digest}")
        return ToolObservationPresentation("；".join(facts), facts, {"size": size, "mime_type": mime_type})

    raw_summary = str(safe_output.get("summary") or safe_output.get("message") or "已获得结构化工具结果")
    return ToolObservationPresentation(
        summary=redact(raw_summary)[:1000],
        facts=[redact(raw_summary)[:1000]],
        status_details={"success": True, "artifact_count": artifact_count},
    )
