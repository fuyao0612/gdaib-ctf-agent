from __future__ import annotations

from uuid import uuid4

from yuwang.domain.models import (
    Artifact,
    CallStatus,
    EventType,
    EvidenceRecord,
    Run,
    TaskSpec,
    Thread,
    ToolCall,
)
from yuwang.results import TaskResultService
from yuwang.storage import SQLiteRepository


def _completed_run(repository: SQLiteRepository) -> Run:
    thread = repository.save_thread(Thread(title="结果服务测试"))
    run = Run(thread_id=thread.id)
    run.transition("running")
    run.transition("completed")
    return repository.save_run(run)


def test_result_service_persists_multiple_evidence_bound_results(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "results.db")
    run = _completed_run(repository)
    task = TaskSpec(body="提取 IOC", scenario="incident_response")
    evidence = EvidenceRecord(
        run_id=run.id,
        candidate="192.0.2.10",
        source_call_id=uuid4(),
        location="/iocs/ips/0",
        verified=True,
        verification_summary="本地确定性检查通过",
        rule_kind="local_judge",
        deterministic_validation_status="passed",
    )
    repository.save_evidence(evidence)

    results = TaskResultService(repository).persist(
        run,
        task,
        {
            "results": [
                {
                    "result_type": "indicator",
                    "title": "IP IOC",
                    "summary": "发现一个 IP IOC",
                    "structured_data": {"iocs": {"ips": ["192.0.2.10"]}},
                    "evidence_candidates": [str(evidence.id)],
                    "confidence": 0.8,
                },
                {
                    "result_type": "assessment",
                    "title": "事件研判",
                    "summary": "需要继续排查横向活动",
                    "evidence_candidates": [str(evidence.id)],
                    "confidence": 0.5,
                },
            ]
        },
    )

    saved = repository.get_run(run.id)
    assert len(results) == 2
    assert saved and len(saved.results) == 2
    assert all(item.validation_status == "validated" for item in saved.results)
    assert all(item.evidence[0].raw_ref == f"evidence:{evidence.id}" for item in saved.results)


def test_result_service_rejects_forged_or_cross_run_evidence_ids(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "results.db")
    run = _completed_run(repository)
    foreign_run = _completed_run(repository)
    foreign = EvidenceRecord(
        run_id=foreign_run.id,
        candidate="foreign",
        source_call_id=uuid4(),
        location="/candidate",
        verified=True,
        verification_summary="不属于当前运行",
    )
    repository.save_evidence(foreign)

    result = TaskResultService(repository).persist(
        run,
        TaskSpec(body="分析", scenario="general"),
        {
            "result_type": "assessment",
            "title": "候选结论",
            "summary": "模型候选",
            "evidence_candidates": [str(foreign.id), str(uuid4())],
        },
    )[0]

    assert result.evidence == []
    assert result.validation_status == "unverified"
    assert result.validator_name == "none"


def test_security_recovery_is_never_synthesized_from_policy_events(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "results.db")
    run = _completed_run(repository)
    artifact = repository.save_artifact(
        Artifact(
            thread_id=run.thread_id,
            filename="hostile.txt",
            kind="upload",
            sha256="b" * 64,
            size=1,
            mime_type="text/plain",
            storage_ref="thread/hostile.blob",
            contains_prompt_injection=True,
        )
    )
    repository.create_event(
        run.id,
        EventType.POLICY_CHECKED,
        "拒绝不可信附件策略覆盖",
        {"reason": "untrusted_prompt_injection", "allowed": False},
    )

    result = TaskResultService(repository).persist(
        run,
        TaskSpec(body="总结已授权附件", artifact_ids=[artifact.id]),
        {
            "result_type": "assessment",
            "title": "摘要",
            "summary": "合法摘要",
            "structured_data": {},
        },
    )[0]

    assert result.structured_data == {}


def test_authorized_inspection_evidence_binds_an_assessment_only_when_tool_succeeded(
    tmp_path,
) -> None:
    repository = SQLiteRepository(tmp_path / "inspection.db")
    run = _completed_run(repository)
    tool_call = ToolCall(
        run_id=run.id,
        tool_name="文件安全检查",
        tool_id="ctf.file_inspect",
        input_summary="检查当前授权附件",
        result_summary="文件安全检查执行成功",
        duration_ms=1,
        status=CallStatus.SUCCEEDED,
    )
    repository.save_tool_call(tool_call)
    evidence = EvidenceRecord(
        run_id=run.id,
        candidate=f"ctf.file_inspect:{tool_call.id}",
        source_call_id=tool_call.id,
        location="/",
        verified=False,
        verification_summary="ctf.file_inspect 已成功检查当前授权附件；不代表摘要内容已验证",
        rule_kind="authorized_attachment_inspection",
        discovery_source="tool_call",
    )
    repository.save_evidence(evidence)

    result = TaskResultService(repository).persist(
        run,
        TaskSpec(body="总结已授权附件", scenario="incident_response"),
        {"result_type": "assessment", "title": "合法摘要", "summary": "仅总结授权附件"},
    )[0]

    assert result.evidence[0].source == str(tool_call.id)
    assert result.evidence[0].tool_verified is False
