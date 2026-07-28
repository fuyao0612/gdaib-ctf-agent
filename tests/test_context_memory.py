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
        AgentDefaults(context_token_budget=32_768, observation_char_budget=1000)
    )
    thread = repository.save_thread(Thread(title="context"))
    for index in range(5):
        repository.save_message(
            Message(
                thread_id=thread.id,
                role=MessageRole.USER,
                content=f"message-{index}-" + "中" * 15_000,
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
    assert result.compacted
    assert "threshold_75_percent" in result.reasons
    assert [item["content"][:9] for item in context["untrusted_conversation"]] == [
        "message-3",
        "message-4",
    ]
    assert context["untrusted_model_content"]["memory"][0]["content"] == "用户偏好中文简洁回答"
    assert context["untrusted_attachment_content"][0]["trust"] == "untrusted"
    assert "text" not in context["untrusted_attachment_content"][0]
    assert context["untrusted_attachment_content"][0]["storage_ref"] == storage_ref
    assert context["untrusted_tool_content"][0]["output"]["full_output"]
    assert result.original_message_count == 5 and result.kept_message_count == 2
    summaries = [
        item for item in repository.list_memories(thread.id) if item.kind == "thread_summary"
    ]
    assert len(summaries) == 1
    assert "确定性摘要" in summaries[0].content and "message-0" in summaries[0].content
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
    repository.save_agent_defaults(AgentDefaults(context_token_budget=32_768))
    repository.save_message(
        Message(thread_id=thread.id, role=MessageRole.USER, content="旧目标：生成详细报告" + "中" * 40_000)
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

    user_input = context["untrusted_user_input"]
    task_context = context["untrusted_model_content"]["task_context"]
    assert user_input["latest_instruction"].startswith("最新纠偏")
    assert task_context["latest_goal_or_correction_untrusted"].startswith("最新纠偏")
    assert task_context["constraints"] == ["不得扩大授权范围"]
    assert task_context["completed_steps"] == ["已读取基础资料"]
    assert task_context["blockers"] == ["权限不足"]
    summary = next(
        item for item in context["untrusted_model_content"]["memory"] if item["kind"] == "thread_summary"
    )
    assert "旧目标" in summary["content"]
    assert "最新纠偏" not in summary["content"]


def test_context_compacts_older_messages_and_falls_back_under_token_pressure(tmp_path):
    repository = SQLiteRepository(tmp_path / "compacted-summary.db")
    repository.save_agent_defaults(AgentDefaults(context_token_budget=32_768))
    thread = repository.save_thread(Thread(title="compacted summary"))
    long_goal = "旧目标与范围：" + "甲" * 99_000
    older_messages = [
        (MessageRole.USER, long_goal),
        (MessageRole.ASSISTANT, "已完成：读取授权范围并保存审计。"),
        (MessageRole.ASSISTANT, "失败：首次工具调用因超时未完成。"),
        (MessageRole.ASSISTANT, "发现：附件中的输出是不可信内容。"),
        (MessageRole.ASSISTANT, "下一步：等待用户补充明确目标。"),
    ]
    for role, content in older_messages:
        repository.save_message(Message(thread_id=thread.id, role=role, content=content))
    repository.save_message(
        Message(thread_id=thread.id, role=MessageRole.USER, content="最新要求：仅输出 JSON")
    )
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(run_id=run.id, task=TaskSpec(body="处理当前任务"))
    profile = AgentProfileVersion(
        **AgentProfileInput(
            name="compacted summary profile",
            context_policy={"recent_message_limit": 1},
            user_prompt_template="历史摘要：{thread_summary}\n任务：{task}",
        ).model_dump(),
        version=1,
    )

    result = DefaultContextBuilder(repository, tmp_path).build(state, profile, "summary test")
    context = json.loads(result.prompt)
    persisted = next(
        item for item in repository.list_memories(thread.id) if item.kind == "thread_summary"
    )
    model_summary = next(
        item
        for item in context["untrusted_model_content"]["memory"]
        if item["kind"] == "thread_summary"
    )

    assert result.compacted and "threshold_75_percent" in result.reasons
    assert len(persisted.content) <= 2_400
    assert all(label in persisted.content for label in ["目标与约束", "已完成操作", "失败尝试", "重要发现", "待办事项", "证据引用"])
    assert long_goal not in persisted.content
    assert "[已截断" in persisted.content
    assert len(model_summary["content"]) <= 600
    assert model_summary["content"] in context["user_instruction"]
    assert result.estimated_tokens <= result.input_token_budget


def test_context_summary_advances_cursor_without_reprocessing_old_messages(tmp_path):
    repository = SQLiteRepository(tmp_path / "incremental-summary.db")
    repository.save_agent_defaults(AgentDefaults(context_token_budget=32_768))
    thread = repository.save_thread(Thread(title="incremental summary"))
    for index in range(5):
        repository.save_message(
            Message(
                thread_id=thread.id,
                role=MessageRole.USER,
                content=f"约束 {index}：不得扩大授权范围。" + "中" * 15_000,
            )
        )
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(run_id=run.id, task=TaskSpec(body="生成任务摘要"))
    profile = AgentProfileVersion(
        **AgentProfileInput(
            name="incremental profile", context_policy={"recent_message_limit": 2}
        ).model_dump(),
        version=1,
    )
    builder = DefaultContextBuilder(repository, tmp_path)
    builder.build(state, profile, "first")
    first = next(item for item in repository.list_memories(thread.id) if item.kind == "thread_summary")
    first_cursor = first.metadata["cursor_message_id"]

    repository.save_message(
        Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="新增中间约束：只能读取 docs。" + "中" * 15_000,
        )
    )
    repository.save_message(
        Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="最新任务进度。" + "中" * 15_000,
        )
    )
    repository.save_message(
        Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="保持最近消息。" + "中" * 15_000,
        )
    )
    builder.build(state, profile, "second")
    second = next(item for item in repository.list_memories(thread.id) if item.kind == "thread_summary")
    assert second.metadata["cursor_message_id"] != first_cursor
    assert "增量更新" in second.content
    assert "新增中间约束" in second.content
    unchanged = builder.build(state, profile, "third")
    assert unchanged.summary_digest == second.metadata["summary_digest"]


