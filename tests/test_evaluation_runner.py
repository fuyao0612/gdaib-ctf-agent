import json
from pathlib import Path

import pytest

from tests.fakes import FakeEchoTool, FakeModelProvider, action_payload
from yuwang.control import AgentPlanDraft, TaskBriefDraft
from yuwang.domain.models import AgentAction, Budget
from yuwang.evaluation import (
    EvaluationCase,
    EvaluationCriterion,
    EvaluationRunner,
    builtin_evaluation_cases,
    load_task_package_case,
)
from yuwang.settings import AgentProfileInput, AgentProfileVersion, ProviderConfig, ProviderPreset
from yuwang.tooling import ToolRegistry
from yuwang.tooling.ctf import register_ctf_tools


class SecondaryFakeEchoTool(FakeEchoTool):
    @property
    def spec(self):
        return super().spec.model_copy(
            update={
                "id": "test.secondary_echo",
                "namespace": "test",
                "name": "secondary_echo",
            }
        )


class PackageModelProvider(FakeModelProvider):
    """仅测试任务包正式执行链的 Provider 替身。"""

    def __init__(self) -> None:
        super().__init__()
        self.action_calls = 0

    async def generate_structured(self, prompt, output_type, **kwargs):
        if output_type in {AgentPlanDraft, TaskBriefDraft}:
            return await super().generate_structured(prompt, output_type, **kwargs)
        context = json.loads(prompt)
        attachments = context.get("untrusted_attachment_content", [])
        artifact_id = next(
            str(item["id"])
            for item in attachments
            if isinstance(item, dict) and item.get("filename") == "challenge.txt"
        )
        observations = context.get("untrusted_tool_content", [])
        self.action_calls += 1
        if not observations:
            action = {
                "kind": "call_tool",
                "summary": "定位编码候选",
                "tool_name": "ctf.artifact_content_search",
                "tool_input": {"artifact_id": artifact_id, "query": "candidate_outer"},
            }
        elif len(observations) == 1:
            action = {
                "kind": "call_tool",
                "summary": "尝试双层 Base64 解码",
                "tool_name": "ctf.encoding_decode",
                "tool_input": {
                    "artifact_id": artifact_id,
                    "encoding": "base64",
                    "max_layers": 2,
                },
            }
        else:
            call_id = observations[-1]["call_id"]
            action = {
                "kind": "finish",
                "summary": "提交带工具证据的结构化结果",
                "structured_output": {
                    "result_type": "finding",
                    "title": "多层编码结果",
                    "summary": "通过双层 Base64 解码获得离线证据值。",
                    "structured_data": {
                        "value": "offline-evidence-chain",
                        "decode_chain": ["base64", "base64"],
                        "evidence": "ctf.encoding_decode",
                    },
                    "evidence_candidates": [call_id],
                    "confidence": 0.99,
                },
            }
        return output_type.model_validate(
            action_payload(AgentAction.model_validate(action), output_type)
        )


@pytest.mark.asyncio
async def test_evaluation_runner_skips_when_no_real_provider_is_injected(tmp_path):
    runner = EvaluationRunner(tmp_path / "evaluation.db")
    cases = builtin_evaluation_cases()

    results = await runner.run(cases)

    assert len(results) == len(cases)
    assert {result.status for result in results} == {"skipped"}
    assert all("未显式注入" in (result.reason or "") for result in results)


@pytest.mark.asyncio
async def test_evaluation_runner_uses_agent_events_and_tool_snapshots(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    provider_config = ProviderConfig(
        name="评测配置快照",
        preset=ProviderPreset.CUSTOM,
        base_url="https://provider.example/v1",
        model="test-model",
        encrypted_api_key="test-encrypted-value",
        enabled=True,
        is_default=True,
        fallback_order=0,
        timeout_seconds=30,
        max_retries=0,
    )
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=FakeModelProvider(),  # 仅测试替身；生产运行器从不内置它。
        registry=registry,
        provider_config=provider_config,
        artifact_root=tmp_path / "artifacts",
    )
    case = EvaluationCase(
        case_id="runner-agent-path",
        name="Agent 执行路径",
        category="测试",
        allowed_tools=("builtin.test_echo",),
        user_messages=("执行一个可验证的工具任务",),
        expected_outcome="task",
        assertions=(
            "创建 Run",
            "工具快照存在",
            "Agent Profile 快照存在",
            "Provider 快照存在",
            "TOOL_STARTED 已持久化",
            "TOOL_FINISHED 已持久化",
            "生成公开任务说明",
            "快照不含明文 API Key",
        ),
    )

    result = await runner.run_case(case)

    assert result.status == "passed"
    assert result.run_id is not None
    assert {item.status for item in result.assertions} == {"passed"}
    assert runner.repository.list_events(result.run_id)


