import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app
from tests.fakes import FakeEchoTool
from yuwang.domain.models import Run, RunStatus


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
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(length))
            prompt = body["messages"][-1]["content"]
            if '"status":"ok"' in prompt:
                response_content = '{"status":"ok"}'
            else:
                fail = "tool_failures=0" in prompt
                action = {
                    "kind": "call_tool",
                    "summary": "协议服务选择测试工具",
                    "tool_name": "test_echo",
                    "tool_input": {"text": "verified", "fail": fail},
                }
                response_content = json.dumps(action)
            encoded = json.dumps(
                {"choices": [{"message": {"content": response_content}}]}
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
    return body


def test_full_api_persistence_upload_sse_and_report(tmp_path, provider_server):
    app = configured_app(tmp_path)
    app.state.registry.register(FakeEchoTool())
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["version"] == "0.1.0"
        provider = create_provider(client, provider_server)
        thread = client.post(
            "/api/v1/threads", json={"title": "集成任务", "mode": "normal"}
        ).json()
        uploaded = client.post(
            f"/api/v1/threads/{thread['id']}/artifacts",
            files={"upload": ("sample.txt", b"evidence", "text/plain")},
        )
        artifact = uploaded.json()
        message = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            json={"content": "执行协议集成任务", "artifact_ids": [artifact["id"]]},
        )
        assert message.status_code == 201
        started = client.post(
            f"/api/v1/threads/{thread['id']}/runs",
            json={"provider_config_id": provider["id"]},
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        assert wait_for_terminal(client, run_id)["status"] == "completed"
        events = client.get(f"/api/v1/runs/{run_id}/events").json()
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        resumed = client.get(
            f"/api/v1/runs/{run_id}/events", params={"after": 2}
        ).json()
        assert resumed[0]["sequence"] == 3
        report = client.get(f"/api/v1/runs/{run_id}/report")
        assert report.status_code == 200 and "首次工具调用失败" in report.json()["markdown"]
        assert client.get(f"/api/v1/artifacts/{artifact['id']}/download").content == b"evidence"
        assert len(client.get("/api/v1/providers").json()) == 1
        assert len(client.get("/api/v1/tools").json()) == 3
    with TestClient(app) as reopened:
        detail = reopened.get(f"/api/v1/threads/{thread['id']}").json()
        assert detail["messages"] and detail["runs"] and detail["artifacts"]


def test_unconfigured_provider_and_admin_auth_are_explicit(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/admin/settings/providers").status_code == 401
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


def test_competition_lock_stop_openapi_and_upload_policy(tmp_path):
    app = configured_app(tmp_path)
    with TestClient(app) as client:
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
        assert client.get("/api/v1/openapi.json").json()["info"]["version"] == "0.1.0"
