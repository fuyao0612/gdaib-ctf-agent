from __future__ import annotations

from uuid import uuid4

import pytest

from yuwang.control import PlanRevision, PlanSource, TaskBrief, TaskBriefSource
from yuwang.domain.models import AgentPlan
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
            plan=plan.model_copy(update={"steps": ["确认目标", "生成说明"]}),
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
    assert {1, 2, 3, 4}.issubset(versions)
