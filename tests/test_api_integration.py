import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
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
            if '"status":"ok"' in prompt:
                response_content = '{"status":"ok"}'
            else:
                context = json.loads(prompt)
                if "生成动态计划" in context["purpose"] or "重新规划" in context["purpose"]:
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
    yield f"http://127.0.0.1:{server.server_port}/v1"
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
        assert client.get("/api/v1/health").json()["version"] == "0.4.0"
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


def test_waiting_input_api_persists_memory_and_resumes(tmp_path, provider_server):
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
        supplied = client.post(
            f"/api/v1/runs/{run_id}/input", json={"content": "目标受众是技术团队"}
        )
        assert supplied.status_code == 202, supplied.text
        finished = wait_for_terminal(client, run_id)
        assert finished["status"] == "completed"
        assert finished["validation_status"] == "unverified"
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
        assert client.get("/api/v1/openapi.json").json()["info"]["version"] == "0.4.0"


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
