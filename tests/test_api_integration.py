import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from apps.api.schemas import MessageCreate
from tests.fakes import FakeEchoTool
from yuwang.agent import AgentStateModel
from yuwang.domain.models import AgentAction, AgentPlan, Observation, Run, RunStatus, TaskSpec


def wait_for_terminal(client, run_id):
    for _ in range(150):
        run = client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


@pytest.fixture
def provider_server():
    chat_payloads = []
    chat_attempts = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            encoded = json.dumps({"data": [{"id": "test-model"}, {"id": "test-model-alt"}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(length))
            prompt = body["messages"][-1]["content"]
            if "response_format" not in body:
                chat_payloads.append(body)
                chat_attempts[prompt] = chat_attempts.get(prompt, 0) + 1
                if prompt == "先失败再重试" and chat_attempts[prompt] <= 2:
                    self.send_response(503)
                    self.end_headers()
                    return
                user_history = [
                    item["content"] for item in body["messages"] if item["role"] == "user"
                ]
                response_content = (
                    "你好，我是御网智元。"
                    if len(user_history) == 1
                    else f"我记得你先说了：{user_history[0]}"
                )
            elif '"status":"ok"' in prompt:
                response_content = '{"status":"ok"}'
            else:
                context = json.loads(prompt)
                if "slow-control" in context.get("untrusted_task", ""):
                    time.sleep(0.12)
                if "生成公开 Task Brief" in context["purpose"]:
                    needs_clarification = (
                        "需要澄清任务" in context["untrusted_task"]
                        and not context.get("supplemental_inputs")
                    )
                    response_content = json.dumps(
                        {
                            "goal": "完成协议集成任务",
                            "authorized_scope": context.get("authorized_targets", []),
                            "constraints": context.get("constraints", []),
                            "success_criteria": context.get("success_conditions", []),
                            "expected_output": "可审核结果",
                            "known_information": ["原始要求已保存"],
                            "assumptions": [],
                            "risks": ["不得扩大授权范围"],
                            "needs_clarification": needs_clarification,
                            "clarification_questions": (
                                ["请补充目标受众"] if needs_clarification else []
                            ),
                        }
                    )
                elif "生成动态计划" in context["purpose"] or "重新规划" in context["purpose"]:
                    response_content = json.dumps(
                        {
                            "summary": "协议服务生成的计划",
                            "steps": ["调用测试工具", "验证候选"],
                            "success_approach": "使用确定性规则验证工具证据",
                        }
                    )
                else:
                    observations = context.get("observations_untrusted", context.get("observations", []))
                    if "需要补充" in context["untrusted_task"]:
                        if not context.get("supplemental_inputs"):
                            action = {
                                "kind": "request_input",
                                "summary": "请补充目标受众",
                            }
                        else:
                            action = {
                                "kind": "finish",
                                "summary": "已生成建议",
                                "answer": "建议面向技术团队分阶段实施",
                            }
                    elif observations and observations[-1]["success"]:
                        latest = observations[-1]
                        action = {
                            "kind": "finish",
                            "summary": "提交带来源候选",
                            "candidate": {
                                "value": latest["output"]["echoed"],
                                "source_call_id": latest["call_id"],
                                "location": "/echoed",
                            },
                            "tool_input": {},
                        }
                    else:
                        action = {
                            "kind": "call_tool",
                            "summary": "协议服务选择测试工具",
                            "tool_name": "test_echo",
                            "tool_input": {"text": "verified", "fail": not observations},
                        }
                    response_content = json.dumps(action)
            encoded = json.dumps(
                {
                    "choices": [{"message": {"content": response_content}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    class ProviderURL(str):
        pass

    url = ProviderURL(f"http://127.0.0.1:{server.server_port}/v1")
    url.chat_payloads = chat_payloads
    yield url
    server.shutdown()
    thread.join(timeout=2)


def configured_app(tmp_path):
    return create_app(
        Settings(
            database_path=tmp_path / "api.db",
            artifact_root=tmp_path / "artifacts",
            admin_token="test-admin-token",
            master_key=Fernet.generate_key().decode(),
            allow_insecure_local_provider=True,
        )
    )


def create_provider(client: TestClient, base_url: str) -> dict:
    response = client.post(
        "/api/v1/admin/settings/providers",
        headers={"Authorization": "Bearer test-admin-token"},
        json={
            "name": "本地协议服务",
            "preset": "custom",
            "base_url": base_url,
            "model": "test-model",
            "api_key": "test-api-key-value",
            "enabled": True,
            "is_default": True,
            "fallback_order": 0,
            "timeout_seconds": 5,
            "max_retries": 0,
            "structured_mode": "json_schema",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "api_key" not in body and "encrypted_api_key" not in body
    assert body["has_api_key"] is True
    tested = client.post(
        f"/api/v1/admin/settings/providers/{body['id']}/test",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["usage_reported"] is True
    discovered = client.get(
        f"/api/v1/admin/settings/providers/{body['id']}/models",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert discovered.status_code == 200
    assert discovered.json()["models"] == ["test-model", "test-model-alt"]
    return body


def test_full_api_persistence_upload_sse_and_report(tmp_path, provider_server):
    app = configured_app(tmp_path)
    app.state.registry.register(FakeEchoTool())
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        assert client.get("/api/v1/health").json()["version"] == "0.5.0"
        provider = create_provider(client, provider_server)
        thread = client.post("/api/v1/threads", json={"title": "集成任务", "mode": "normal"}).json()
        uploaded = client.post(
            f"/api/v1/threads/{thread['id']}/artifacts",
            files={"upload": ("sample.txt", b"evidence", "text/plain")},
        )
        artifact = uploaded.json()
        started = client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={
                "content": "执行协议集成任务",
                "artifact_ids": [artifact["id"]],
                "provider_config_id": provider["id"],
                "verification_rules": [{"kind": "regex", "value": "verified"}],
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        assert wait_for_terminal(client, run_id)["status"] == "completed"
        profile_snapshot = app.state.repository.get_run_agent_profile(__import__("uuid").UUID(run_id))
        assert profile_snapshot
        assert profile_snapshot.version == thread["agent_profile_version"]
        events = client.get(f"/api/v1/runs/{run_id}/events").json()
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        resumed = client.get(f"/api/v1/runs/{run_id}/events", params={"after": 2}).json()
        assert resumed[0]["sequence"] == 3
        report = client.get(f"/api/v1/runs/{run_id}/report")
        assert report.status_code == 200
        assert "调整：协议服务生成的计划" in report.json()["markdown"]
        audit = client.get(f"/api/v1/runs/{run_id}/audit").json()
        assert audit["model_calls"] and audit["tool_calls"] and audit["evidence"]
        assert audit["profile"]["planning_strategy"] == "dynamic"
        assert audit["profile"]["workflow_preset"] == "verified"
        assert audit["profile"]["context_policy"]["include_memories"] is True
        assert [item["checkpoint_sequence"] for item in audit["checkpoints"]] == list(
            range(1, len(audit["checkpoints"]) + 1)
        )
        assert client.get(f"/api/v1/artifacts/{artifact['id']}/download").content == b"evidence"
        assert len(client.get("/api/v1/providers").json()) == 1
        assert len(client.get("/api/v1/tools").json()) == 3
    with TestClient(app) as reopened:
        reopened.headers.update({"Authorization": "Bearer test-admin-token"})
        detail = reopened.get(f"/api/v1/threads/{thread['id']}").json()
        assert detail["messages"] and detail["runs"] and detail["artifacts"]
        assert detail["messages"][-1]["role"] == "assistant"


def test_plain_chat_is_natural_persistent_and_does_not_create_run(
    tmp_path, provider_server
):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        provider = create_provider(client, provider_server)
        thread = client.post("/api/v1/threads", json={"title": "新对话"}).json()
        assert thread["interaction_mode"] == "chat"

        first_request = str(uuid4())
        first = client.post(
            f"/api/v1/threads/{thread['id']}/chat",
            json={
                "request_id": first_request,
                "content": "你好",
                "provider_config_id": provider["id"],
            },
        )
        assert first.status_code == 200
        assert "event: reply_start" in first.text
        assert "event: text_delta" in first.text
        assert "你好，我是御网智元。" in first.text
        assert "event: reply_complete" in first.text

        second = client.post(
            f"/api/v1/threads/{thread['id']}/chat",
            json={
                "request_id": str(uuid4()),
                "content": "我刚才说了什么？",
                "provider_config_id": provider["id"],
            },
        )
        assert "我记得你先说了：你好" in second.text
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert detail["runs"] == []
        assert [item["role"] for item in detail["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert detail["title"] == "你好"
        assert all("response_format" not in item for item in provider_server.chat_payloads)
        assert provider_server.chat_payloads[0]["stream"] is True
        assert [
            item["content"]
            for item in provider_server.chat_payloads[1]["messages"]
            if item["role"] == "user"
        ] == ["你好", "我刚才说了什么？"]

        defaults = client.get("/api/v1/admin/settings/chat").json()
        saved_defaults = client.put(
            "/api/v1/admin/settings/chat",
            json={
                **defaults,
                "default_provider_id": provider["id"],
                "stream_enabled": False,
                "sidebar_expanded": False,
            },
        )
        assert saved_defaults.status_code == 200
        uploaded = client.post(
            f"/api/v1/threads/{thread['id']}/artifacts",
            files={"upload": ("notes.txt", "附件里的真实文本".encode(), "text/plain")},
        ).json()
        attached = client.post(
            f"/api/v1/threads/{thread['id']}/chat",
            json={
                "request_id": str(uuid4()),
                "content": "解释附件",
                "artifact_ids": [uploaded["id"]],
            },
        )
        assert "event: reply_complete" in attached.text
        assert provider_server.chat_payloads[-1]["stream"] is False
        assert "[不可信附件：notes.txt]" in provider_server.chat_payloads[-1]["messages"][-1][
            "content"
        ]
        assert "附件里的真实文本" in provider_server.chat_payloads[-1]["messages"][-1][
            "content"
        ]

    with TestClient(app) as reopened:
        reopened.headers.update({"Authorization": "Bearer test-admin-token"})
        restored = reopened.get(f"/api/v1/threads/{thread['id']}").json()
        assert len(restored["messages"]) == 6
        assert restored["runs"] == []


def test_unified_message_entry_chooses_free_text_or_controlled_run(
    tmp_path, provider_server
):
    """浏览器只发送消息，服务端才决定是否进入受控执行路径。"""

    app = configured_app(tmp_path)
    app.state.registry.register(FakeEchoTool())
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        create_provider(client, provider_server)
        thread = client.post("/api/v1/threads", json={"title": "统一入口"}).json()

        greeting = client.post(
            f"/api/v1/threads/{thread['id']}/message",
            json={"request_id": str(uuid4()), "content": "你好"},
        )
        assert greeting.status_code == 200
        assert "event: reply_complete" in greeting.text
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert detail["runs"] == []
        assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]
        assert "response_format" not in provider_server.chat_payloads[-1]

        task = client.post(
            f"/api/v1/threads/{thread['id']}/message",
            json={
                "request_id": str(uuid4()),
                "content": "完成这道授权 CTF 题，并验证并报告结果",
            },
        )
        assert task.status_code == 200
        assert "event: execution_started" in task.text
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert len(detail["runs"]) == 1
        assert detail["messages"][-1]["role"] == "user"
        task_spec = app.state.repository.get_run_task(UUID(detail["runs"][0]["id"]))
        assert task_spec and task_spec.verification_rules == []


def test_unified_attachment_analysis_starts_one_task_with_artifact_reference(
    tmp_path, provider_server
):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        create_provider(client, provider_server)
        thread = client.post("/api/v1/threads", json={"title": "附件分析"}).json()
        artifact = client.post(
            f"/api/v1/threads/{thread['id']}/artifacts",
            files={"upload": ("notes.txt", "仅作不可信上下文".encode(), "text/plain")},
        ).json()
        payload = {
            "request_id": str(uuid4()),
            "content": "分析这个文件并给出结果",
            "artifact_ids": [artifact["id"]],
        }

        response = client.post(f"/api/v1/threads/{thread['id']}/message", json=payload)

        assert response.status_code == 200
        assert "event: execution_started" in response.text
        run_id = client.get(f"/api/v1/threads/{thread['id']}").json()["runs"][0]["id"]
        task = app.state.repository.get_run_task(UUID(run_id))
        assert task and [str(value) for value in task.artifact_ids] == [artifact["id"]]
        assert task.origin_message_id == UUID(payload["request_id"])


def test_unified_run_keeps_its_own_origin_when_a_later_message_is_saved(
    tmp_path, provider_server, monkeypatch
):
    """统一入口不能因为并发/重入而把 TaskSpec 绑定到线程的后一条消息。"""

    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        create_provider(client, provider_server)
        thread = client.post("/api/v1/threads", json={"title": "来源绑定"}).json()
        context = app.state.context
        original_start_run = context.start_run

        async def save_later_message(thread_id, body, *, origin_message=None):
            context.save_user_message(
                thread_id,
                MessageCreate(content="这条较晚消息不能成为本次任务来源"),
            )
            return await original_start_run(
                thread_id,
                body,
                origin_message=origin_message,
            )

        monkeypatch.setattr(context, "start_run", save_later_message)
        payload = {
            "request_id": str(uuid4()),
            "content": "完成这道授权 CTF 题并验证结果",
        }
        response = client.post(f"/api/v1/threads/{thread['id']}/message", json=payload)

        assert response.status_code == 200, response.text
        run_id = client.get(f"/api/v1/threads/{thread['id']}").json()["runs"][0]["id"]
        task = app.state.repository.get_run_task(UUID(run_id))
        assert task is not None
        assert task.body == payload["content"]
        assert task.origin_message_id == UUID(payload["request_id"])


def test_chat_failure_retry_is_idempotent_and_never_saves_partial_reply(
    tmp_path, provider_server
):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        provider = create_provider(client, provider_server)
        thread = client.post("/api/v1/threads", json={"title": "新对话"}).json()
        request_id = str(uuid4())
        payload = {
            "request_id": request_id,
            "content": "先失败再重试",
            "provider_config_id": provider["id"],
        }

        failed = client.post(f"/api/v1/threads/{thread['id']}/chat", json=payload)
        assert "event: reply_failed" in failed.text
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert [item["role"] for item in detail["messages"]] == ["user"]

        retried = client.post(
            f"/api/v1/threads/{thread['id']}/chat",
            json={**payload, "retry": True},
        )
        assert "event: reply_complete" in retried.text
        duplicate = client.post(
            f"/api/v1/threads/{thread['id']}/chat",
            json={**payload, "retry": True},
        )
        assert "event: reply_complete" in duplicate.text
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]


def test_unconfigured_provider_and_admin_auth_are_explicit(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/admin/settings/providers").status_code == 401
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        thread = client.post("/api/v1/threads", json={"title": "needs model"}).json()
        client.post(f"/api/v1/threads/{thread['id']}/messages", json={"content": "task"})
        response = client.post(f"/api/v1/threads/{thread['id']}/runs", json={})
        assert response.status_code == 409
        assert "需要配置模型" in response.json()["error"]["message"]
        validation = client.post(
            "/api/v1/admin/settings/providers",
            headers={"Authorization": "Bearer test-admin-token"},
            json={
                "name": "bad",
                "preset": "custom",
                "base_url": "https://provider.example/v1",
                "model": "x",
                "api_key": "leak123",
            },
        )
        assert validation.status_code == 422
        assert "leak123" not in validation.text


def test_admin_cookie_session_requires_csrf_for_mutations(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/threads").status_code == 401
        assert client.post("/api/v1/admin/session", json={"token": "wrong"}).status_code == 401

        login = client.post(
            "/api/v1/admin/session", json={"token": "test-admin-token"}
        )
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]
        csrf = login.json()["csrf_token"]

        session = client.get("/api/v1/admin/session")
        assert session.json()["authenticated"] is True
        assert session.json()["csrf_token"] == csrf
        assert client.get("/api/v1/admin/settings/providers").status_code == 200
        created = client.post(
            "/api/v1/threads",
            headers={"X-CSRF-Token": csrf},
            json={"title": "待管理对话"},
        )
        thread_id = created.json()["id"]
        renamed = client.patch(
            f"/api/v1/threads/{thread_id}",
            headers={"X-CSRF-Token": csrf},
            json={"title": "已重命名", "archived": True},
        )
        assert renamed.json()["title"] == "已重命名"
        assert renamed.json()["archived"] is True
        assert client.delete(
            f"/api/v1/threads/{thread_id}", headers={"X-CSRF-Token": csrf}
        ).status_code == 204
        assert client.delete("/api/v1/admin/session").status_code == 403

        logout = client.delete(
            "/api/v1/admin/session", headers={"X-CSRF-Token": csrf}
        )
        assert logout.status_code == 204
        assert client.get("/api/v1/admin/settings/providers").status_code == 401


def test_health_readiness_and_setup_status_are_distinct(tmp_path, provider_server):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        status = client.get("/api/v1/setup/status")
        assert status.status_code == 200
        assert status.json()["checks"] == {
            "database": True,
            "master_key": True,
            "admin": True,
            "provider": False,
            "agent": False,
        }
        assert client.get("/api/v1/readiness").status_code == 503

        create_provider(client, provider_server)
        assert client.get("/api/v1/setup/status").json()["configured"] is True
        assert client.get("/api/v1/readiness").json()["status"] == "ready"


def test_agent_profile_api_versions_preview_export_and_thread_snapshot(tmp_path):
    app = configured_app(tmp_path)
    headers = {"Authorization": "Bearer test-admin-token"}
    with TestClient(app) as client:
        client.headers.update(headers)
        defaults = client.get("/api/v1/admin/settings/agent-profiles", headers=headers)
        assert defaults.status_code == 200 and defaults.json()[0]["is_default"]
        created = client.post(
            "/api/v1/admin/settings/agent-profiles",
            headers=headers,
            json={
                "name": "API 配置",
                "description": "第一版",
                "user_prompt_template": "任务：{task}",
                "completion_mode": "advisory",
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()
        thread = client.post(
            "/api/v1/threads",
            json={"title": "profile thread", "agent_profile_id": profile["profile_id"]},
        ).json()
        assert thread["agent_profile_version"] == 1

        edited = {key: value for key, value in profile.items() if key not in {"profile_id", "version", "schema_version", "created_at"}}
        edited["description"] = "第二版"
        updated = client.put(
            f"/api/v1/admin/settings/agent-profiles/{profile['profile_id']}",
            headers=headers,
            json=edited,
        )
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert client.get(f"/api/v1/threads/{thread['id']}").json()["agent_profile_version"] == 1

        preview = client.post(
            "/api/v1/admin/settings/agent-profiles/template-preview",
            headers=headers,
            json={"template": "{task}", "values": {"task": "预览"}},
        )
        assert preview.json() == {"rendered": "预览"}
        exported = client.get(
            "/api/v1/admin/settings/agent-profiles/export",
            headers=headers,
            params={"profile_id": profile["profile_id"]},
        )
        assert exported.status_code == 200
        exported_profile = exported.json()["profiles"][0]
        assert exported_profile["default_provider_id"] is None
        assert exported_profile["fallback_provider_ids"] == []
        assert "api_key" not in exported.text


def test_waiting_input_api_persists_memory_and_resumes(
    tmp_path, provider_server, monkeypatch
):
    app = configured_app(tmp_path)
    headers = {"Authorization": "Bearer test-admin-token"}
    with TestClient(app) as client:
        client.headers.update(headers)
        provider = create_provider(client, provider_server)
        profile_response = client.post(
            "/api/v1/admin/settings/agent-profiles",
            headers=headers,
            json={
                "name": "交互建议助手",
                "default_provider_id": provider["id"],
                "completion_mode": "advisory",
                "planning_strategy": "direct",
                "workflow": {"preset": "direct"},
                "context_policy": {
                    "recent_message_limit": 7,
                    "include_thread_summary": False,
                    "include_run_summaries": True,
                    "include_memories": False,
                    "text_attachment_char_limit": 1234,
                },
                "memory_policy": {
                    "enabled": True,
                    "persist_important_facts": False,
                    "max_facts": 3,
                },
                "intervention_policy": {
                    "normal_mode": "wait",
                    "competition_mode": "fail",
                    "max_requests": 1,
                },
                "validation_policy": {"require_external_evidence": False},
            },
        )
        assert profile_response.status_code == 201, profile_response.text
        profile = profile_response.json()
        thread = client.post(
            "/api/v1/threads",
            json={"title": "waiting", "agent_profile_id": profile["profile_id"]},
        ).json()
        client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            json={"content": "需要补充后给出方案"},
        )
        started = client.post(f"/api/v1/threads/{thread['id']}/runs", json={})
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        assert wait_for_terminal(client, run_id)["status"] == "waiting_input"
        input_payload = {
            "request_id": str(uuid4()),
            "content": "目标受众是技术团队",
        }
        original_schedule = app.state.context.schedule
        attempts = 0

        def fail_first_resume_schedule(run_id, coroutine):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("injected resume scheduling failure")
            original_schedule(run_id, coroutine)

        monkeypatch.setattr(app.state.context, "schedule", fail_first_resume_schedule)
        failed = client.post(f"/api/v1/threads/{thread['id']}/message", json=input_payload)
        assert failed.status_code == 503
        pending = client.get(f"/api/v1/runs/{run_id}").json()
        assert pending["status"] == "running"
        assert app.state.repository.latest_checkpoint(UUID(run_id)).node == "input_received"
        assert [
            item.content for item in app.state.repository.list_messages(UUID(thread["id"]))
        ].count(input_payload["content"]) == 1
        assert sum(
            event.type == "input_received"
            for event in app.state.repository.list_events(UUID(run_id))
        ) == 1
        monkeypatch.setattr(app.state.context, "schedule", original_schedule)
        supplied = client.post(
            f"/api/v1/threads/{thread['id']}/message", json=input_payload
        )
        assert supplied.status_code == 200, supplied.text
        assert "event: input_received" in supplied.text
        finished = wait_for_terminal(client, run_id)
        assert finished["status"] == "completed"
        assert finished["validation_status"] == "unverified"
        replayed = client.post(
            f"/api/v1/threads/{thread['id']}/message", json=input_payload
        )
        assert replayed.status_code == 200, replayed.text
        assert "event: input_received" in replayed.text
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert [item["content"] for item in detail["messages"]].count(
            input_payload["content"]
        ) == 1
        audit = client.get(f"/api/v1/runs/{run_id}/audit").json()
        assert audit["profile"]["planning_strategy"] == "direct"
        assert audit["profile"]["workflow_preset"] == "direct"
        assert audit["profile"]["context_policy"]["recent_message_limit"] == 7
        assert audit["profile"]["memory_policy"] == {
            "enabled": True,
            "persist_important_facts": False,
            "max_facts": 3,
        }
        assert audit["profile"]["intervention_policy"]["max_requests"] == 1
        assert not any(
            event["type"] == "plan_updated"
            for event in client.get(f"/api/v1/runs/{run_id}/events").json()
        )
        memories = client.get(f"/api/v1/threads/{thread['id']}/memories").json()
        assert any(item["kind"] == "user_input" for item in memories)
        assert any(item["kind"] == "run_summary" for item in memories)
        user_memory = next(item for item in memories if item["kind"] == "user_input")
        assert client.delete(
            f"/api/v1/threads/{thread['id']}/memories/{user_memory['id']}"
        ).status_code == 204
        assert client.patch(
            f"/api/v1/threads/{thread['id']}/memories", json={"enabled": False}
        ).status_code == 204
        assert client.delete(f"/api/v1/threads/{thread['id']}/memories").status_code == 204


def test_task_brief_clarification_persists_versions_and_resumes(
    tmp_path, provider_server, monkeypatch
):
    app = configured_app(tmp_path)
    app.state.registry.register(FakeEchoTool())
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        provider = create_provider(client, provider_server)
        thread = client.post("/api/v1/threads", json={"title": "clarify"}).json()
        run_id = client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={
                "content": "需要澄清任务：整理方案",
                "provider_config_id": provider["id"],
                "verification_rules": [{"kind": "regex", "value": "verified"}],
            },
        ).json()["id"]
        assert wait_for_terminal(client, run_id)["status"] == "waiting_clarification"
        control = client.get(f"/api/v1/runs/{run_id}/control").json()
        assert control["task_briefs"][0]["clarification_questions"] == ["请补充目标受众"]

        clarification_payload = {
            "request_id": str(uuid4()),
            "content": "目标受众是新成员",
        }
        original_schedule = app.state.context.schedule
        attempts = 0

        def fail_first_resume_schedule(run_id, coroutine):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("injected resume scheduling failure")
            original_schedule(run_id, coroutine)

        monkeypatch.setattr(app.state.context, "schedule", fail_first_resume_schedule)
        failed = client.post(
            f"/api/v1/threads/{thread['id']}/message",
            json=clarification_payload,
        )
        assert failed.status_code == 503
        pending = client.get(f"/api/v1/runs/{run_id}").json()
        assert pending["status"] == "running"
        assert app.state.repository.latest_checkpoint(UUID(run_id)).node == "clarification_received"
        assert [
            item.content for item in app.state.repository.list_messages(UUID(thread["id"]))
        ].count(clarification_payload["content"]) == 1
        assert sum(
            event.type == "clarification_received"
            for event in app.state.repository.list_events(UUID(run_id))
        ) == 1
        monkeypatch.setattr(app.state.context, "schedule", original_schedule)
        resumed = client.post(
            f"/api/v1/threads/{thread['id']}/message",
            json=clarification_payload,
        )
        assert resumed.status_code == 200, resumed.text
        assert "event: clarification_received" in resumed.text
        assert wait_for_terminal(client, run_id)["status"] == "completed"
        replayed = client.post(
            f"/api/v1/threads/{thread['id']}/message",
            json=clarification_payload,
        )
        assert replayed.status_code == 200, replayed.text
        assert "event: clarification_received" in replayed.text
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert [item["content"] for item in detail["messages"]].count(
            clarification_payload["content"]
        ) == 1
        control = client.get(f"/api/v1/runs/{run_id}/control").json()
        assert [item["version"] for item in control["task_briefs"]] == [1, 2]
        assert control["task_briefs"][1]["needs_clarification"] is False


def test_plan_edit_approve_and_duplicate_request_are_idempotent(tmp_path, provider_server):
    app = configured_app(tmp_path)
    app.state.registry.register(FakeEchoTool())
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        provider = create_provider(client, provider_server)
        thread = client.post(
            "/api/v1/threads", json={"title": "approval", "plan_mode": "approval"}
        ).json()
        run_id = client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={
                "content": "执行需确认的计划",
                "provider_config_id": provider["id"],
                "verification_rules": [{"kind": "regex", "value": "verified"}],
            },
        ).json()["id"]
        assert wait_for_terminal(client, run_id)["status"] == "waiting_approval"
        control = client.get(f"/api/v1/runs/{run_id}/control").json()
        edited_plan = {
            **control["plans"][0]["plan"],
            "steps": ["先核对授权范围", "调用测试工具", "验证候选"],
            "expected_results": ["范围有效", "获得结果", "候选通过验证"],
            "verification_methods": ["策略检查", "工具返回", "正则验证"],
        }
        edit_body = {
            "request_id": str(uuid4()),
            "expected_version": 1,
            "plan": edited_plan,
            "reason": "增加授权检查",
        }
        first_edit = client.put(f"/api/v1/runs/{run_id}/plan", json=edit_body)
        second_edit = client.put(f"/api/v1/runs/{run_id}/plan", json=edit_body)
        assert first_edit.status_code == second_edit.status_code == 200
        assert first_edit.json()["version"] == second_edit.json()["version"] == 2

        decision = {
            "request_id": str(uuid4()),
            "expected_version": 2,
            "reason": "范围和验证方式已确认",
        }
        approved = client.post(f"/api/v1/runs/{run_id}/plan/approve", json=decision)
        duplicate = client.post(f"/api/v1/runs/{run_id}/plan/approve", json=decision)
        assert approved.status_code == duplicate.status_code == 202
        assert wait_for_terminal(client, run_id)["status"] == "completed"
        events = client.get(f"/api/v1/runs/{run_id}/events").json()
        assert sum(item["type"] == "plan_edited" for item in events) == 1
        assert sum(item["type"] == "plan_approved" for item in events) == 1


def test_plan_rejection_creates_agent_replan_version(tmp_path, provider_server):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        provider = create_provider(client, provider_server)
        thread = client.post(
            "/api/v1/threads", json={"title": "reject", "plan_mode": "approval"}
        ).json()
        run_id = client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={"content": "仅生成建议", "provider_config_id": provider["id"]},
        ).json()["id"]
        assert wait_for_terminal(client, run_id)["status"] == "waiting_approval"
        rejected = client.post(
            f"/api/v1/runs/{run_id}/plan/reject",
            json={
                "request_id": str(uuid4()),
                "expected_version": 1,
                "reason": "请增加回滚步骤",
            },
        )
        assert rejected.status_code == 202, rejected.text
        assert wait_for_terminal(client, run_id)["status"] == "waiting_approval"
        control = client.get(f"/api/v1/runs/{run_id}/control").json()
        assert [item["version"] for item in control["plans"]] == [1, 2]
        assert control["plans"][1]["source"] == "agent_replan"


def test_waiting_plan_can_be_stopped_without_background_task(tmp_path, provider_server):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        provider = create_provider(client, provider_server)
        thread = client.post(
            "/api/v1/threads", json={"title": "stop wait", "plan_mode": "approval"}
        ).json()
        run_id = client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={"content": "等待后停止", "provider_config_id": provider["id"]},
        ).json()["id"]
        assert wait_for_terminal(client, run_id)["status"] == "waiting_approval"

        stopped = client.post(f"/api/v1/runs/{run_id}/stop")

        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"


def test_unified_message_queues_active_run_guidance_once_and_keeps_timeline_order(
    tmp_path, provider_server
):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        thread = client.post("/api/v1/threads", json={"title": "统一追加"}).json()
        run = Run(thread_id=thread["id"])
        run.transition(RunStatus.RUNNING)
        app.state.repository.save_run(run)
        request_id = str(uuid4())
        payload = {
            "request_id": request_id,
            "content": "先核对新增约束，再继续执行",
        }
        first = client.post(f"/api/v1/threads/{thread['id']}/message", json=payload)
        duplicate = client.post(f"/api/v1/threads/{thread['id']}/message", json=payload)

        assert first.status_code == duplicate.status_code == 200
        assert "event: guidance_queued" in first.text
        control = client.get(f"/api/v1/runs/{run.id}/control").json()
        assert [item["sequence"] for item in control["guidance"]] == [1]
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert [item["content"] for item in detail["messages"]] == [payload["content"]]
        assert len(detail["runs"]) == 1

        # 模拟页面重连发生在任务结束之后：不能把同一 request_id 重新当聊天发送。
        run.transition(RunStatus.STOPPED, "test completion")
        app.state.repository.save_run(run)
        replayed = client.post(f"/api/v1/threads/{thread['id']}/message", json=payload)
        assert replayed.status_code == 200
        assert "event: guidance_queued" in replayed.text
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert [item["content"] for item in detail["messages"]] == [payload["content"]]


def test_guidance_rejects_terminal_race_without_partial_timeline_record(
    tmp_path, provider_server, monkeypatch
):
    """状态在接口初检后变为终态时，消息、指引和事件必须一起回滚。"""

    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        thread = client.post("/api/v1/threads", json={"title": "终态竞态"}).json()
        run = Run(thread_id=thread["id"])
        run.transition(RunStatus.RUNNING)
        app.state.repository.save_run(run)
        validate_artifacts = app.state.context.validate_user_message_artifacts
        stopped = False

        def finish_before_guidance_commit(thread_id, artifact_ids):
            nonlocal stopped
            if not stopped:
                current = app.state.repository.get_run(run.id)
                assert current
                current.transition(RunStatus.STOPPED, "test terminal race")
                app.state.repository.save_run(current)
                stopped = True
            validate_artifacts(thread_id, artifact_ids)

        monkeypatch.setattr(
            app.state.context,
            "validate_user_message_artifacts",
            finish_before_guidance_commit,
        )
        response = client.post(
            f"/api/v1/threads/{thread['id']}/message",
            json={"request_id": str(uuid4()), "content": "这条指引不能留在终态任务中"},
        )

        assert response.status_code == 409
        assert app.state.repository.list_guidance(run.id) == []
        assert app.state.repository.list_messages(thread["id"]) == []
        assert app.state.repository.list_events(run.id) == []


def test_guidance_rejects_stop_requested_run_without_partial_timeline_record(
    tmp_path, provider_server
):
    """停止已排队但尚未写成终态时，也不能接收必然无法应用的新指引。"""

    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        thread = client.post("/api/v1/threads", json={"title": "停止中的指引"}).json()
        run = Run(thread_id=thread["id"])
        run.transition(RunStatus.RUNNING)
        app.state.repository.save_run(run)
        app.state.repository.request_stop(run.id, request_id=uuid4())

        response = client.post(
            f"/api/v1/threads/{thread['id']}/message",
            json={"request_id": str(uuid4()), "content": "这条指引不能在停止后入队"},
        )

        assert response.status_code == 409
        assert app.state.repository.list_guidance(run.id) == []
        assert app.state.repository.list_messages(thread["id"]) == []
        assert app.state.repository.list_events(run.id) == []


def test_pause_guidance_resume_is_idempotent_and_survives_restart(
    tmp_path, provider_server
):
    app = configured_app(tmp_path)
    app.state.registry.register(FakeEchoTool())
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        provider = create_provider(client, provider_server)
        thread = client.post("/api/v1/threads", json={"title": "run control"}).json()
        run_id = client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={
                "content": "slow-control 运行控制测试",
                "provider_config_id": provider["id"],
                "verification_rules": [{"kind": "regex", "value": "verified"}],
            },
        ).json()["id"]
        for _ in range(50):
            if client.get(f"/api/v1/runs/{run_id}").json()["status"] == "running":
                break
            time.sleep(0.01)

        guidance_request = str(uuid4())
        guidance_body = {
            "request_id": guidance_request,
            "content": "保持原授权范围，并在完成前重新核对证据",
        }
        first_guidance = client.post(
            f"/api/v1/runs/{run_id}/guidance", json=guidance_body
        )
        duplicate_guidance = client.post(
            f"/api/v1/runs/{run_id}/guidance", json=guidance_body
        )
        assert first_guidance.status_code == duplicate_guidance.status_code == 202
        assert first_guidance.json()["sequence"] == duplicate_guidance.json()["sequence"] == 1

        pause_request = {"request_id": str(uuid4())}
        assert client.post(f"/api/v1/runs/{run_id}/pause", json=pause_request).status_code == 202
        assert client.post(f"/api/v1/runs/{run_id}/pause", json=pause_request).status_code == 202
        assert wait_for_terminal(client, run_id)["status"] == "paused"
        control = client.get(f"/api/v1/runs/{run_id}/control").json()
        assert control["guidance"][0]["consumed_at"] is not None

    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "paused"
        resume_request = {"request_id": str(uuid4())}
        first_resume = client.post(f"/api/v1/runs/{run_id}/resume", json=resume_request)
        duplicate_resume = client.post(f"/api/v1/runs/{run_id}/resume", json=resume_request)
        assert first_resume.status_code == duplicate_resume.status_code == 202
        assert wait_for_terminal(client, run_id)["status"] == "completed"
        events = client.get(f"/api/v1/runs/{run_id}/events").json()
        assert sum(item["type"] == "guidance_queued" for item in events) == 1
        assert sum(item["type"] == "guidance_applied" for item in events) == 1
        assert sum(item["type"] == "pause_requested" for item in events) == 1
        assert sum(item["type"] == "run_paused" for item in events) == 1
        assert sum(item["type"] == "run_resumed" for item in events) == 1


def test_competition_lock_stop_openapi_and_upload_policy(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        thread = client.post(
            "/api/v1/threads", json={"title": "比赛", "mode": "competition"}
        ).json()
        repository = app.state.repository
        queued = repository.save_run(Run(thread_id=thread["id"]))
        locked = client.post(
            f"/api/v1/threads/{thread['id']}/messages", json={"content": "extra hint"}
        )
        assert locked.status_code == 409
        stopped = client.post(f"/api/v1/runs/{queued.id}/stop")
        assert stopped.status_code == 200 and stopped.json()["stop_requested"]
        queued.transition(RunStatus.STOPPED, "test stop")
        repository.save_run(queued)
        denied = client.post(
            f"/api/v1/threads/{thread['id']}/artifacts",
            files={"upload": ("unsafe.exe", b"x", "application/octet-stream")},
        )
        assert denied.status_code == 400
        assert client.get("/api/v1/openapi.json").json()["info"]["version"] == "0.5.0"


def test_service_lifespan_resumes_active_run_from_checkpoint(tmp_path, provider_server):
    app = configured_app(tmp_path)
    app.state.registry.register(FakeEchoTool())
    with TestClient(app) as client:
        client.headers.update({"Authorization": "Bearer test-admin-token"})
        provider = create_provider(client, provider_server)
        repository = app.state.repository
        thread = client.post("/api/v1/threads", json={"title": "restart"}).json()
        run = Run(
            thread_id=thread["id"],
            provider="本地协议服务",
            provider_config_id=provider["id"],
        )
        run.transition(RunStatus.RUNNING)
        repository.save_run(run)
        task = TaskSpec(
            body="resume after restart",
            verification_rules=[{"kind": "regex", "value": "verified"}],
        )
        repository.save_run_task(run.id, task)
        provider_config = repository.get_provider_config(provider["id"])
        assert provider_config is not None
        repository.save_provider_snapshot(run.id, [provider_config])
        observation = Observation(
            call_id=__import__("uuid").uuid4(),
            tool_name="test_echo",
            success=True,
            output={"echoed": "verified"},
            summary="completed before restart",
        )
        state = AgentStateModel(
            run_id=run.id,
            task=task,
            plan=AgentPlan(
                summary="persisted plan",
                steps=["verify existing evidence"],
                success_approach="deterministic regex",
            ),
            action=AgentAction(
                kind="call_tool",
                summary="already done",
                tool_name="test_echo",
                tool_input={"text": "verified"},
            ),
            observations=[observation],
            elapsed_seconds=2.0,
        )
        repository.save_checkpoint(run.id, "execute_tool", state.model_dump(mode="json"))
    with TestClient(app) as restarted:
        restarted.headers.update({"Authorization": "Bearer test-admin-token"})
        assert wait_for_terminal(restarted, str(run.id))["status"] == "completed"
        events = restarted.get(f"/api/v1/runs/{run.id}/events").json()
        assert any("恢复" in event["summary"] for event in events)
