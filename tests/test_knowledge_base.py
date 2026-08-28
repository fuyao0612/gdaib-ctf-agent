import json

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.context import ApiContext
from apps.api.main import create_app
from apps.api.schemas import RunCreate
from yuwang.agent import AgentStateModel, DefaultContextBuilder
from yuwang.domain.models import Message, MessageRole, Run, Thread
from yuwang.knowledge import KnowledgeBaseService, KnowledgeDocumentInput, chunk_text
from yuwang.settings import AgentProfileInput, AgentProfileVersion
from yuwang.storage import SQLiteRepository


def test_chunking_and_sparse_retrieval_are_deterministic_and_scenario_scoped(tmp_path):
    repository = SQLiteRepository(tmp_path / "knowledge.db")
    service = KnowledgeBaseService(repository)
    content = "SQL 注入需要参数化查询。\n\n" + "A" * 2_000
    assert chunk_text(content) == chunk_text(content)
    assert len(chunk_text(content)) >= 2

    service.import_document(
        KnowledgeDocumentInput(
            title="SQL 注入修复手册",
            content=content,
            tags=["SQL", "CWE-89"],
            scenarios=["vulnerability_analysis"],
            allow_provider_context=True,
        )
    )
    service.import_document(
        KnowledgeDocumentInput(
            title="仅本地保存的秘密笔记",
            content="SQL 注入密码 password=do-not-send",
            allow_provider_context=False,
        )
    )

    hits = service.search(
        "如何修复 SQL 注入和 CWE-89", scenario="vulnerability_analysis"
    )
    assert hits
    assert hits[0].title == "SQL 注入修复手册"
    assert all("do-not-send" not in hit.content for hit in hits)
    assert service.search("SQL 注入", scenario="incident_response") == []


def test_task_freezes_scenario_and_rag_hits_into_untrusted_context(tmp_path):
    settings = Settings(
        database_path=tmp_path / "api.db",
        artifact_root=tmp_path / "artifacts",
        master_key=Fernet.generate_key().decode(),
    )
    context = ApiContext(settings)
    thread = context.repository.save_thread(
        Thread(title="应急分析", scenario="incident_response")
    )
    message = context.repository.save_message(
        Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="请根据日志建立事件时间线并提取 IOC",
        )
    )
    profile = context.profile_service.resolve(None)
    task = context.build_task(
        thread, RunCreate(), profile, origin_message=message
    )
    assert task.scenario == "incident_response"
    assert task.knowledge_matches
    assert task.knowledge_matches[0].title == "应急响应日志分析基线"

    run = context.repository.save_run(Run(thread_id=thread.id))
    state = AgentStateModel(run_id=run.id, task=task)
    prompt = DefaultContextBuilder(context.repository, settings.artifact_root).build(
        state,
        AgentProfileVersion(
            **AgentProfileInput(name="RAG 测试 Agent").model_dump(), version=1
        ),
        "rag test",
    ).prompt
    payload = json.loads(prompt)
    assert payload["untrusted_user_input"]["scenario"] == "incident_response"
    assert payload["untrusted_retrieved_knowledge"][0]["content_sha256"]
    assert "时间线" in payload["untrusted_retrieved_knowledge"][0]["content"]


def test_admin_can_manage_and_preview_knowledge_documents(tmp_path):
    app = create_app(
        Settings(
            database_path=tmp_path / "api.db",
            artifact_root=tmp_path / "artifacts",
            master_key=Fernet.generate_key().decode(),
        )
    )
    with TestClient(app) as client:
        session = client.post("/api/v1/admin/session")
        client.headers.update({"X-CSRF-Token": session.json()["csrf_token"]})

        documents = client.get("/api/v1/admin/knowledge/documents")
        assert documents.status_code == 200
        assert len(documents.json()) == 4

        created = client.post(
            "/api/v1/admin/knowledge/documents",
            json={
                "title": "自定义 YARA 笔记",
                "content": "YARA 规则应包含稳定字符串并避免过宽条件。",
                "tags": ["YARA"],
                "scenarios": ["reverse_static"],
                "enabled": True,
                "allow_provider_context": True,
            },
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]

        search = client.post(
            "/api/v1/admin/knowledge/search",
            json={"query": "YARA 规则字符串", "scenario": "reverse_static", "limit": 4},
        )
        assert search.status_code == 200
        assert any(hit["document_id"] == document_id for hit in search.json())

        deleted = client.delete(f"/api/v1/admin/knowledge/documents/{document_id}")
        assert deleted.status_code == 204
