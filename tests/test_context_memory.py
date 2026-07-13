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
