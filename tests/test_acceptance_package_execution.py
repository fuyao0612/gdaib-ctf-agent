from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from tests.fakes import FakeModelProvider, action_payload
from yuwang.control import AgentPlanDraft, TaskBriefDraft
from yuwang.domain.models import AgentAction
from yuwang.evaluation import EvaluationRunner, load_task_package_case
from yuwang.settings import AgentProfileInput, AgentProfileVersion, ProviderConfig, ProviderPreset
from yuwang.tooling import ToolCallRequest, ToolExecutor, ToolRegistry
from yuwang.tooling.builtins import LocalhostHTTPProbeTool
from yuwang.tooling.ctf import register_ctf_tools


class AcceptanceProvider(FakeModelProvider):
    """仅验证正式 Runner/Agent/SQLite/Judge 路径的确定性模型替身。"""

    async def generate_structured(self, prompt, output_type, **kwargs):
        if output_type in {AgentPlanDraft, TaskBriefDraft}:
            return await super().generate_structured(prompt, output_type, **kwargs)
        context = json.loads(prompt)
        attachments = context.get("untrusted_attachment_content", [])
        files = {item["filename"]: str(item["id"]) for item in attachments if isinstance(item, dict)}
        observations = context.get("untrusted_tool_content", [])
        names = set(files)

        def action(value: dict[str, object]):
            return output_type.model_validate(action_payload(AgentAction.model_validate(value), output_type))

        if "mixed.log" in names:
            if not observations:
                return action({"kind": "call_tool", "summary": "提取 IOC", "tool_name": "ctf.ioc_extract", "tool_input": {"artifact_id": files["mixed.log"]}})
            return action({"kind": "finish", "summary": "提交 IOC 结构化结果", "structured_output": {"result_type": "indicator", "title": "IOC", "summary": "已从日志提取并脱敏 IOC。", "structured_data": {"valid_ipv4s": ["192.0.2.10"], "ioc_types": ["cve", "email", "file_path", "ipv4", "ipv6", "sha256", "url"], "redacted": True}, "evidence_candidates": [observations[-1]["call_id"]], "confidence": 0.98}})
        if names == {"challenge.txt"}:
            if not observations:
                return action({"kind": "call_tool", "summary": "定位编码候选", "tool_name": "ctf.artifact_content_search", "tool_input": {"artifact_id": files["challenge.txt"], "query": "candidate_outer"}})
            if len(observations) == 1:
                return action({"kind": "call_tool", "summary": "执行双层解码", "tool_name": "ctf.encoding_decode", "tool_input": {"artifact_id": files["challenge.txt"], "encoding": "base64", "max_layers": 2}})
            return action({"kind": "finish", "summary": "提交解码结果", "structured_output": {"result_type": "finding", "title": "编码结果", "summary": "双层 Base64 已解码。", "structured_data": {"value": "offline-evidence-chain", "decode_chain": ["base64", "base64"]}, "evidence_candidates": [observations[-1]["call_id"]], "confidence": 0.99}})
        if {"timeline.log", "network.log", "files.log"} == names:
            order = [("timeline.log", "beacon_detected"), ("network.log", "198.51.100.42"), ("files.log", "sha256")]
            if len(observations) < 3:
                filename, query = order[len(observations)]
                return action({"kind": "call_tool", "summary": "定位取证线索", "tool_name": "ctf.artifact_content_search", "tool_input": {"artifact_id": files[filename], "query": query}})
            if len(observations) == 3:
                return action({"kind": "call_tool", "summary": "提取网络 IOC", "tool_name": "ctf.ioc_extract", "tool_input": {"artifact_id": files["network.log"]}})
            return action({"kind": "finish", "summary": "提交关联结果", "structured_output": {"result_type": "finding", "title": "关联事件", "summary": "多 Artifact 证据已关联。", "structured_data": {"event": "beacon_detected", "source_ip": "198.51.100.42", "hostname": "beacon.example.test", "file_sha256": "b" * 64}, "evidence_candidates": [item["call_id"] for item in observations if item.get("success")], "confidence": 0.97}})
        if "web-hints.txt" in names:
            if not observations:
                return action({"kind": "call_tool", "summary": "读取授权健康接口", "tool_name": "builtin.localhost_http_probe", "tool_input": {"url": "http://127.0.0.1:8080/api/v1/health"}})
            latest = observations[-1]
            return action({"kind": "finish", "summary": "提交健康状态", "structured_output": {"result_type": "finding", "title": "localhost 健康状态", "summary": "已读取授权接口。", "structured_data": {"status_code": latest["output"]["status_code"], "explicit_links": latest["output"].get("explicit_links", [])}, "evidence_candidates": [latest["call_id"]], "confidence": 0.99}})
        if "token.txt" in names:
            if not observations:
                return action({"kind": "call_tool", "summary": "静态解析 JWT", "tool_name": "ctf.jwt_analyze", "tool_input": {"artifact_id": files["token.txt"]}})
            latest = observations[-1]
            return action({"kind": "finish", "summary": "提交 JWT 研判", "structured_output": {"result_type": "finding", "title": "JWT 静态风险", "summary": "识别到空签名且缺少过期时间的 JWT。", "structured_data": {"candidate_count": 1, "algorithm": "none", "subject": "alice", "risks": ["empty_signature", "missing_exp"]}, "evidence_candidates": [latest["call_id"]], "confidence": 0.99}})
        if "recovery.log" in names:
            artifact_id = files["recovery.log"]
            if not observations:
                return action({"kind": "call_tool", "summary": "尝试编码候选", "tool_name": "ctf.encoding_decode", "tool_input": {"artifact_id": artifact_id, "encoding": "base64"}})
            if len(observations) == 1:
                return action({"kind": "call_tool", "summary": "记录失败候选", "tool_name": "ctf.encoding_decode", "tool_input": {"encoding": "base64"}})
            if len(observations) == 2:
                return action({"kind": "call_tool", "summary": "失败后改用 IOC", "tool_name": "ctf.ioc_extract", "tool_input": {"artifact_id": artifact_id}})
            if len(observations) == 3:
                return action({"kind": "call_tool", "summary": "定位最终证据", "tool_name": "ctf.artifact_content_search", "tool_input": {"artifact_id": artifact_id, "query": "198.51.100.42"}})
            return action({"kind": "finish", "summary": "提交恢复结果", "structured_output": {"result_type": "indicator", "title": "恢复后的 IOC", "summary": "已记录失败观察并完成重规划。", "structured_data": {"ioc": "198.51.100.42", "validation": "evidence_bound"}, "evidence_candidates": [observations[-1]["call_id"]], "confidence": 0.96}})
        raise AssertionError(f"unexpected acceptance attachments: {names}")


