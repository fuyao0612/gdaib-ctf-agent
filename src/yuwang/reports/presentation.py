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


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


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

    if tool_id == "ctf.artifact_content_search":
        query_label = redact(str(safe_output.get("query", "")))
        count = safe_output.get("match_count", 0)
        matches = safe_output.get("matches")
        lines = [
            str(item.get("line"))
            for item in matches[:3]
            if isinstance(matches, list) and isinstance(item, dict) and item.get("line")
        ] if isinstance(matches, list) else []
        facts = [f"关键词 {query_label or '未命名查询'} 命中 {count} 处"]
        if lines:
            facts.append(f"代表行：{', '.join(lines)}")
        return ToolObservationPresentation("；".join(facts), facts, {"match_count": count, "artifact_count": artifact_count})

    if tool_id == "ctf.web_evidence_analyze":
        title = redact(str(safe_output.get("title") or "未发现标题"))
        links = _values(safe_output.get("same_origin_links"))
        scripts = _values(safe_output.get("script_references"))
        fields = _values(safe_output.get("form_fields"))
        facts = [f"页面标题：{title}", f"同源链接 {_count(safe_output.get('same_origin_links'))} 个"]
        if links:
            facts.append(f"代表链接：{', '.join(links)}")
        if scripts:
            facts.append(f"脚本引用：{', '.join(scripts)}")
        if fields:
            facts.append(f"表单字段：{', '.join(fields)}")
        return ToolObservationPresentation("；".join(facts), facts, {"link_count": _count(safe_output.get("same_origin_links")), "artifact_count": artifact_count})

    if tool_id == "ctf.ioc_extract":
        indicators = safe_output.get("indicators")
        values = indicators if isinstance(indicators, list) else []
        kinds = sorted({str(item.get("kind")) for item in values if isinstance(item, dict) and item.get("kind")})
        facts = [f"提取到 {len(values)} 个 IOC"]
        if kinds:
            facts.append(f"类型：{', '.join(kinds[:8])}")
        return ToolObservationPresentation("；".join(facts), facts, {"indicator_count": len(values), "artifact_count": artifact_count})

    if tool_id == "ctf.incident_timeline_analyze":
        count = safe_output.get("event_count", _count(safe_output.get("events")))
        raw_categories = safe_output.get("category_counts")
        categories = [
            f"{name}={count}"
            for name, count in raw_categories.items()
            if isinstance(raw_categories, dict) and isinstance(count, int) and count > 0
        ][:5] if isinstance(raw_categories, dict) else []
        facts = [f"归纳出 {count} 条时间线事件"]
        if categories:
            facts.append(f"事件类别：{', '.join(categories)}")
        return ToolObservationPresentation("；".join(facts), facts, {"event_count": count, "artifact_count": artifact_count})

    if tool_id == "ctf.hash_analyze":
        matches = _values(safe_output.get("expected_matches"), 5)
        candidates = safe_output.get("embedded_hashes")
        facts = [f"计算并识别哈希候选 {_count(candidates)} 个"]
        if matches:
            facts.append(f"期望摘要匹配：{', '.join(matches)}")
        return ToolObservationPresentation("；".join(facts), facts, {"candidate_count": _count(candidates), "artifact_count": artifact_count})

    if tool_id == "ctf.jwt_analyze":
        candidates = safe_output.get("candidates")
        count = safe_output.get("candidate_count", _count(candidates))
        warnings = sum(
            _count(item.get("warnings"))
            for item in candidates
            if isinstance(candidates, list) and isinstance(item, dict)
        ) if isinstance(candidates, list) else 0
        facts = [f"识别到 {count} 个 JWT/JWS 候选", f"安全提示 {warnings} 条"]
        return ToolObservationPresentation("；".join(facts), facts, {"candidate_count": count, "warning_count": warnings, "artifact_count": artifact_count})

    if tool_id == "ctf.network_capture_analyze":
        packet_count = safe_output.get("packet_count", 0)
        analyzed = safe_output.get("analyzed_packets", 0)
        protocols = safe_output.get("protocols")
        names = [
            str(item.get("protocol"))
            for item in protocols[:5]
            if isinstance(protocols, list) and isinstance(item, dict) and item.get("protocol")
        ] if isinstance(protocols, list) else []
        facts = [f"流量包 {packet_count} 个，已分析 {analyzed} 个"]
        if names:
            facts.append(f"协议：{', '.join(names)}")
        dns_count = _count(safe_output.get("dns_queries"))
        http_count = _count(safe_output.get("http_requests"))
        if dns_count or http_count:
            facts.append(f"DNS 查询 {dns_count} 条，HTTP 请求 {http_count} 条")
        return ToolObservationPresentation("；".join(facts), facts, {"packet_count": packet_count, "analyzed_packets": analyzed, "artifact_count": artifact_count})

    raw_summary = str(safe_output.get("summary") or safe_output.get("message") or "已获得结构化工具结果")
    return ToolObservationPresentation(
        summary=redact(raw_summary)[:1000],
        facts=[redact(raw_summary)[:1000]],
        status_details={"success": True, "artifact_count": artifact_count},
    )