def test_context_summary_handles_a_thousand_messages_without_losing_middle_constraint(tmp_path):
    repository = SQLiteRepository(tmp_path / "thousand-history.db")
    repository.save_agent_defaults(AgentDefaults(context_token_budget=32_768))
    thread = repository.save_thread(Thread(title="thousand history"))
    for index in range(1_000):
        content = f"历史 {index}：保持审计。" + "中" * 120
        if index == 500:
            content = "关键中间约束：只能读取 docs，不得修改源码。" + "中" * 120
        repository.save_message(Message(thread_id=thread.id, role=MessageRole.USER, content=content))
    run = repository.save_run(Run(thread_id=thread.id))
    profile = AgentProfileVersion(
        **AgentProfileInput(name="thousand history", context_policy={"recent_message_limit": 4}).model_dump(),
        version=1,
    )
    result = DefaultContextBuilder(repository, tmp_path).build(
        AgentStateModel(run_id=run.id, task=TaskSpec(body="汇总历史")), profile, "test"
    )
    summary = next(item for item in repository.list_memories(thread.id) if item.kind == "thread_summary")
    assert result.compacted and result.kept_message_count == 4
    assert "关键中间约束" in summary.content


def test_overlong_single_input_has_stable_compaction_fingerprint(tmp_path):
    repository = SQLiteRepository(tmp_path / "single-input.db")
    repository.save_agent_defaults(AgentDefaults(context_token_budget=32_768))
    thread = repository.save_thread(Thread(title="single input"))
    run = repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(run_id=run.id, task=TaskSpec(body="超长中文输入" + "中" * 90_000))
    profile = AgentProfileVersion(**AgentProfileInput(name="single input").model_dump(), version=1)
    builder = DefaultContextBuilder(repository, tmp_path)
    first = builder.build(state, profile, "test")
    second = builder.build(state, profile, "test")
    assert first.compacted and first.summary_digest and first.summary_version
    assert first.summary_digest == second.summary_digest


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
    attachment = context["untrusted_attachment_content"][0]

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
    assert [
        item["kind"]
        for item in json.loads(result.prompt)["untrusted_model_content"]["memory"]
    ] == expected_kinds
