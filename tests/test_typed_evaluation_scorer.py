from uuid import uuid4

from yuwang.domain.models import Artifact, EvidenceRecord, Run, TaskSpec, Thread
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
            verification_summary="格式和确定性规则均通过",
            rule_kind="flag_format",
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
