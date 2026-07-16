from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from yuwang.control import PlanRevision, PlanSource, TaskBrief, TaskBriefSource
from yuwang.domain.models import AgentPlan, Run, RunStatus, Thread
from yuwang.storage import SQLiteRepository


def test_task_brief_versions_preserve_original_request(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "control.db")
    run_id = uuid4()
    first = repository.save_task_brief(
        TaskBrief(
            run_id=run_id,
            version=1,
            original_request="整理部署步骤",
            goal="生成可执行的部署说明",
            needs_clarification=True,
            clarification_questions=["目标系统是什么？"],
        )
    )
    repository.save_task_brief(
        first.model_copy(
            update={
                "id": uuid4(),
                "version": 2,
                "goal": "生成 Windows 部署说明",
                "needs_clarification": False,
                "clarification_questions": [],
                "source": TaskBriefSource.USER_CLARIFICATION,
            }
        )
    )

    assert [item.version for item in repository.list_task_briefs(run_id)] == [1, 2]
    assert repository.latest_task_brief(run_id).goal == "生成 Windows 部署说明"
    with pytest.raises(ValueError, match="原始要求不可修改"):
        repository.save_task_brief(
            first.model_copy(
                update={"id": uuid4(), "version": 3, "original_request": "替换原文"}
            )
        )


def test_plan_revisions_are_consecutive_and_reuse_agent_plan(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "control.db")
    run_id = uuid4()
    plan = AgentPlan(summary="先确认范围", steps=["确认目标"], success_approach="用户确认")
    repository.save_plan_revision(
        PlanRevision(run_id=run_id, version=1, plan=plan, source=PlanSource.AGENT_INITIAL)
    )
    repository.save_plan_revision(
        PlanRevision(
            run_id=run_id,
            version=2,
            plan=AgentPlan(
                summary=plan.summary,
                steps=["确认目标", "生成说明"],
                success_approach=plan.success_approach,
            ),
            source=PlanSource.USER_EDIT,
            based_on_version=1,
            change_reason="补充输出步骤",
        )
    )

    assert repository.latest_plan_revision(run_id).source == PlanSource.USER_EDIT
    with pytest.raises(ValueError, match="直接前一版本"):
        PlanRevision(
            run_id=run_id,
            version=3,
            plan=plan,
            source=PlanSource.AGENT_REPLAN,
            based_on_version=1,
        )


def test_v4_migration_is_idempotent(tmp_path) -> None:
    path = tmp_path / "upgrade.db"
    repository = SQLiteRepository(path)
    repository.migrate()
    with repository.connect() as database:
        versions = {
            row["version"] for row in database.execute("SELECT version FROM schema_migrations")
        }
    assert {1, 2, 3, 4, 5}.issubset(versions)


def test_concurrent_plan_decisions_only_claim_once(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "control.db")
    run = Run(thread_id=uuid4(), plan_mode="approval")
    run.transition(RunStatus.RUNNING)
    run.transition(RunStatus.WAITING_APPROVAL)
    repository.save_run(run)
    repository.save_plan_revision(
        PlanRevision(
            run_id=run.id,
            version=1,
            plan=AgentPlan(summary="计划", steps=["执行"], success_approach="验证"),
            source=PlanSource.AGENT_INITIAL,
        )
    )

    def claim(index: int) -> bool:
        try:
            _, claimed = repository.claim_run_control(
                run.id,
                uuid4(),
                "plan_approve",
                f"hash-{index}",
                RunStatus.WAITING_APPROVAL,
                1,
            )
            return claimed
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(claim, range(4)))
    assert results.count(True) == 1


def test_deleting_thread_removes_control_history(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "control.db")
    thread = repository.save_thread(Thread(title="delete control"))
    run = repository.save_run(Run(thread_id=thread.id))
    repository.save_task_brief(
        TaskBrief(
            run_id=run.id,
            version=1,
            original_request="删除测试",
            goal="确认无孤立控制记录",
        )
    )
    repository.save_plan_revision(
        PlanRevision(
            run_id=run.id,
            version=1,
            plan=AgentPlan(summary="删除", steps=["删除"], success_approach="查询为空"),
            source=PlanSource.AGENT_INITIAL,
        )
    )

    repository.delete_thread(thread.id)

    assert repository.list_task_briefs(run.id) == []
    assert repository.list_plan_revisions(run.id) == []