def _runner(tmp_path: Path, case_name: str) -> tuple[EvaluationRunner, object]:
    registry = ToolRegistry()
    runner = EvaluationRunner(
        tmp_path / f"{case_name}.db",
        provider=AcceptanceProvider(),
        registry=registry,
        provider_config=ProviderConfig(name="acceptance fake", preset=ProviderPreset.CUSTOM, base_url="https://provider.example/v1", model="fake", encrypted_api_key="redacted", enabled=True, is_default=True, fallback_order=0, timeout_seconds=30, max_retries=0),
        profile=AgentProfileVersion(**AgentProfileInput(name="acceptance fake", completion_mode="structured", validation_policy={"json_schema": {"type": "object"}}).model_dump(), version=1),
        artifact_root=tmp_path / f"{case_name}-artifacts",
    )
    case = load_task_package_case(Path("evaluation_cases/acceptance") / case_name)
    return runner, case


@pytest.mark.asyncio
@pytest.mark.parametrize("case_name", ["complex-ioc-local", "jwt-static-analysis-local", "multi-artifact-correlation-local", "multi-layer-encoding-local", "replan-recovery-local"])
async def test_acceptance_packages_execute_through_formal_pipeline(tmp_path: Path, case_name: str) -> None:
    runner, case = _runner(tmp_path, case_name)
    register_ctf_tools(runner.registry, runner.repository, runner.artifact_root)
    result = await runner.run_case(case)
    assert result.status == "passed", result.reason
    assert result.run_id is not None
    calls = runner.repository.list_tool_calls(result.run_id)
    assert calls
    assert all(call.target_scope == list(case.authorized_targets) for call in calls)
    assert len(calls) <= case.budget.max_tool_calls
    assert runner.repository.list_evidence(result.run_id)
    if case_name == "replan-recovery-local":
        events = runner.repository.list_events(result.run_id)
        assert any(str(event.type) == "replanned" and event.payload.get("reason") == "tool_failure" for event in events)


@pytest.mark.asyncio
async def test_localhost_acceptance_uses_real_health_endpoint_and_rejects_scope(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/api/v1/health":
                self.send_response(404)
                self.end_headers()
                return
            body = b'{"status":"ok","version":"test"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            return

    server: ThreadingHTTPServer | None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 8080), Handler)
    except OSError:
        server = None
        with urlopen("http://127.0.0.1:8080/api/v1/health", timeout=3) as response:
            assert response.status == 200
    if server is not None:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    try:
        runner, case = _runner(tmp_path, "localhost-web-analysis-local")
        runner.registry.register(LocalhostHTTPProbeTool(runner.artifact_root, runner.repository))
        result = await runner.run_case(case)
        assert result.status == "passed", result.reason
        call = runner.repository.list_tool_calls(result.run_id)[0]
        assert call.tool_id == "builtin.localhost_http_probe"
        denied = await ToolExecutor(runner.registry).execute_call(
            ToolCallRequest(
                run_id=result.run_id,
                tool_id="builtin.localhost_http_probe",
                tool_version="1.1.0",
                target_scope=["http://127.0.0.1:1"],
                arguments={"url": "http://127.0.0.1:8080/api/v1/health"},
            )
        )
        assert not denied.success
        assert denied.error and "授权范围" in denied.error.message
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
