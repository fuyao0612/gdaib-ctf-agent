from uuid import uuid4

import pytest

from yuwang.chat import build_chat_messages, local_thread_title
from yuwang.domain.models import Message, MessageRole, Thread
from yuwang.storage import SQLiteRepository


def test_chat_context_preserves_order_and_prunes_whole_messages() -> None:
    thread_id = uuid4()
    messages = [
        Message(thread_id=thread_id, role=MessageRole.USER, content="旧问题" * 20),
        Message(thread_id=thread_id, role=MessageRole.ASSISTANT, content="旧回答" * 20),
        Message(thread_id=thread_id, role=MessageRole.USER, content="最新问题"),
    ]
    selected = build_chat_messages(messages, recent_limit=3, token_limit=10)
    assert selected == [{"role": "user", "content": "最新问题"}]
    assert local_thread_title("  很长的\n新对话标题  ", limit=8) == "很长的 新对话标…"


def test_chat_request_is_exclusive_idempotent_and_restart_retryable(tmp_path) -> None:
    path = tmp_path / "chat.db"
    repository = SQLiteRepository(path)
    thread = repository.save_thread(Thread(title="chat"))
    request_id = uuid4()
    user, completed = repository.begin_chat_request(
        thread.id, request_id, "你好", [], False
    )
    assert completed is None
    with pytest.raises(ValueError, match="仍有回复"):
        repository.begin_chat_request(thread.id, uuid4(), "并发消息", [], False)

    reopened = SQLiteRepository(path)
    retried, completed = reopened.begin_chat_request(
        thread.id, request_id, "你好", [], True
    )
    assert retried.id == user.id and completed is None
    assistant = reopened.complete_chat_request(request_id, thread.id, "你好呀")
    duplicate_user, duplicate_assistant = reopened.begin_chat_request(
        thread.id, request_id, "你好", [], True
    )
    assert duplicate_user.id == user.id
    assert duplicate_assistant and duplicate_assistant.id == assistant.id
    assert [item.role for item in reopened.list_messages(thread.id)] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
