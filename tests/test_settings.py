import pytest
from cryptography.fernet import Fernet

from yuwang.domain.models import Run, TaskSpec, Thread
from yuwang.settings import (
    AgentDefaults,
    AgentProfileInput,
    AgentProfileService,
    ProviderConfigInput,
    ProviderPreset,
    SecretCipher,
    SettingsService,
    SkillInput,
    SkillService,
)
from yuwang.settings.models import (
    PROVIDER_PRESETS,
    ChatDefaults,
    resolve_structured_mode,
    validate_provider_url,
)
from yuwang.storage import SQLiteRepository


def input_config(**overrides):
    values = {
        "name": "DeepSeek 生产",
        "preset": ProviderPreset.DEEPSEEK,
        "base_url": PROVIDER_PRESETS[ProviderPreset.DEEPSEEK]["base_url"],
        "model": PROVIDER_PRESETS[ProviderPreset.DEEPSEEK]["model"],
        "api_key": "secret-provider-key",
        "enabled": True,
        "is_default": True,
        "fallback_order": 0,
        "timeout_seconds": 30,
        "max_retries": 2,
        "structured_mode": "json_schema",
    }
    values.update(overrides)
    return ProviderConfigInput.model_validate(values)


def test_secret_cipher_authenticated_round_trip_and_wrong_key():
    first = SecretCipher(Fernet.generate_key().decode())
    second = SecretCipher(Fernet.generate_key().decode())
    ciphertext = first.encrypt("sensitive-key")
    assert "sensitive-key" not in ciphertext
    assert first.decrypt(ciphertext) == "sensitive-key"
    with pytest.raises(ValueError, match="无法解密"):
        second.decrypt(ciphertext)
    with pytest.raises(ValueError, match="YUWANG_MASTER_KEY"):
        SecretCipher("not-a-valid-key")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?token=secret",
        "https://example.com/v1#fragment",
    ],
)
def test_provider_url_rejects_unsafe_values(url):
    with pytest.raises(ValueError):
        validate_provider_url(url)
    assert validate_provider_url("http://127.0.0.1:9000/v1", True).startswith("http://")


def test_provider_crud_default_fallback_and_key_never_in_view_or_database_plaintext(tmp_path):
    repository = SQLiteRepository(tmp_path / "settings.db")
    service = SettingsService(repository, SecretCipher(Fernet.generate_key().decode()))
    first = service.create_provider(input_config())
    assert first.has_api_key and not hasattr(first, "api_key")
    raw_database = (tmp_path / "settings.db").read_bytes()
    assert b"secret-provider-key" not in raw_database

    second = service.create_provider(
        input_config(
            name="GLM 备用",
            preset="glm",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4.5-flash",
            api_key="second-secret-key",
            is_default=False,
            fallback_order=1,
        )
    )
    chain = service.resolve_chain()
    assert [item.id for item in chain] == [first.id, second.id]
    with pytest.raises(ValueError, match="默认 Provider"):
        service.delete_provider(first.id)

    updated = service.update_provider(
        second.id,
        input_config(
            name="GLM 默认",
            preset="glm",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4.5-flash",
            api_key=None,
            is_default=True,
            fallback_order=0,
        ),
    )
    assert updated.is_default
    assert not service.get_provider(first.id).is_default
    assert service.decrypt_api_key(second.id) == "second-secret-key"
    service.delete_provider(first.id)
    assert len(service.list_providers()) == 1


def test_provider_fallback_requires_explicit_profile_chain_and_default_cannot_be_unset(tmp_path):
    repository = SQLiteRepository(tmp_path / "settings.db")
    service = SettingsService(repository, SecretCipher(Fernet.generate_key().decode()))
    first = service.create_provider(input_config(name="当前模型", api_key="first-secret"))
    second = service.create_provider(
        input_config(name="未配置备用", api_key="second-secret", is_default=False)
    )

    assert [item.id for item in service.resolve_chain(first.id, [])] == [first.id]
    with pytest.raises(ValueError, match="不能取消唯一默认"):
        service.update_provider(
            first.id,
            input_config(name="当前模型", api_key=None, is_default=False),
        )

    service.update_provider(
        second.id,
        input_config(name="新默认", api_key=None, is_default=True),
    )
    providers = service.list_providers()
    assert [item.id for item in providers if item.is_default] == [second.id]


def test_agent_defaults_persist(tmp_path):
    repository = SQLiteRepository(tmp_path / "settings.db")
    service = SettingsService(repository, SecretCipher(Fernet.generate_key().decode()))
    defaults = AgentDefaults(context_token_budget=64000, provider_retry_budget=4)
    service.save_agent_defaults(defaults)
    assert service.get_agent_defaults() == defaults


def test_provider_snapshot_is_encrypted_and_immutable(tmp_path):
    repository = SQLiteRepository(tmp_path / "snapshots.db")
    service = SettingsService(repository, SecretCipher(Fernet.generate_key().decode()))
    view = service.create_provider(input_config())
    stored = service.get_provider(view.id)
    run_id = __import__("uuid").uuid4()
    repository.save_provider_snapshot(run_id, [stored])
    restored = repository.get_provider_snapshot(run_id)
    assert restored == [stored]
    assert b"secret-provider-key" not in (tmp_path / "snapshots.db").read_bytes()
    changed = stored.model_copy(update={"model": "changed-model"})
    with pytest.raises(ValueError, match="不可变"):
        repository.save_provider_snapshot(run_id, [changed])


