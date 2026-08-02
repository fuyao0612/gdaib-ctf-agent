from uuid import uuid4

from yuwang.domain.models import Artifact, EvidenceRecord, Run, TaskResult, TaskSpec, Thread
from yuwang.evaluation import EvaluationCriterion, EvaluationScorer
from yuwang.storage import SQLiteRepository


def test_ctf_score_requires_deterministic_flag_evidence(tmp_path):
    repository = SQLiteRepository(tmp_path / "score.db")
    thread = repository.save_thread(Thread(title="CTF 评测"))
    run = Run(thread_id=thread.id)
    run.transition("running")
    run.transition("completed")
    repository.save_run(run)
    task = TaskSpec(body="验证授权 CTF 结果", scenario="ctf")
    repository.save_run_task(run.id, task)
    call_id = uuid4()
    repository.save_evidence(
        EvidenceRecord(
            run_id=run.id,
            candidate="flag{deterministic}",
            source_call_id=call_id,
            location="/candidate",
            verified=True,
            verification_summary="本地 Judge 已确认答案正确",
            rule_kind="local_judge",
            verification_scope="deterministic_rule",
            deterministic_validation_status="passed",
        )
    )

    scorer = EvaluationScorer(repository)
    result = scorer.score(
        run,
        task,
        (
            EvaluationCriterion(
                criterion_id="flag-proof",
                description="必须存在确定性 Flag 证据",
                validator_type="flag_evidence",
            ),
        ),
    )
    assert result[0].status == "passed"


def test_format_only_flag_never_passes_score(tmp_path):
    repository = SQLiteRepository(tmp_path / "score.db")
    thread = repository.save_thread(Thread(title="CTF 格式候选"))
    run = Run(thread_id=thread.id)
    run.transition("running")
    run.transition("completed")
    repository.save_run(run)
    task = TaskSpec(body="验证 CTF 结果", scenario="ctf")
    repository.save_run_task(run.id, task)
    repository.save_evidence(
        EvidenceRecord(
            run_id=run.id,
            candidate="flag{wrong-but-well-formed}",
            source_call_id=uuid4(),
            location="/candidate",
            verified=False,
            verification_summary="仅格式匹配",
            rule_kind="flag_format",
            verification_scope="format",
            deterministic_validation_status="passed",
        )
    )

    result = EvaluationScorer(repository).score(
        run,
        task,
        (
            EvaluationCriterion(
                criterion_id="flag", description="独立 Judge", validator_type="flag_evidence"
            ),
        ),
    )
    assert result[0].status == "failed"


def test_registry_reports_configuration_and_not_executed_states(tmp_path):
    repository = SQLiteRepository(tmp_path / "score.db")
    thread = repository.save_thread(Thread(title="评分器状态"))
    run = repository.save_run(Run(thread_id=thread.id))
    task = TaskSpec(body="评分器状态")
    results = EvaluationScorer(repository).score(
        run,
        task,
        (
            EvaluationCriterion(
                criterion_id="unknown", description="未知验证器", validator_type="unknown_validator"
            ),
            EvaluationCriterion(
                criterion_id="provider",
                description="缺少 Provider 快照",
                validator_type="provider_snapshot",
            ),
        ),
    )
    assert [item.status for item in results] == ["configuration_error", "not_executed"]


def test_result_validators_only_read_persisted_run_results(tmp_path):
    repository = SQLiteRepository(tmp_path / "score.db")
    thread = repository.save_thread(Thread(title="IOC 结果"))
    run = Run(thread_id=thread.id)
    run.results.append(
        TaskResult(
            result_type="indicator",
            title="IOC",
            summary="日志中发现 IOC",
            scenario="incident_response",
            structured_data={"ioc": "192.0.2.10"},
        )
    )
    repository.save_run(run)
    task = TaskSpec(body="提取 IOC", scenario="incident_response")
    results = EvaluationScorer(repository).score(
        run,
        task,
        (
            EvaluationCriterion(
                criterion_id="ioc-result",
                description="有 IOC 结果",
                validator_type="result_exists",
                expected_value="indicator",
            ),
            EvaluationCriterion(
                criterion_id="ioc-value",
                description="IOC 值一致",
                validator_type="result_field_equals",
                expected_value={"field": "ioc", "value": "192.0.2.10"},
            ),
        ),
    )
    assert [item.status for item in results] == ["passed", "passed"]


def test_non_ctf_score_can_verify_artifact_hash_without_flag_fields(tmp_path):
    repository = SQLiteRepository(tmp_path / "score.db")
    thread = repository.save_thread(Thread(title="日志分析评测"))
    content = b"ioc=192.0.2.10\n"
    import hashlib

    artifact = repository.save_artifact(
        Artifact(
            thread_id=thread.id,
            filename="events.log",
            kind="upload",
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            mime_type="text/plain",
            storage_ref="thread/artifact.blob",
        )
    )
    run = Run(thread_id=thread.id)
    run.transition("running")
    run.transition("completed")
    repository.save_run(run)
    task = TaskSpec(body="从日志提取 IOC", artifact_ids=[artifact.id], scenario="incident_response")
    repository.save_run_task(run.id, task)

    result = EvaluationScorer(repository).score(
        run,
        task,
        (
            EvaluationCriterion(
                criterion_id="artifact-integrity",
                description="输入日志哈希必须一致",
                validator_type="artifact_sha256",
                expected_value=artifact.sha256,
            ),
        ),
    )
    assert result[0].status == "passed"
