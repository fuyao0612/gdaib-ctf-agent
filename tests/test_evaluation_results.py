import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from yuwang.domain.evaluation import EvaluationRecord, summarize_evaluations
from yuwang.domain.models import Run, TaskSpec, Thread
from yuwang.evaluation import EvaluationCase, EvaluationRunner, load_golden_case
from yuwang.evaluation.golden import evaluate_golden_run
from yuwang.storage import SQLiteRepository


def record(**overrides) -> EvaluationRecord:
    values = {
        "case_id": "case-one",
        "category": "任务",
        "difficulty": "基础",
        "provider": "测试 Provider",
        "model": "test-model",
        "attempt": 1,
        "duration_ms": 120,
        "model_calls": 2,
        "tool_calls": 1,
        "input_tokens": 30,
        "output_tokens": 10,
        "estimated_cost": 0.02,
        "success": True,
        "status": "passed",
        "finish_reason": "断言全部通过",
    }
    values.update(overrides)
    return EvaluationRecord(**values)


def test_evaluation_result_storage_filters_and_summarizes(tmp_path):
    repository = SQLiteRepository(tmp_path / "evaluation.db")
    passed = repository.save_evaluation_record(record())
    failed = repository.save_evaluation_record(
        record(
            case_id="case-two",
            category="恢复",
            difficulty="进阶",
            status="failed",
            success=False,
            finish_reason="模型超时",
            failure_category="provider_failure",
        )
    )
    repository.save_evaluation_record(
        record(
            case_id="case-three",
            provider=None,
            model=None,
            status="skipped",
            success=False,
            finish_reason="未配置 Provider",
            failure_category="provider_unavailable",
        )
    )

    assert repository.get_evaluation_record(passed.id) == passed
    filtered = repository.list_evaluation_records(category="恢复")
    assert [value.id for value in filtered] == [failed.id]
    statistics = summarize_evaluations(repository.list_evaluation_records())
    assert statistics.total == 3
    assert statistics.success_rate == 0.5
    assert statistics.pass_at_1 == 0.5
    assert statistics.pass_at_3 == 0.5
    assert statistics.median_duration_ms == 120
    assert statistics.average_tokens == 40
    assert statistics.failure_categories == {
        "provider_failure": 1,
        "provider_unavailable": 1,
    }


def test_evaluation_statistics_calculates_pass_at_three_per_case_and_operation_metrics():
    statistics = summarize_evaluations(
        [
            record(case_id="retry", attempt=1, status="failed", success=False, duration_ms=90),
            record(
                case_id="retry",
                attempt=2,
                status="passed",
                success=True,
                duration_ms=150,
                tool_calls=3,
                replans=1,
                manual_interventions=2,
            ),
            record(case_id="single", attempt=1, status="passed", success=True, duration_ms=120),
            record(case_id="skip", attempt=1, status="skipped", success=False),
        ]
    )

    assert statistics.pass_at_1 == 0.5
    assert statistics.pass_at_3 == 1
    assert statistics.median_duration_ms == 120
    assert statistics.average_tool_calls == pytest.approx(5 / 3)
    assert statistics.average_replans == pytest.approx(1 / 3)
    assert statistics.average_manual_interventions == pytest.approx(2 / 3)


def test_evaluation_api_reads_persisted_results_and_statistics(tmp_path):
    app = create_app(
        Settings(
            database_path=tmp_path / "api.db",
            artifact_root=tmp_path / "artifacts",
            master_key=Fernet.generate_key().decode(),
        )
    )
    saved = app.state.repository.save_evaluation_record(record(case_id="api-case", category="API"))

    with TestClient(app) as client:
        session = client.post("/api/v1/admin/session")
        assert session.status_code == 200, session.text
        client.headers.update({"X-CSRF-Token": session.json()["csrf_token"]})
        listed = client.get("/api/v1/evaluations", params={"category": "API"})
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [str(saved.id)]
        statistics = client.get("/api/v1/evaluations/statistics", params={"category": "API"})
        assert statistics.status_code == 200, statistics.text
        assert statistics.json()["success_rate"] == 1
        detail = client.get(f"/api/v1/evaluations/{saved.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["case_id"] == "api-case"
        exported_json = client.get("/api/v1/evaluations/export.json", params={"category": "API"})
        assert exported_json.status_code == 200, exported_json.text
        assert exported_json.json()[0]["case_version"] == "1.0"
        exported_csv = client.get("/api/v1/evaluations/export.csv", params={"category": "API"})
        assert exported_csv.status_code == 200, exported_csv.text
        assert "provider_requests" in exported_csv.text


def test_golden_case_loader_only_allows_bundled_cases_and_keeps_judge_private():
    case = load_golden_case("C-prompt-injection")
    assert case.case_id == "golden-prompt-injection"
    assert case.judge_config["judge_type"] == "structured_value"
    with pytest.raises(ValueError, match="未知黄金案例"):
        load_golden_case("../../etc")


def test_golden_evaluation_rejects_an_unbound_similar_run(tmp_path):
    repository = SQLiteRepository(tmp_path / "golden.db")
    thread = repository.save_thread(Thread(title="相似运行"))
    run = Run(thread_id=thread.id)
    run.transition("running")
    run.transition("completed")
    repository.save_run(run)
    repository.save_run_task(run.id, TaskSpec(body="看起来像黄金案例", scenario="ctf"))

    with pytest.raises(ValueError, match="未建立黄金案例绑定"):
        evaluate_golden_run(repository, run, load_golden_case("A-ctf-attachment"))


@pytest.mark.asyncio
async def test_skipped_evaluation_is_persisted_without_creating_a_run(tmp_path):
    runner = EvaluationRunner(tmp_path / "evaluation.db")
    case = EvaluationCase(
        case_id="persisted-skip",
        name="持久化跳过",
        category="测试",
        user_messages=("执行任务",),
        expected_outcome="task",
        assertions=("创建 Run",),
    )

    result = await runner.run_case(case)

    assert result.status == "skipped"
    assert result.run_id is None
    assert result.record_id is not None
    saved = runner.repository.get_evaluation_record(result.record_id)
    assert saved is not None
    assert saved.status == "skipped"
    assert saved.failure_category == "provider_unavailable"
