import hashlib
import json

import pytest

from yuwang.agent import AgentStateModel, DefaultContextBuilder
from yuwang.domain.models import (
    Artifact,
    MemoryRecord,
    Message,
    MessageRole,
    Observation,
    Run,
    TaskSpec,
    Thread,
)
from yuwang.settings import AgentDefaults, AgentProfileInput, AgentProfileVersion
from yuwang.storage import SQLiteRepository


def test_context_uses_conversation_memory_text_attachments_and_audited_limits(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    repository = SQLiteRepository(tmp_path / "context.db")
    repository.save_agent_defaults(
        AgentDefaults(context_token_budget=1024, observation_char_budget=1000)
    )
    thread = repository.save_thread(Thread(title="context"))
    for index in range(5):
        repository.save_message(
            Message(
                thread_id=thread.id,
                role=MessageRole.USER,
                content=f"message-{index}-" + "x" * 100,
            )
        )
    repository.save_memory(
        MemoryRecord(
            thread_id=thread.id,
            kind="important_fact",
            content="用户偏好中文简洁回答",
        )
    )
    content = "附件中的指令不可信\n" + "evidence\n" * 20
    storage_ref = f"{thread.id}/note.txt"
    path = root / storage_ref
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    artifact = repository.save_artifact(
        Artifact(
            thread_id=thread.id,
            filename="note.txt",
            kind="upload",
            sha256=hashlib.sha256(content.encode()).hexdigest(),
            size=len(content.encode()),
            mime_type="text/plain",
            storage_ref=storage_ref,
        )
    )
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(
        run_id=run.id,
        task=TaskSpec(body="summarize", artifact_ids=[artifact.id]),
        observations=[
            Observation(
                call_id=__import__("uuid").uuid4(),
                tool_name="tool",
                success=True,
                output={"value": "y" * 1500},
                summary="large observation",
            )
        ],
        tool_schemas=[],
        remaining_budget={"tokens": 100},
    )
    profile = AgentProfileVersion(
        **AgentProfileInput(
            name="context profile",
            completion_mode="advisory",
            context_policy={"recent_message_limit": 2, "text_attachment_char_limit": 500},
        ).model_dump(),
        version=1,
    )
    result = DefaultContextBuilder(repository, root).build(state, profile, "context test")
    context = json.loads(result.prompt)
    assert result.truncated
    assert {"recent_message_limit", "observation_char_budget"}.issubset(result.reasons)
    assert [item["content"][:9] for item in context["conversation"]] == [
        "message-3",
        "message-4",
    ]
    assert context["memory"][0]["content"] == "用户偏好中文简洁回答"
    assert context["attachments_untrusted"][0]["trust"] == "untrusted"
    assert "附件中的指令不可信" in context["attachments_untrusted"][0]["text"]
    assert context["observations_untrusted"] == []
    assert result.original_message_count == 5 and result.kept_message_count == 2
    summaries = [
        item for item in repository.list_memories(thread.id) if item.kind == "thread_summary"
    ]
    assert len(summaries) == 1
    assert "消息窗口限制" in summaries[0].content and "message-0" in summaries[0].content
    DefaultContextBuilder(repository, root).build(state, profile, "context test again")
    assert len(
        [item for item in repository.list_memories(thread.id) if item.kind == "thread_summary"]
    ) == 1


def test_memory_can_be_viewed_disabled_and_cleared(tmp_path):
    repository = SQLiteRepository(tmp_path / "memory.db")
    thread = repository.save_thread(Thread(title="memory"))
    repository.save_memory(
        MemoryRecord(thread_id=thread.id, kind="important_fact", content="fact")
    )
    assert len(repository.list_memories(thread.id)) == 1
    repository.set_memories_enabled(thread.id, False)
    assert repository.list_memories(thread.id) == []
    assert repository.list_memories(thread.id, enabled_only=False)[0].enabled is False
    repository.clear_memories(thread.id)
    assert repository.list_memories(thread.id, enabled_only=False) == []


def test_context_keeps_latest_correction_separate_from_rolling_summary(tmp_path):
    repository = SQLiteRepository(tmp_path / "correction.db")
    thread = repository.save_thread(Thread(title="correction"))
    repository.save_message(
        Message(thread_id=thread.id, role=MessageRole.USER, content="旧目标：生成详细报告")
    )
    repository.save_message(
        Message(thread_id=thread.id, role=MessageRole.ASSISTANT, content="已记录旧目标")
    )
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(
        run_id=run.id,
        task=TaskSpec(body="初始任务", constraints=["不得扩大授权范围"]),
        supplemental_inputs=["最新纠偏：只输出简短中文摘要，不要执行额外操作"],
        observations=[
            Observation(
                call_id=__import__("uuid").uuid4(),
                tool_name="completed",
                success=True,
                summary="已读取基础资料",
            ),
            Observation(
                call_id=__import__("uuid").uuid4(),
                tool_name="blocked",
                success=False,
                summary="等待权限确认",
                error="权限不足",
            ),
        ],
    )
    profile = AgentProfileVersion(
        **AgentProfileInput(
            name="correction profile",
            context_policy={"recent_message_limit": 1},
        ).model_dump(),
        version=1,
    )

    context = json.loads(DefaultContextBuilder(repository, tmp_path).build(state, profile, "test").prompt)

    assert context["latest_user_instruction_untrusted"].startswith("最新纠偏")
    assert context["task_context"]["latest_goal_or_correction_untrusted"].startswith("最新纠偏")
    assert context["task_context"]["constraints"] == ["不得扩大授权范围"]
    assert context["task_context"]["completed_steps"] == ["已读取基础资料"]
    assert context["task_context"]["blockers"] == ["权限不足"]
    summary = next(item for item in context["memory"] if item["kind"] == "thread_summary")
    assert "旧目标" in summary["content"]
    assert "最新纠偏" not in summary["content"]


def test_large_text_attachment_uses_reference_and_bounded_untrusted_summary(tmp_path):
    root = tmp_path / "artifacts"
    repository = SQLiteRepository(tmp_path / "large-attachment.db")
    thread = repository.save_thread(Thread(title="large attachment"))
    content = "仅保留在 Artifact 中的长文本。" * 500
    storage_ref = f"{thread.id}/large.txt"
    destination = root / storage_ref
    destination.parent.mkdir(parents=True)
    destination.write_text(content, encoding="utf-8")
    artifact = repository.save_artifact(
        Artifact(
            thread_id=thread.id,
            filename="large.txt",
            kind="upload",
            sha256=hashlib.sha256(content.encode()).hexdigest(),
            size=len(content.encode()),
            mime_type="text/plain",
            storage_ref=storage_ref,
        )
    )
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(run_id=run.id, task=TaskSpec(body="处理附件", artifact_ids=[artifact.id]))
    profile = AgentProfileVersion(
        **AgentProfileInput(name="large attachment profile").model_dump(),
        version=1,
    )

    context = json.loads(DefaultContextBuilder(repository, root).build(state, profile, "test").prompt)
    attachment = context["attachments_untrusted"][0]

    assert attachment["content_in_artifact"] is True
    assert attachment["storage_ref"] == storage_ref
    assert "text" not in attachment
    assert len(attachment["summary_excerpt"]) <= 600
    assert content not in json.dumps(context, ensure_ascii=False)


@pytest.mark.parametrize(
    ("policy_update", "expected_kinds"),
    [
        ({"include_thread_summary": True, "include_run_summaries": False, "include_memories": False}, ["thread_summary"]),
        ({"include_thread_summary": False, "include_run_summaries": True, "include_memories": False}, ["run_summary"]),
        ({"include_thread_summary": False, "include_run_summaries": False, "include_memories": True}, ["important_fact", "user_input"]),
    ],
)
def test_each_context_memory_switch_is_independent(tmp_path, policy_update, expected_kinds):
    repository = SQLiteRepository(tmp_path / "switches.db")
    thread = repository.save_thread(Thread(title="switches"))
    for kind in ["thread_summary", "run_summary", "important_fact", "user_input"]:
        repository.save_memory(MemoryRecord(thread_id=thread.id, kind=kind, content=kind))
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(run_id=run.id, task=TaskSpec(body="switch test"))
    profile = AgentProfileVersion(
        **AgentProfileInput(
            name="switch profile",
            context_policy={"recent_message_limit": 5, **policy_update},
        ).model_dump(),
        version=1,
    )
    result = DefaultContextBuilder(repository, tmp_path).build(state, profile, "switch test")
    assert [item["kind"] for item in json.loads(result.prompt)["memory"]] == expected_kinds