@pytest.mark.asyncio
async def test_executed_evaluation_with_unmapped_assertion_is_not_provider_unavailable(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=FakeModelProvider(),
        registry=registry,
        provider_config=ProviderConfig(
            name="评测配置快照",
            preset=ProviderPreset.CUSTOM,
            base_url="https://provider.example/v1",
            model="test-model",
            encrypted_api_key="test-encrypted-value",
            enabled=True,
            is_default=True,
            fallback_order=0,
            timeout_seconds=30,
            max_retries=0,
        ),
        artifact_root=tmp_path / "artifacts",
    )
    result = await runner.run_case(
        EvaluationCase(
            case_id="unmapped-executed-assertion",
            name="未映射断言",
            category="测试",
            allowed_tools=("builtin.test_echo",),
            user_messages=("执行一个可验证的工具任务",),
            expected_outcome="task",
            assertions=("当前未映射的运行语义",),
        )
    )

    assert result.status == "skipped"
    saved = runner.repository.get_evaluation_record(result.record_id)
    assert saved is not None
    assert saved.run_id == result.run_id
    assert saved.failure_category is None


@pytest.mark.asyncio
async def test_required_configuration_error_is_preserved_in_evaluation_record(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=FakeModelProvider(),
        registry=registry,
        artifact_root=tmp_path / "artifacts",
    )
    case = EvaluationCase(
        case_id="configuration-error",
        name="配置错误保留",
        category="测试",
        version="2.0",
        allowed_tools=("builtin.test_echo",),
        user_messages=("执行一个可验证任务",),
        expected_outcome="task",
        criteria=(
            EvaluationCriterion(
                criterion_id="unsupported",
                description="不支持的验证器",
                validator_type="unknown_validator",
            ),
        ),
        assertions=("创建 Run",),
    )

    result = await runner.run_case(case)

    saved = runner.repository.get_evaluation_record(result.record_id)
    assert result.status == "failed"
    assert saved is not None
    assert saved.failure_category == "configuration_error"
    assert saved.case_version == "2.0"
    assert saved.execution_status == "completed"


@pytest.mark.asyncio
async def test_evaluation_runner_only_snapshots_explicitly_allowed_tools(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    registry.register(SecondaryFakeEchoTool())
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=FakeModelProvider(),
        registry=registry,
        artifact_root=tmp_path / "artifacts",
    )
    case = EvaluationCase(
        case_id="single-allowed-tool",
        name="单工具最小授权",
        category="测试",
        allowed_tools=("builtin.test_echo",),
        user_messages=("执行一个可验证的工具任务",),
        expected_outcome="task",
        assertions=("创建 Run",),
    )

    result = await runner.run_case(case)

    assert result.status == "passed"
    assert result.run_id is not None
    task = runner.repository.get_run_task(result.run_id)
    assert task is not None
    assert [snapshot.tool_id for snapshot in task.tool_snapshots] == ["builtin.test_echo"]


@pytest.mark.asyncio
async def test_evaluation_runner_persists_unknown_allowed_tool_as_configuration_error(tmp_path):
    provider = FakeModelProvider()
    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=provider,
        registry=registry,
        artifact_root=tmp_path / "artifacts",
    )
    case = EvaluationCase(
        case_id="unknown-allowed-tool",
        name="未知工具配置失败",
        category="测试",
        allowed_tools=("missing.not_registered",),
        user_messages=("不应启动 Agent",),
        expected_outcome="task",
        assertions=("创建 Run",),
    )

    result = await runner.run_case(case)

    assert result.status == "failed"
    assert result.run_id is None
    assert provider.calls == 0
    assert runner.repository.list_threads() == []
    saved = runner.repository.get_evaluation_record(result.record_id)
    assert saved is not None
    assert saved.failure_category == "configuration_error"
    assert saved.execution_status == "not_executed"
    assert "missing.not_registered" in saved.finish_reason


@pytest.mark.asyncio
async def test_task_package_runs_through_artifacts_tools_and_local_judge(tmp_path):
    provider = PackageModelProvider()
    runner = EvaluationRunner(
        tmp_path / "evaluation.db",
        provider=provider,
        artifact_root=tmp_path / "artifacts",
        profile=AgentProfileVersion(
            **AgentProfileInput(
                name="任务包集成测试",
                completion_mode="structured",
                validation_policy={"json_schema": {"type": "object"}},
            ).model_dump(),
            version=1,
        ),
    )
    register_ctf_tools(runner.registry, runner.repository, runner.artifact_root)
    case = load_task_package_case(
        Path("evaluation_cases/acceptance/multi-layer-encoding-local")
    ).model_copy(update={"budget": Budget(max_steps=30, max_tool_calls=4, max_duration_seconds=60)})

    result = await runner.run_case(case)

    assert result.status == "passed"
    assert result.run_id is not None
    task = runner.repository.get_run_task(result.run_id)
    assert task is not None
    assert len(task.artifact_ids) == 1
    artifact = runner.repository.get_artifact(task.artifact_ids[0])
    assert artifact is not None
    assert artifact.filename == "challenge.txt"
    persisted_run = runner.repository.get_run(result.run_id)
    assert persisted_run is not None
    assert artifact.thread_id == persisted_run.thread_id
    calls = runner.repository.list_tool_calls(result.run_id)
    assert [call.tool_id for call in calls] == [
        "ctf.artifact_content_search",
        "ctf.encoding_decode",
    ]
    assert any(item.status == "passed" for item in result.criteria)
