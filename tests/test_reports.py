from __future__ import annotations

from uuid import uuid4

from yuwang.domain.models import (
    Artifact,
    CallStatus,
    ExecutionStep,
    Run,
    TaskSpec,
    Thread,
    ToolCall,
)
from yuwang.reports.facts import public_arguments
from yuwang.reports.generator import ReportGenerator
from yuwang.reports.trace import RunTraceService
from yuwang.storage import SQLiteRepository


def test_ctf_report_renders_persisted_timeline_without_duplicate_h1() -> None:
    run = Run(thread_id=uuid4())
    task = TaskSpec(body="完成授权 CTF 题", scenario="ctf")
    call_id = str(uuid4())
    steps = [
        {
            "sequence": 1, "call_id": call_id, "goal": "读取首页", "action_summary": "请求首页",
            "observation_summary": "HTTP 200；页面显式链接：/robots.txt", "decision": "下一步：读取 robots.txt",
        },
        {
            "sequence": 2, "call_id": str(uuid4()), "goal": "读取 robots", "action_summary": "请求 robots.txt",
            "observation_summary": "robots.txt 暴露路径：/dev-notes.txt", "decision": "下一步：读取开发说明",
        },
        {
            "sequence": 3, "call_id": str(uuid4()), "goal": "读取说明", "action_summary": "请求 /dev-notes.txt",
            "observation_summary": "开发说明给出 /api/debug 的访问条件", "decision": "下一步：请求调试接口",
        },
        {
            "sequence": 4, "call_id": str(uuid4()), "goal": "读取调试接口", "action_summary": "请求 /api/debug",
            "tool_id": "builtin.localhost_http_probe",
            "arguments": {"url": "http://127.0.0.1:8088/api/debug?unlock=1", "ctf_header": {"name": "X-CTF-Token", "value": "sunrise-7"}},
            "observation_summary": "响应正文包含 flag_b64 字段", "decision": "下一步：使用 Base64 解码",
        },
        {
            "sequence": 5, "call_id": str(uuid4()), "goal": "解码", "action_summary": "执行 Base64 解码",
            "tool_id": "ctf.encoding_decode", "arguments": {"text": "ZmxhZ3tkZW1vfQ==", "encoding": "base64"},
            "observation_summary": "解码得到 1 个高置信 Flag 候选", "decision": "下一步：校验 Flag 格式",
        },
        {
            "sequence": 6, "call_id": str(uuid4()), "goal": "校验候选", "action_summary": "检查 Flag 候选格式",
            "observation_summary": "候选 Flag flag{demo}；格式校验状态：format_matched；尚未经过赛题平台验证", "decision": "结束：已完成格式校验",
        },
    ]
    markdown, data = ReportGenerator().generate(run, task, [], {
        "validation_status": "partial", "trace": {"steps": steps, "metrics": {"logical_model_calls": 2, "provider_requests": 3, "tool_calls": 6, "tool_failures": 0}, "artifacts": [{"filename": "debug.json", "kind": "http_response", "size": 42, "sha256": "a" * 64}]},
        "evidence_records": [{"candidate": "flag{demo}", "source_call_id": call_id, "rule_kind": "flag_format", "verified": False}],
    })
    assert data["report_kind"] == "ctf"
    assert sum(line.startswith("# ") and not line.startswith("## ") for line in markdown.splitlines()) == 1
    for path in ("/robots.txt", "/dev-notes.txt", "/api/debug"):
        assert path in markdown
    assert "尚未经过赛题平台验证" in markdown
    assert "Artifact 清单" in markdown
    assert "逻辑模型调用：2，实际 Provider 请求：3" in markdown
    assert "参数：`{" not in markdown
    assert "X-CTF-Token" in markdown and "sunrise-7" in markdown
    assert "`unlock` = `1`" in markdown
    assert "使用 `base64` 解码" in markdown


