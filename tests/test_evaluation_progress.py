from uuid import uuid4

import pytest

from yuwang.evaluation import (
    EvaluationAssertionResult,
    EvaluationCase,
    EvaluationProgress,
    EvaluationProgressStore,
    EvaluationResult,
    EvaluationRunner,
)


def evaluation_case(case_id: str = "resume-case") -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        name="恢复测试",
        category="测试",
        user_messages=("请回复",),
        expected_outcome="chat",
        assertions=("返回自然语言回复",),
        max_attempts=2,
    )


def persisted_result(case_id: str) -> EvaluationResult:
    return EvaluationResult(
        case_id=case_id,
        status="failed",
        record_id=uuid4(),
        assertions=(
            EvaluationAssertionResult(assertion="测试", status="failed", detail="已完成"),
        ),
        reason="评测失败",
    )


def test_progress_store_is_small_and_omits_responses_flags_and_credentials(tmp_path):
    case = evaluation_case()
    progress = EvaluationProgress.create(provider_id=uuid4(), cases=(case,), attempts=2)
    progress = progress.add_result(case=case, attempt=1, result=persisted_result(case.case_id))
    path = tmp_path / "progress.json"

    EvaluationProgressStore(path).save(progress)

    loaded = EvaluationProgressStore(path).load()
    content = path.read_text(encoding="utf-8")
    assert loaded.completed_keys == {(case.case_id, 1)}
    assert "api_key" not in content
    assert "flag{" not in content
    assert "请回复" not in content
    assert "评测失败" not in content


def test_progress_rejects_incompatible_resume_parameters():
    provider_id = uuid4()
    progress = EvaluationProgress.create(
        provider_id=provider_id,
        cases=(evaluation_case(),),
        attempts=2,
    )

    with pytest.raises(ValueError, match="Provider"):
        progress.ensure_compatible(provider_id=uuid4(), cases=(evaluation_case(),), attempts=2)
    with pytest.raises(ValueError, match="用例"):
        progress.ensure_compatible(
            provider_id=provider_id,
            cases=(evaluation_case("different-case"),),
            attempts=2,
        )
    with pytest.raises(ValueError, match="尝试次数"):
        progress.ensure_compatible(provider_id=provider_id, cases=(evaluation_case(),), attempts=1)


@pytest.mark.asyncio
async def test_runner_skips_completed_attempts_and_calls_checkpoint_after_persistence(tmp_path):
    runner = EvaluationRunner(tmp_path / "evaluation.db")
    case = evaluation_case()
    completed: list[tuple[str, int, EvaluationResult]] = []

    async def checkpoint(
        completed_case: EvaluationCase, attempt: int, result: EvaluationResult
    ) -> None:
        assert result.record_id is not None
        assert runner.repository.get_evaluation_record(result.record_id) is not None
        completed.append((completed_case.case_id, attempt, result))

    results = await runner.run(
        (case,),
        attempts=2,
        completed_attempts={(case.case_id, 1)},
        on_attempt_completed=checkpoint,
    )

    assert [result.case_id for result in results] == [case.case_id]
    assert [attempt for _, attempt, _ in completed] == [2]
    assert len(runner.repository.list_evaluation_records()) == 1
