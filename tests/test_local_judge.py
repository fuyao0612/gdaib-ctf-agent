from __future__ import annotations

import hashlib

from yuwang.domain.models import (
    CallStatus,
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