def test_final_answer_flag_is_reported_without_evidence_record() -> None:
    run = Run(thread_id=uuid4())
    task = TaskSpec(body="完成授权 CTF", scenario="general")
    markdown, data = ReportGenerator().generate(
        run,
        task,
        [],
        {
            "validation_status": "unverified",
            "final_answer": "最终结果是 flag{persisted_answer}",
            "trace": {"steps": [], "metrics": {}, "artifacts": []},
        },
    )
    assert data["report_kind"] == "ctf"
    assert data["flag_candidates"][0]["candidate"] == "flag{persisted_answer}"
    assert data["flag_candidates"][0]["platform_verified"] is False
    assert "未发现 Flag 候选" not in markdown
    assert "尚未完成外部验证" in markdown


def test_report_keeps_custom_prefix_candidate_and_deduplicates_sources() -> None:
    run = Run(thread_id=uuid4())
    task = TaskSpec(body="完成授权 CTF", scenario="ctf")
    call_id = str(uuid4())
    _, data = ReportGenerator().generate(
        run,
        task,
        [],
        {
            "validation_status": "unverified",
            "trace": {
                "steps": [{
                    "sequence": 1, "call_id": call_id, "tool_id": "ctf.encoding_decode",
                    "observation_summary": "解码完成", "preview": "GDAIB{custom_prefix}",
                }],
                "tool_calls": [{"id": call_id, "result_summary": "GDAIB{custom_prefix}"}],
                "metrics": {}, "artifacts": [],
            },
            "evidence_records": [{
                "candidate": "GDAIB{custom_prefix}", "source_call_id": call_id,
                "source_step": 1, "location": "/candidates/0/value",
                "discovery_source": "encoding_decode", "format_status": "not_checked",
                "verification_scope": "none", "verified": False,
                "verification_summary": "工具输出发现候选值，尚未执行验证",
            }],
        },
    )
    candidate = data["flag_candidates"][0]
    assert candidate["candidate"] == "GDAIB{custom_prefix}"
    assert candidate["source_call_id"] == call_id
    assert candidate["location"] == "/candidates/0/value"
    assert len(candidate["sources"]) == 1
    assert candidate["platform_verified"] is False


def test_unverified_general_report_does_not_claim_a_flag_candidate() -> None:
    run = Run(thread_id=uuid4())
    task = TaskSpec(body="总结普通文本", scenario="general")
    _, data = ReportGenerator().generate(
        run, task, [], {
            "validation_status": "unverified", "final_answer": "普通结论",
            "trace": {"steps": [], "metrics": {}, "artifacts": []},
        },
    )
    assert data["validation_label"] == "结果未经外部验证"
    assert data["flag_candidates"] == []
    for key in ("mode", "status", "result", "plan", "execution_mode", "evidence", "duration_ms", "errors", "policy_checks"):
        assert key in data


