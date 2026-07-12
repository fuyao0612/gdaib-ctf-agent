import pytest
from cryptography.fernet import Fernet

from yuwang.settings import (
    AgentDefaults,
    ProviderConfigInput,
    ProviderPreset,
    SecretCipher,
    SettingsService,
)
from yuwang.settings.models import PROVIDER_PRESETS, validate_provider_url
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


def test_agent_defaults_persist(tmp_path):
    repository = SQLiteRepository(tmp_path / "settings.db")
    service = SettingsService(repository, SecretCipher(Fernet.generate_key().decode()))
    defaults = AgentDefaults(context_token_budget=64000, provider_retry_budget=4)
    service.save_agent_defaults(defaults)
    assert service.get_agent_defaults() == defaults
