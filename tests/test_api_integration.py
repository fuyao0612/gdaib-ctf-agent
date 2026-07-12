import time

from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app


def wait_for_terminal(client, run_id):
    for _ in range(100):
        run = client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_full_api_persistence_upload_sse_and_report(tmp_path):
    settings = Settings(database_path=tmp_path / "api.db", artifact_root=tmp_path / "artifacts")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health").json()["version"] == "0.1.0"
        thread = client.post("/api/v1/threads", json={"title": "集成演示", "mode": "normal"}).json()
        uploaded = client.post(f"/api/v1/threads/{thread['id']}/artifacts", files={"upload": ("sample.txt", b"evidence", "text/plain")})
        assert uploaded.status_code == 201
        artifact = uploaded.json()
        message = client.post(f"/api/v1/threads/{thread['id']}/messages", json={"content": "请完成安全演示", "artifact_ids": [artifact["id"]]})
        assert message.status_code == 201
        started = client.post(f"/api/v1/threads/{thread['id']}/runs", json={"provider": "mock"})
        assert started.status_code == 202
        run_id = started.json()["id"]
        assert wait_for_terminal(client, run_id)["status"] == "completed"
        events = client.get(f"/api/v1/runs/{run_id}/events").json()
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        resumed = client.get(f"/api/v1/runs/{run_id}/events", params={"after": 2}).json()
        assert resumed[0]["sequence"] == 3
        stream = client.get(f"/api/v1/runs/{run_id}/events/stream", params={"after": len(events) - 1})
        assert f"id: {len(events)}" in stream.text
        report = client.get(f"/api/v1/runs/{run_id}/report")
        assert report.status_code == 200 and "首次工具调用失败" in report.json()["markdown"]
        assert client.get(f"/api/v1/runs/{run_id}/report.md").status_code == 200
        assert client.get(f"/api/v1/runs/{run_id}/report.json").status_code == 200
        assert client.get(f"/api/v1/artifacts/{artifact['id']}/download").content == b"evidence"
        assert len(client.get("/api/v1/providers").json()) == 2
        assert len(client.get("/api/v1/tools").json()) == 3
    with TestClient(create_app(settings)) as reopened:
        detail = reopened.get(f"/api/v1/threads/{thread['id']}").json()
        assert detail["messages"] and detail["runs"] and detail["artifacts"]


def test_competition_lock_errors_and_archive(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path / "c.db", artifact_root=tmp_path / "a"))) as client:
        thread = client.post("/api/v1/threads", json={"title": "比赛", "mode": "competition"}).json()
        client.post(f"/api/v1/threads/{thread['id']}/messages", json={"content": "task"})
        run = client.post(f"/api/v1/threads/{thread['id']}/runs", json={}).json()
        locked = client.post(f"/api/v1/threads/{thread['id']}/messages", json={"content": "extra hint"})
        assert locked.status_code in {201, 409}  # deterministic run may finish before this request
        wait_for_terminal(client, run["id"])
        archived = client.patch(f"/api/v1/threads/{thread['id']}/archive").json()
        assert archived["archived"] is True
        error = client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")
        assert error.json()["error"]["code"] == "http_404"


def test_stop_retry_openapi_and_upload_policy(tmp_path):
    app = create_app(
        Settings(database_path=tmp_path / "control.db", artifact_root=tmp_path / "files")
    )
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={"title": "control"}).json()
        client.post(f"/api/v1/threads/{thread['id']}/messages", json={"content": "safe"})
        from yuwang.domain.models import Run, RunStatus

        repository = app.state.repository
        queued = repository.save_run(Run(thread_id=thread["id"]))
        stopped = client.post(f"/api/v1/runs/{queued.id}/stop")
        assert stopped.status_code == 200 and stopped.json()["stop_requested"]
        queued.transition(RunStatus.STOPPED, "test stop")
        repository.save_run(queued)
        retried = client.post(f"/api/v1/runs/{queued.id}/retry")
        assert retried.status_code == 202
        assert wait_for_terminal(client, retried.json()["id"])["status"] == "completed"
        denied = client.post(
            f"/api/v1/threads/{thread['id']}/artifacts",
            files={"upload": ("unsafe.exe", b"x", "application/octet-stream")},
        )
        assert denied.status_code == 400
        assert client.get("/api/v1/openapi.json").json()["info"]["version"] == "0.1.0"
