from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import yaml

from yuwang.domain.models import (
    CallStatus,
    EvidenceRecord,
    EvidenceReference,
    Run,
    TaskResult,
    TaskSpec,
    Thread,
    ToolCall,
)
from yuwang.evaluation import EvaluationCriterion, EvaluationScorer
from yuwang.storage import SQLiteRepository


def _run_with_result(repository: SQLiteRepository, value: str = "flag{correct}") -> tuple[Run, TaskSpec]:
    thread = repository.save_thread(Thread(title="Judge 测试"))
    run = Run(thread_id=thread.id)
    run.transition("running")
    run.transition("completed")
    call = ToolCall(
        run_id=run.id,
        tool_name="controlled_reader",
        tool_id="controlled.reader",
        arguments={},
        input_summary="读取受控输入",
        result_summary="读取完成",
        duration_ms=1,
        status=CallStatus.SUCCEEDED,
    )
    repository.save_tool_call(call)
    run.results.append(
        TaskResult(
            result_type="flag",
            title="Flag",
            summary=value,
            scenario="ctf",
            structured_data={"value": value, "ioc": "192.0.2.10"},
            evidence=[
                EvidenceReference(
                    evidence_type="tool_call",
                    source=str(call.id),
                    content_summary="读取受控输入",
                    raw_ref=f"tool_call:{call.id}",
                    reliable=True,
                    tool_verified=True,
                )
            ],
            tool_call_ids=[call.id],
        )
    )
    repository.save_run(run)
    return run, TaskSpec(body="验证受控结果", scenario="ctf")


def test_local_judge_records_successful_exact_hash_evidence(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "judge.db")
    run, task = _run_with_result(repository)
    criterion = EvaluationCriterion(
        criterion_id="judge",
        description="私有哈希校验",
        validator_type="local_judge",
        private_config={
            "judge_type": "exact_hash",
            "field": "value",
            "expected_sha256": hashlib.sha256(b"flag{correct}").hexdigest(),
            "result_type": "flag",
        },
    )

    result = EvaluationScorer(repository).score(run, task, (criterion,))[0]

    evidence = repository.list_evidence(run.id)
    assert result.status == "passed"
    assert evidence[-1].rule_kind == "local_judge"
    assert evidence[-1].verified is True
    assert evidence[-1].candidate.startswith("{\"ioc\":")


def test_local_judge_rejects_wrong_value_and_missing_configuration(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "judge.db")
    run, task = _run_with_result(repository, "flag{wrong}")
    wrong = EvaluationCriterion(
        criterion_id="wrong",
        description="错误值",
        validator_type="local_judge",
        private_config={
            "judge_type": "structured_value",
            "field": "value",
            "expected_value": "flag{correct}",
        },
    )
    missing = EvaluationCriterion(
        criterion_id="missing",
        description="缺少配置",
        validator_type="local_judge",
        private_config={"judge_type": "exact_hash"},
    )

    results = EvaluationScorer(repository).score(run, task, (wrong, missing))

    assert [item.status for item in results] == ["failed", "configuration_error"]


def test_local_judge_does_not_execute_without_result_evidence(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "judge.db")
    thread = repository.save_thread(Thread(title="空结果"))
    run = repository.save_run(Run(thread_id=thread.id))
    criterion = EvaluationCriterion(
        criterion_id="empty",
        description="无证据不执行",
        validator_type="local_judge",
        private_config={"judge_type": "platform_result", "result": "passed"},
    )

    result = EvaluationScorer(repository).score(run, TaskSpec(body="验证"), (criterion,))[0]

    assert result.status == "not_executed"


def test_local_judge_rejects_fabricated_evidence_reference(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "fabricated-evidence.db")
    run, task = _run_with_result(repository)
    run.results[0].evidence[0].source = str(uuid4())
    run.results[0].tool_call_ids.clear()
    repository.save_run(run)
    criterion = EvaluationCriterion(
        criterion_id="fabricated",
        description="拒绝伪造来源",
        validator_type="local_judge",
        private_config={
            "judge_type": "structured_value",
            "field": "value",
            "expected_value": "flag{correct}",
        },
    )

    result = EvaluationScorer(repository).score(run, task, (criterion,))[0]

    assert result.status == "not_executed"


def test_local_judge_structured_fields_requires_all_fields_and_records_metadata(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "judge-fields.db")
    run, task = _run_with_result(repository)
    run.results[0].structured_data = {
        "algorithm": "AES-256-GCM",
        "constant": "0xC0FFEE",
    }
    repository.save_run(run)
    criterion = EvaluationCriterion(
        criterion_id="reverse-fields",
        description="逆向静态字段",
        validator_type="local_judge",
        private_config={
            "judge_type": "structured_fields",
            "expected_fields": {"algorithm": "AES-256-GCM", "constant": "0xC0FFEE"},
        },
    )

    result = EvaluationScorer(repository).score(run, task, (criterion,))[0]

    assert result.status == "passed"
    evidence = repository.list_evidence(run.id)[-1]
    assert evidence.verification_scope == "deterministic_rule"
    assert evidence.deterministic_validation_status == "passed"

    run.results[0].structured_data["constant"] = "0xBAD"
    repository.save_run(run)
    failed = EvaluationScorer(repository).score(run, task, (criterion,))[0]
    assert failed.status == "failed"