def test_provider_capability_negotiation_preserves_v02_rows():
    assert resolve_structured_mode(ProviderPreset.DEEPSEEK, "auto") == "json_object"
    assert resolve_structured_mode(ProviderPreset.DEEPSEEK, "json_schema") == "json_object"
    assert resolve_structured_mode(ProviderPreset.CUSTOM, "json_schema") == "json_schema"


def test_deleting_non_default_provider_atomically_falls_back_referencing_threads(tmp_path):
    repository = SQLiteRepository(tmp_path / "settings.db")
    service = SettingsService(repository, SecretCipher(Fernet.generate_key().decode()))
    default = service.create_provider(input_config(name="全局默认", api_key="default-secret-key"))
    selected = service.create_provider(
        input_config(
            name="待删除模型",
            api_key="delete-secret-key",
            is_default=False,
            fallback_order=1,
        )
    )
    thread = repository.save_thread(Thread(title="保留选择", provider_config_id=selected.id))

    impact = service.provider_deletion_impact(selected.id)
    assert impact["affected_thread_count"] == 1
    assert impact["fallback_provider"] == {
        "id": str(default.id),
        "name": default.name,
        "model": default.model,
    }
    assert impact["blocking_reasons"] == []
    assert "delete-secret-key" not in repr(impact)

    service.delete_provider(selected.id)

    with pytest.raises(KeyError):
        service.get_provider(selected.id)
    restored = repository.get_thread(thread.id)
    assert restored is not None
    assert restored.provider_config_id == default.id
    assert restored.provider_fallback_notice and "待删除模型" in restored.provider_fallback_notice
    assert b"delete-secret-key" not in (tmp_path / "settings.db").read_bytes()


def test_provider_deletion_rejects_chat_profile_and_active_run_references(tmp_path):
    repository = SQLiteRepository(tmp_path / "settings.db")
    service = SettingsService(repository, SecretCipher(Fernet.generate_key().decode()))
    service.create_provider(input_config(name="全局默认", api_key="default-secret-key"))
    selected = service.create_provider(
        input_config(
            name="受引用模型",
            api_key="referenced-secret-key",
            is_default=False,
            fallback_order=1,
        )
    )
    service.save_chat_defaults(ChatDefaults(default_provider_id=selected.id))
    AgentProfileService(repository).create(
        AgentProfileInput(name="引用模型的任务配置", default_provider_id=selected.id)
    )
    thread = repository.save_thread(Thread(title="活动任务", provider_config_id=selected.id))
    repository.save_run(
        Run(thread_id=thread.id, provider="受引用模型", provider_config_id=selected.id)
    )

    impact = service.provider_deletion_impact(selected.id)
    reasons = impact["blocking_reasons"]
    assert any("默认聊天模型" in str(reason) for reason in reasons)
    assert any("Agent 配置" in str(reason) for reason in reasons)
    assert any("活动 Run" in str(reason) for reason in reasons)
    assert "referenced-secret-key" not in repr(impact)
    with pytest.raises(ValueError, match="无法删除 Provider"):
        service.delete_provider(selected.id)
    assert service.get_provider(selected.id).id == selected.id


def test_declarative_skills_snapshot_and_thread_cleanup(tmp_path):
    repository = SQLiteRepository(tmp_path / "skills.db")
    service = SkillService(repository)
    skill = service.create(
        SkillInput(
            name="发布检查",
            description="帮助整理可审计的发布前检查。",
            prompt="先明确发布范围，再按步骤核对。",
            steps=["确认变更范围", "核对回滚方案"],
            checklist=["没有未授权变更", "已记录验证结果"],
        )
    )
    thread = repository.save_thread(Thread(title="技能对话", skill_ids=[skill.id]))
    snapshots = service.snapshots_for(thread.skill_ids)
    task = TaskSpec(body="整理发布检查", skills=snapshots)

    assert task.skills[0].name == "发布检查"
    assert task.skills[0].steps == ["确认变更范围", "核对回滚方案"]
    updated = service.update(
        skill.id,
        SkillInput(name="发布检查", prompt="已修改模板", enabled=True),
    )
    assert updated.prompt == "已修改模板"
    assert task.skills[0].prompt == "先明确发布范围，再按步骤核对。"

    service.delete(skill.id)
    restored = repository.get_thread(thread.id)
    assert restored and restored.skill_ids == []
    with pytest.raises(KeyError, match="Skill 不存在"):
        service.snapshots_for([skill.id])


def test_skills_reject_code_payloads_and_disabled_selection(tmp_path):
    repository = SQLiteRepository(tmp_path / "skills.db")
    service = SkillService(repository)
    with pytest.raises(ValueError, match="代码块"):
        SkillInput(name="脚本", prompt="```powershell\nRemove-Item\n```")
    disabled = service.create(SkillInput(name="停用模板", prompt="仅供阅读", enabled=False))
    with pytest.raises(ValueError, match="已停用"):
        service.snapshots_for([disabled.id])
