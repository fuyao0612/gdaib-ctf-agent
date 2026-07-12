import json
import sqlite3

import pytest

from yuwang.domain.models import Budget, Thread
from yuwang.settings import (
    AgentProfileExport,
    AgentProfileInput,
    AgentProfileService,
    SafeTemplateRenderer,
)
from yuwang.settings.models import ProviderConfig, ProviderPreset
from yuwang.storage import SQLiteRepository


def test_profile_versions_copy_rollback_default_and_immutable_snapshot(tmp_path):
    repository = SQLiteRepository(tmp_path / "profiles.db")
    service = AgentProfileService(repository)
    default = service.ensure_default(Budget(max_steps=12))
    assert default.version == 1 and default.is_default

    created = service.create(
        AgentProfileInput(
            name="分析助手",
            description="版本化配置",
            user_prompt_template="任务：{task}\n预算：{remaining_budget}",
            completion_mode="advisory",
        )
    )
    updated_input = AgentProfileInput.model_validate(
        {
            **created.model_dump(
                exclude={"profile_id", "version", "schema_version", "created_at"}
            ),
            "description": "第二版",
        }
    )
    updated = service.update(created.profile_id, updated_input)
    assert updated.version == 2
    assert [value.version for value in repository.list_agent_profile_versions(created.profile_id)] == [
        1,
        2,
    ]
    rolled_back = service.rollback(created.profile_id, 1)
    assert rolled_back.version == 3 and rolled_back.description == "版本化配置"

    copied = service.copy(created.profile_id, "分析助手副本")
    assert copied.profile_id != created.profile_id and copied.version == 1
    promoted = service.set_default(copied.profile_id)
    assert promoted.is_default
    assert not service.require(default.profile_id).is_default
    with pytest.raises(ValueError, match="默认"):
        service.delete(copied.profile_id)

    run_id = __import__("uuid").uuid4()
    repository.save_run_agent_profile(run_id, updated)
    assert repository.get_run_agent_profile(run_id) == updated
    with pytest.raises(ValueError, match="不可变"):
        repository.save_run_agent_profile(run_id, rolled_back)


def test_profile_export_import_is_secretless_and_template_safe(tmp_path):
    first_repository = SQLiteRepository(tmp_path / "first.db")
    service = AgentProfileService(first_repository)
    primary = first_repository.save_provider_config(
        ProviderConfig(
            name="primary",
            preset=ProviderPreset.CUSTOM,
            base_url="https://provider.example/v1",
            model="model",
            encrypted_api_key="encrypted",
            enabled=True,
            is_default=True,
            fallback_order=0,
            timeout_seconds=30,
            max_retries=1,
        )
    )
    fallback = first_repository.save_provider_config(
        primary.model_copy(
            update={
                "id": __import__("uuid").uuid4(),
                "name": "fallback",
                "is_default": False,
            }
        )
    )
    profile = service.create(
        AgentProfileInput(
            name="可导出配置",
            default_provider_id=primary.id,
            fallback_provider_ids=[fallback.id],
            user_prompt_template="{task} / {thread_summary}",
        )
    )
    bundle = service.export(profile.profile_id)
    serialized = bundle.model_dump_json()
    assert bundle.profiles[0].default_provider_id is None
    assert bundle.profiles[0].fallback_provider_ids == []
    assert "api_key" not in serialized.lower() and "storage_ref" not in serialized

    second_service = AgentProfileService(SQLiteRepository(tmp_path / "second.db"))
    imported = second_service.import_profiles(AgentProfileExport.model_validate_json(serialized))
    assert imported[0].name == "可导出配置" and imported[0].profile_id != profile.profile_id

    rendered = SafeTemplateRenderer.render(
        "任务={task};观察={observations}", {"task": "总结", "observations": [1, 2]}
    )
    assert rendered == "任务=总结;观察=[1, 2]"
    for unsafe in ["{unknown}", "{task.__class__}", "{task!r}", "{task:>10}"]:
        with pytest.raises(ValueError):
            SafeTemplateRenderer.validate(unsafe)


def test_v02_database_and_json_rows_migrate_without_profile_fields(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_thread = Thread(title="v0.2 thread").model_dump(mode="json")
    legacy_thread.pop("agent_profile_id", None)
    legacy_thread.pop("agent_profile_version", None)
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY);
            INSERT INTO schema_migrations(version) VALUES (1);
            CREATE TABLE threads(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
            """
        )
        db.execute(
            "INSERT INTO threads VALUES(?,?,?)",
            (legacy_thread["id"], json.dumps(legacy_thread, default=str), legacy_thread["created_at"]),
        )
    repository = SQLiteRepository(path)
    restored = repository.get_thread(legacy_thread["id"])
    assert restored and restored.agent_profile_id is None
    service = AgentProfileService(repository)
    assert service.ensure_default().is_default
    with sqlite3.connect(path) as db:
        versions = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
    assert versions == {1, 2}
