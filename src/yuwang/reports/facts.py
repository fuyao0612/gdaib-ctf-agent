"""Build report facts from persisted execution records without invoking tools or models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from yuwang.domain.models import Event, Run, TaskSpec
from yuwang.flag_candidates import find_flag_candidates, is_flag_candidate
from yuwang.policy import redact, redact_data

_CTF_HEADER = re.compile(r"^X-CTF-[A-Za-z0-9-]+$")


def _items(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _observation_facts(step: dict[str, Any]) -> list[Any]:
    """兼容历史步骤中缺失或为 null 的 observation_facts。"""

    facts = step.get("observation_facts")
    return facts if isinstance(facts, list) and facts else [step.get("observation_summary")]


def _status(value: str, has_candidate: bool, has_answer: bool) -> str:
    return {
        "pending": "待验证",
        "unverified": (
            "已发现候选，尚未完成外部验证" if has_candidate
            else "结果未经外部验证" if has_answer
            else "尚未完成验证"
        ),
        "partial": "已通过格式或结构化校验，尚未进行外部平台验证",
        "validated": "已通过确定性外部验证",
        "failed": "验证失败",
    }.get(value, "验证状态未知")


def _decision(value: object) -> str | None:
    text = str(value or "").strip()
    while text.startswith("下一步："):
        text = text.removeprefix("下一步：").strip()
    while text.startswith("结束："):
        text = text.removeprefix("结束：").strip()
    return text or None


def public_arguments(value: object) -> dict[str, Any]:
    """Return tool parameters suitable for persisted public reports.

    The local CTF probe deliberately accepts a narrowly scoped `X-CTF-*` header
    discovered from the target.  Its value is part of the reproducible challenge
    procedure, unlike generic authentication headers, which must stay redacted.
    """

    arguments = value if isinstance(value, dict) else {}
    safe = redact_data(arguments)
    assert isinstance(safe, dict)
    header = arguments.get("ctf_header")
    if isinstance(header, dict):
        name = str(header.get("name", ""))
        raw_value = header.get("value")
        safe["ctf_header"] = {
            "name": name,
            "value": redact(str(raw_value)) if _CTF_HEADER.fullmatch(name) else "[REDACTED]",
        }
    return safe


@dataclass(frozen=True)
class ReportFacts:
    """A small, typed view of facts consumed by both report formats.

    The input is an already persisted trace plus the finalization snapshot.  Keeping this
    boundary explicit prevents a report from guessing facts from model prose or running tools.
    """

    report_kind: str
    report_kind_reason: str
    execution_status: str
    validation_status: str
    validation_label: str
    task_summary: str
    final_answer: str | None
    timeline: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    key_clues: list[dict[str, Any]]
    reproduction_steps: list[dict[str, Any]]
    failed_attempts: list[dict[str, Any]]
    policy_summary: list[dict[str, Any]]
    adjustments: list[str]
    metrics: dict[str, Any]
    handoff: dict[str, Any]

    def retrospective_input(self) -> dict[str, Any]:
        """向模型提供的脱敏事实摘要；工具输出始终是不可信数据。"""

        return {
            "task": self.task_summary,
            "validation_status": self.validation_status,
            "plan_adjustments": [
                {"ref": f"adjustment:{index}", "text": value}
                for index, value in enumerate(self.adjustments, 1)
            ],
            "timeline_untrusted": [
                {
                    "sequence": step.get("sequence"),
                    "action_ref": f"step:{step.get('sequence')}:action",
                    "goal": step.get("goal"),
                    "reason": step.get("action_reason"),
                    "action": step.get("action_summary"),
                    "observation_facts": [
                        {"ref": f"step:{step.get('sequence')}:observation:{index}", "text": fact}
                        for index, fact in enumerate(step.get("observation_facts", []), 1)
                    ],
                    "observation_summary": step.get("observation_summary"),
                    "status": step.get("observation_status"),
                }
                for step in self.timeline
            ],
            "failed_attempts": self.failed_attempts,
            "candidate_statuses": [
                {
                    "format_status": item.get("format_status"),
                    "deterministic_validation_status": item.get("deterministic_validation_status"),
                    "platform_validation_status": item.get("platform_validation_status"),
                }
                for item in self.candidates
            ],
        }

    @classmethod
    def build(
        cls, run: Run, task: TaskSpec, events: list[Event], metrics: dict[str, Any]
    ) -> ReportFacts:
        trace_value = metrics.get("trace")
        trace: dict[str, Any] = trace_value if isinstance(trace_value, dict) else {}
        timeline = [
            {**step, "arguments": public_arguments(step.get("arguments"))}
            for step in _items(trace.get("steps"))
        ]
        timeline = cls._normalize_timeline(timeline)
        evidence = _items(metrics.get("evidence_records") or trace.get("evidence"))
        tool_calls = [
            {**call, "arguments": public_arguments(call.get("arguments"))}
            for call in _items(trace.get("tool_calls"))
        ]
        final_answer = str(metrics.get("final_answer") or trace.get("final_answer") or "").strip() or None
        is_ctf = (
            str(task.scenario).casefold() == "ctf"
            or any(str(step.get("tool_id", "")).startswith("ctf.") for step in timeline)
            or any(item.get("rule_kind") == "flag_format" for item in evidence)
            or bool(find_flag_candidates(final_answer or "", task.verification_rules))
        )
        reason = (
            "任务场景明确为 CTF" if str(task.scenario).casefold() == "ctf"
            else "持久化执行记录包含 CTF 工具、Flag 证据或最终答案候选"
            if is_ctf else "未发现 CTF 专用持久化事实"
        )
        candidates = cls._candidates(
            evidence, timeline, tool_calls, final_answer, task.verification_rules
        )
        artifacts = cls._artifacts(_items(trace.get("artifacts")), timeline, tool_calls)
        clues = [
            {
                "step": step.get("sequence"),
                "call_id": step.get("call_id"),
                "summary": fact,
            }
            for step in timeline
            for fact in _observation_facts(step)
            if isinstance(fact, str) and fact.strip()
        ]
        reproduction = [cls._reproduction(index, step) for index, step in enumerate(timeline, 1)]
        policies: dict[str, int] = {}
        for event in events:
            if str(event.type) == "policy_checked":
                policies[event.summary] = policies.get(event.summary, 0) + 1
        completed_steps = [
            step.get("sequence")
            for step in timeline
            if step.get("observation_status") == "success"
        ]
        blockers: list[str] = []
        status = str(run.status)
        if status in {"waiting_input", "waiting_clarification"}:
            blockers.append("等待用户补充或澄清")
        if status == "waiting_approval":
            blockers.append("等待计划或风险审批")
        if status == "paused":
            blockers.append("任务已暂停，等待从检查点恢复")
        budget = {
            "steps": max(0, task.budget.max_steps - int(trace.get("metrics", {}).get("steps", 0)))
            if isinstance(trace.get("metrics"), dict) else task.budget.max_steps,
            "model_calls": max(0, task.budget.max_model_calls - int(trace.get("metrics", {}).get("logical_model_calls", 0)))
            if isinstance(trace.get("metrics"), dict) else task.budget.max_model_calls,
            "tool_calls": max(0, task.budget.max_tool_calls - int(trace.get("metrics", {}).get("tool_calls", 0)))
            if isinstance(trace.get("metrics"), dict) else task.budget.max_tool_calls,
        }
        handoff = {
            "current_goal": redact(task.body[:500]),
            "completed_steps": completed_steps,
            "validated_results": [
                item.get("candidate") for item in candidates
                if item.get("deterministic_validation_status") == "passed"
                or item.get("platform_validation_status") == "passed"
            ],
            "key_evidence": [item["summary"] for item in clues[:8] if item.get("summary")],
            "failed_paths": [
                step.get("sequence") for step in timeline
                if step.get("observation_status") in {"error", "timeout", "blocked", "stopped"}
            ],
            "current_blockers": blockers,
            "pending_approvals": [
                event.summary for event in events
                if str(event.type) in {"plan_approval_requested", "risk_approval_requested"}
            ],
            "remaining_budget": budget,
            "recommended_action": (
                "先处理当前阻塞后从检查点继续" if blockers
                else "复核报告和证据后决定是否进行下一步授权验证"
            ),
        }
        return cls(
            report_kind="ctf" if is_ctf else "general",
            report_kind_reason=reason,
            execution_status=str(run.status),
            validation_status=str(metrics.get("validation_status", run.validation_status)),
            validation_label=_status(
                str(metrics.get("validation_status", run.validation_status)),
                bool(candidates), bool(final_answer),
            ),
            task_summary=redact(task.body[:500]), final_answer=redact(final_answer) if final_answer else None,
            timeline=timeline, artifacts=artifacts, candidates=candidates, key_clues=clues,
            reproduction_steps=reproduction,
            failed_attempts=[step for step in timeline if step.get("observation_status") in {"error", "timeout", "blocked", "stopped"}],
            policy_summary=[{"summary": summary, "count": count} for summary, count in policies.items()],
            adjustments=[event.summary for event in events if str(event.type) == "replanned"],
            metrics=(
                trace["metrics"]
                if isinstance(trace.get("metrics"), dict)
                else {}
            ),
            handoff=handoff,
        )

    @staticmethod
    def _candidates(
        evidence: list[dict[str, Any]], timeline: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]], final_answer: str | None, rules: list[Any],
    ) -> list[dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        source_steps = {
            str(step.get("call_id")): step.get("sequence")
            for step in timeline if step.get("call_id")
        }

        def add(
            candidate: str, kind: str, call_id: object = None, step: object = None,
            *, location: str | None = None, format_status: str = "not_checked",
            verification_scope: str = "none", deterministic_status: str = "not_run",
            platform_status: str = "not_run", summary: str = "",
        ) -> None:
            candidate = candidate.strip()
            if not is_flag_candidate(candidate, rules):
                return
            source = {
                "kind": kind, "call_id": str(call_id) if call_id else None,
                "step": step, "location": location,
            }
            current = values.setdefault(candidate, {
                "candidate": candidate, "source_kind": kind,
                "source_call_id": str(call_id) if call_id else None,
                "source_step": step, "location": location,
                "discovery_source": kind, "format_status": format_status,
                "verification_scope": verification_scope,
                "deterministic_validation_status": deterministic_status,
                "platform_validation_status": platform_status,
                "platform_verified": platform_status == "passed",
                "verification_summary": summary or "工具输出发现候选值，尚未执行验证",
                "sources": [],
            })
            if not any(item.get("call_id") == source["call_id"] and source["call_id"] for item in current["sources"]):
                current["sources"].append(source)
            if current["format_status"] != "format_matched" and format_status == "format_matched":
                current["format_status"] = format_status
            if deterministic_status == "passed":
                current["deterministic_validation_status"] = "passed"
                current["verification_scope"] = "deterministic_rule"
            if platform_status == "passed":
                current["platform_validation_status"] = "passed"
                current["platform_verified"] = True
                current["verification_scope"] = "platform"
            if summary:
                current["verification_summary"] = summary

        for record in evidence:
            scope = str(record.get("verification_scope") or "")
            rule_kind = str(record.get("rule_kind") or "")
            # Legacy EvidenceRecord.verified means a deterministic rule in this project,
            # never a competition-platform receipt.
            deterministic = str(record.get("deterministic_validation_status") or (
                "passed" if record.get("verified") and scope != "platform" else "not_run"
            ))
            platform = str(record.get("platform_validation_status") or (
                "passed" if scope == "platform" and record.get("verified") else "not_run"
            ))
            add(
                str(record.get("candidate", "")), str(record.get("discovery_source") or "evidence"),
                record.get("source_call_id"), record.get("source_step") or source_steps.get(str(record.get("source_call_id"))),
                location=str(record.get("location") or "") or None,
                format_status=str(record.get("format_status") or ("format_matched" if rule_kind == "flag_format" else "not_checked")),
                verification_scope=scope or ("deterministic_rule" if deterministic == "passed" else "none"),
                deterministic_status=deterministic, platform_status=platform,
                summary=str(record.get("verification_summary", "")),
            )
        for step in timeline:
            for candidate in find_flag_candidates(
                " ".join(str(step.get(key, "")) for key in ("observation_summary", "preview")), rules
            ):
                add(
                    candidate,
                    "encoding_decode" if step.get("tool_id") == "ctf.encoding_decode" else "execution_step",
                    step.get("call_id"), step.get("sequence"),
                )
        for call in tool_calls:
            for candidate in find_flag_candidates(str(call.get("result_summary", "")), rules):
                add(candidate, "tool_call", call.get("id"), None)
        for candidate in find_flag_candidates(
            final_answer or "", rules, allow_whole_text_sha256=True
        ):
            add(candidate, "final_answer")
        return list(values.values())

    @staticmethod
    def _artifacts(
        artifacts: list[dict[str, Any]], timeline: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        sources: dict[str, tuple[object, object]] = {}
        for step in timeline:
            for artifact_id in step.get("artifact_ids", []):
                sources.setdefault(str(artifact_id), (step.get("sequence"), step.get("call_id")))
        for call in tool_calls:
            for artifact_id in call.get("artifact_ids", []):
                sources.setdefault(str(artifact_id), (None, call.get("id")))
        return [
            {
                **artifact,
                "source_step": sources.get(str(artifact.get("id")), (None, None))[0],
                "source_call_id": sources.get(str(artifact.get("id")), (None, None))[1],
            }
            for artifact in artifacts
        ]

    @staticmethod
    def _normalize_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prefer persisted tool parameters over contradictory model prose in reports."""

        normalized: list[dict[str, Any]] = []
        for step in timeline:
            current = dict(step)
            if current.get("tool_id") == "ctf.encoding_decode":
                arguments = current.get("arguments")
                encoded = str(arguments.get("text", "")) if isinstance(arguments, dict) else ""
                def matching_url(previous: dict[str, Any], target: str = encoded) -> str:
                    previous_arguments = previous.get("arguments")
                    if (
                        previous.get("tool_id") != "builtin.localhost_http_probe"
                        or not target
                        or not isinstance(previous_arguments, dict)
                        or target not in str(previous.get("preview", ""))
                    ):
                        return ""
                    return str(previous_arguments.get("url", ""))

                source_step = next(
                    (previous for previous in reversed(normalized) if matching_url(previous)),
                    None,
                )
                source_url = matching_url(source_step) if source_step else ""
                if source_step is not None and source_url:
                    path = urlsplit(source_url).path or source_url
                    encoding = str(arguments.get("encoding", "auto")) if isinstance(arguments, dict) else "auto"
                    summary = f"使用 {encoding} 解码来自 {path} 响应的已记录值"
                    source_step["decision"] = f"下一步：{summary}"
                    current["goal"] = summary
                    current["action_summary"] = summary
                    current["decision"] = "解码完成；候选结果见本步骤观察"
                    current["decode_source"] = {
                        "source_step": source_step.get("sequence"),
                        "source_url": source_url,
                        "field": "flag_b64" if "flag_b64" in str(source_step.get("preview", "")) else None,
                    }
            normalized.append(current)
        return normalized

    @staticmethod
    def _reproduction(index: int, step: dict[str, Any]) -> dict[str, Any]:
        arguments = public_arguments(step.get("arguments"))
        reproduction: dict[str, Any] = {
            "sequence": index, "call_id": step.get("call_id"), "tool_id": step.get("tool_id"),
            "action": step.get("action_summary") or step.get("goal"), "arguments": arguments,
            "expected": step.get("observation_summary"),
            "artifact_ids": step.get("artifact_ids", []),
        }
        if step.get("tool_id") == "builtin.localhost_http_probe":
            parsed = urlsplit(str(arguments.get("url", "")))
            header = arguments.get("ctf_header")
            reproduction["kind"] = "http"
            reproduction["http"] = {
                "method": "GET",
                "url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                "query": parse_qsl(parsed.query, keep_blank_values=True),
                "ctf_header": header if isinstance(header, dict) else None,
            }
        elif step.get("tool_id") == "ctf.encoding_decode":
            text = arguments.get("text")
            source = step.get("decode_source")
            source = source if isinstance(source, dict) else {}
            reproduction["kind"] = "decode"
            reproduction["decode"] = {
                "encoding": arguments.get("encoding", "auto"),
                "source": (
                    f"步骤 {source.get('source_step')} 的 {source.get('source_url')}"
                    if source.get("source_url") else
                    f"Artifact {arguments['artifact_id']}" if arguments.get("artifact_id") else "步骤中记录的文本"
                ),
                "source_step": source.get("source_step"),
                "source_url": source.get("source_url"),
                "field": source.get("field"),
                "input": redact(str(text)) if text is not None else None,
                "json_pointer": arguments.get("json_pointer"),
            }
        else:
            reproduction["kind"] = "generic"
        return reproduction