def test_historical_ctf_trace_recovers_legacy_artifacts_and_uses_real_request_parameters(tmp_path) -> None:
    """Old HTTP artifacts have no run_id, but their persisted step IDs are authoritative."""

    repository = SQLiteRepository(tmp_path / "historical-ctf.db")
    thread = repository.save_thread(Thread(title="历史 CTF 运行"))
    run = repository.save_run(Run(thread_id=thread.id))
    task = TaskSpec(body="拿到授权本机靶场的 Flag", authorized_targets=["http://127.0.0.1:8088/"])
    repository.save_run_task(run.id, task)
    steps = [
        ("http://127.0.0.1:8088/", None, "首页声明 robots.txt"),
        ("http://127.0.0.1:8088/robots.txt", None, "robots.txt 指向 /dev-notes.txt"),
        ("http://127.0.0.1:8088/dev-notes.txt", {"name": "X-CTF-Build-Token", "value": "sunrise-7"}, "开发说明给出 /api/debug"),
        ("http://127.0.0.1:8088/api/debug?unlock=1", {"name": "X-CTF-Token", "value": "sunrise-7"}, "HTTP 200；响应正文包含 flag_b64 字段"),
    ]
    for sequence, (url, header, observation) in enumerate(steps, 1):
        call_id, artifact_id = uuid4(), uuid4()
        arguments = {"url": url, **({"ctf_header": header} if header else {})}
        repository.save_tool_call(
            ToolCall(
                id=call_id, run_id=run.id, tool_name="localhost_http_probe",
                tool_id="builtin.localhost_http_probe", arguments=arguments,
                input_summary="读取已经公开的本机 CTF 线索", result_summary="HTTP 请求完成",
                duration_ms=10, status=CallStatus.SUCCEEDED, artifact_ids=[artifact_id],
            )
        )
        repository.save_execution_step(
            ExecutionStep(
                run_id=run.id, sequence=sequence, call_id=call_id, goal="跟随公开线索",
                action_kind="tool_call", action_summary="请求本机 HTTP 资源",
                tool_id="builtin.localhost_http_probe", tool_name="localhost_http_probe",
                arguments=arguments, observation_status="success", observation_summary=observation,
                preview=("flag_b64=ZmxhZ3tsb2NhbF9hZ2VudF9mb3VuZF90aGVfZGVidWdfZG9vcn0=" if sequence == 4 else None),
                decision=("下一步：Decode base64 flag from /api/status response." if sequence == 4 else None),
                artifact_ids=[artifact_id], duration_ms=10,
            )
        )
        # This represents data written before the probe recorded run_id.
        repository.save_artifact(
            Artifact(
                id=artifact_id, thread_id=thread.id, filename=f"response-{sequence}.txt",
                kind="http_evidence", sha256=f"{sequence:x}" * 64, size=100 + sequence,
                mime_type="text/plain", storage_ref=f"{thread.id}/response-{sequence}.txt",
            )
        )
    decode_id = uuid4()
    repository.save_tool_call(
        ToolCall(
            id=decode_id, run_id=run.id, tool_name="常见编码解码", tool_id="ctf.encoding_decode",
            arguments={"text": "ZmxhZ3tsb2NhbF9hZ2VudF9mb3VuZF90aGVfZGVidWdfZG9vcn0=", "encoding": "base64"},
            input_summary="解码 flag_b64", result_summary="解码得到 flag{local_agent_found_the_debug_door}",
            duration_ms=5, status=CallStatus.SUCCEEDED,
        )
    )
    repository.save_execution_step(
        ExecutionStep(
            run_id=run.id, sequence=5, call_id=decode_id, goal="解码 flag_b64", action_kind="tool_call",
            action_summary="Decode base64 flag from /api/status response.", tool_id="ctf.encoding_decode", tool_name="常见编码解码",
            arguments={"text": "ZmxhZ3tsb2NhbF9hZ2VudF9mb3VuZF90aGVfZGVidWdfZG9vcn0=", "encoding": "base64"},
            observation_status="success", observation_summary="解码得到 flag{local_agent_found_the_debug_door}",
            preview="flag{local_agent_found_the_debug_door}", duration_ms=5,
        )
    )

    trace = RunTraceService(repository).snapshot(run.id)
    markdown, data = ReportGenerator().generate(
        run, task, [], {"validation_status": "unverified", "trace": trace, "final_answer": "flag{local_agent_found_the_debug_door}"}
    )

    assert len(trace["artifacts"]) == 4
    assert [item["filename"] for item in trace["artifacts"]] == [f"response-{item}.txt" for item in range(1, 5)]
    assert "flag{local_agent_found_the_debug_door}" in markdown
    assert "/api/debug" in markdown and "`unlock` = `1`" in markdown
    assert "X-CTF-Token" in markdown and "sunrise-7" in markdown
    assert "来自 /api/debug 响应的已记录值" in markdown
    assert "from /api/status response" not in markdown
    assert "参数：`{" not in markdown
    assert data["flag_candidates"][0]["candidate"] == "flag{local_agent_found_the_debug_door}"


def test_public_arguments_keeps_only_explicit_ctf_header_values() -> None:
    assert public_arguments({"ctf_header": {"name": "X-CTF-Token", "value": "sunrise-7"}})["ctf_header"]["value"] == "sunrise-7"
    assert public_arguments({"ctf_header": {"name": "Authorization", "value": "Bearer private"}})["ctf_header"]["value"] == "[REDACTED]"