def test_reverse_package_scores_real_tool_evidence_from_zero(tmp_path) -> None:
    package_root = Path("evaluation_cases/reverse-local")
    manifest = yaml.safe_load((package_root / "manifest.yaml").read_text(encoding="utf-8"))
    judge = yaml.safe_load((package_root / "verifier" / manifest["judge"]).read_text(encoding="utf-8"))
    repository = SQLiteRepository(tmp_path / "reverse.db")
    thread = repository.save_thread(Thread(title="Reverse package"))
    run = Run(thread_id=thread.id)
    run.transition("running")
    call = ToolCall(
        run_id=run.id,
        tool_name="artifact_content_search",
        tool_id="ctf.artifact_content_search",
        arguments={"query": "algorithm="},
        input_summary="定位算法字符串",
        result_summary="第 2 行匹配 AES-256-GCM",
        duration_ms=1,
        status=CallStatus.SUCCEEDED,
        target_scope=["evaluation_cases/reverse-local/inputs"],
    )
    repository.save_tool_call(call)
    repository.save_evidence(
        EvidenceRecord(
            run_id=run.id,
            candidate="algorithm=AES-256-GCM; constant=0xC0FFEE",
            source_call_id=call.id,
            location="/matches",
            verified=True,
            verification_summary="文本检索返回第 2、3 行",
            rule_kind="tool_observation",
        )
    )
    run.results.append(
        TaskResult(
            result_type="finding",
            title="静态字符串发现",
            summary="发现算法与常量",
            scenario="reverse_static",
            structured_data={"algorithm": "AES-256-GCM", "constant": "0xC0FFEE"},
            evidence=[
                EvidenceReference(
                    evidence_type="tool_call",
                    source=str(call.id),
                    content_summary="第 2、3 行匹配",
                    raw_ref=f"tool_call:{call.id}",
                    reliable=True,
                    tool_verified=True,
                )
            ],
            tool_call_ids=[call.id],
        )
    )
    run.transition("completed")
    repository.save_run(run)
    task = TaskSpec(
        body=manifest["objective"],
        scenario=manifest["scenario"],
        authorized_targets=manifest["authorization_scope"],
        budget=manifest["budget"],
    )
    criteria = tuple(
        EvaluationCriterion(
            criterion_id=f"reverse-{index}",
            description=str(raw["validator_type"]),
            validator_type=str(raw["validator_type"]),
            expected_value=raw.get("expected_value"),
            private_config={**judge, "result_type": raw.get("result_type")}
            if raw["validator_type"] == "local_judge"
            else {},
        )
        for index, raw in enumerate(manifest["criteria"], start=1)
    )

    results = EvaluationScorer(repository).score(run, task, criteria)

    assert [item.status for item in results] == ["passed"] * len(criteria)
    evidence = repository.list_evidence(run.id)
    assert evidence[-1].rule_kind == "local_judge"
    assert evidence[-1].discovery_source == "local_judge:structured_fields"


def test_multi_artifact_package_scores_correlation_with_all_sources(tmp_path) -> None:
    package_root = Path("evaluation_cases/acceptance/multi-artifact-correlation-local")
    manifest = yaml.safe_load((package_root / "manifest.yaml").read_text(encoding="utf-8"))
    judge = yaml.safe_load((package_root / "verifier" / manifest["judge"]).read_text(encoding="utf-8"))
    repository = SQLiteRepository(tmp_path / "correlation.db")
    thread = repository.save_thread(Thread(title="Correlation package"))
    run = Run(thread_id=thread.id)
    run.transition("running")
    artifact_ids = [uuid4(), uuid4(), uuid4()]
    calls: list[ToolCall] = []
    for artifact_id, tool_id in zip(
        artifact_ids,
        ["ctf.artifact_content_search", "ctf.ioc_extract", "ctf.artifact_content_search"],
        strict=True,
    ):
        call = ToolCall(
            run_id=run.id,
            tool_name=tool_id.rsplit(".", 1)[-1],
            tool_id=tool_id,
            arguments={"artifact_id": str(artifact_id)},
            input_summary="读取授权 Artifact",
            result_summary="分析完成",
            duration_ms=1,
            status=CallStatus.SUCCEEDED,
            target_scope=["evaluation_cases/acceptance/multi-artifact-correlation-local/inputs"],
        )
        repository.save_tool_call(call)
        calls.append(call)
    run.results.append(
        TaskResult(
            result_type="finding",
            title="关联事件",
            summary="时间线、网络和文件证据指向同一事件",
            scenario="incident_response",
            structured_data={
                "event": "beacon_detected",
                "source_ip": "198.51.100.42",
                "hostname": "beacon.example.test",
                "file_sha256": "b" * 64,
            },
            evidence=[
                EvidenceReference(
                    evidence_type="tool_call",
                    source=str(calls[0].id),
                    content_summary="时间线匹配",
                    raw_ref=f"tool_call:{calls[0].id}",
                    reliable=True,
                    tool_verified=True,
                )
            ],
            tool_call_ids=[call.id for call in calls],
        )
    )
    run.transition("completed")
    repository.save_run(run)
    task = TaskSpec(
        body=manifest["objective"],
        scenario=manifest["scenario"],
        artifact_ids=artifact_ids,
        authorized_targets=manifest["authorization_scope"],
        budget=manifest["budget"],
    )
    criteria = tuple(
        EvaluationCriterion(
            criterion_id=f"correlation-{index}",
            description=str(raw["validator_type"]),
            validator_type=str(raw["validator_type"]),
            expected_value=raw.get("expected_value"),
            private_config={**judge, "result_type": raw.get("result_type")}
            if raw["validator_type"] == "local_judge"
            else {},
        )
        for index, raw in enumerate(manifest["criteria"], start=1)
    )

    results = EvaluationScorer(repository).score(run, task, criteria)

    assert [item.status for item in results] == ["passed"] * len(criteria)
