"""真实 Provider 验收矩阵。

这些测试只在显式提供隔离测试账户时调用外网。默认跳过不能作为兼容性通过的证据，
也不允许把密钥、响应正文或异常对象写入断言输出。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.fernet import Fernet

from yuwang.agent import AgentEngine
from yuwang.dispatch import classify_new_message
from yuwang.domain.models import Run, RunStatus, TaskSpec, Thread
from yuwang.model_providers import OpenAICompatibleProvider
from yuwang.policy import PolicyEngine
from yuwang.settings import AgentProfileInput, AgentProfileVersion, SecretCipher, SettingsService
from yuwang.settings.models import (
    PROVIDER_PRESETS,
    ProviderConfigInput,
    ProviderPreset,
    resolve_structured_mode,
)
from yuwang.storage import SQLiteRepository
from yuwang.tooling import ToolRegistry


@dataclass(frozen=True)
class RealProviderCase:
    """一类厂商的公开预设与只在本机读取的环境变量前缀。"""

    preset: ProviderPreset
    label: str
    env_name: str


REAL_PROVIDER_CASES = [
    RealProviderCase(ProviderPreset.DEEPSEEK, "DeepSeek", "DEEPSEEK"),
    RealProviderCase(ProviderPreset.QWEN, "阿里云百炼/千问", "QWEN"),
    RealProviderCase(ProviderPreset.GLM, "智谱 GLM", "GLM"),
    RealProviderCase(ProviderPreset.CUSTOM, "自定义 OpenAI 兼容接口", "CUSTOM"),
]


def _required_env(case: RealProviderCase) -> tuple[str, str, str]:
    prefix = f"YUWANG_REAL_{case.env_name}"
    api_key = os.getenv(f"{prefix}_API_KEY", "")
    descriptor = PROVIDER_PRESETS[case.preset]
    base_url = os.getenv(f"{prefix}_BASE_URL", str(descriptor["base_url"]))
    model = os.getenv(f"{prefix}_MODEL", str(descriptor["model"]))
    if case.preset == ProviderPreset.CUSTOM:
        # 自定义接口没有可验证的公共默认地址或模型，必须由测试者明确给出。
        missing = [
            name
            for name, value in [
                (f"{prefix}_API_KEY", api_key),
                (f"{prefix}_BASE_URL", os.getenv(f"{prefix}_BASE_URL", "")),
                (f"{prefix}_MODEL", os.getenv(f"{prefix}_MODEL", "")),
            ]
            if not value
        ]
    else:
        missing = [f"{prefix}_API_KEY"] if not api_key else []
    if missing:
        pytest.skip(f"已跳过真实 {case.label} Provider 验收：未配置 {', '.join(missing)}")
    return base_url, api_key, model


def _provider_from_saved_config(
    service: SettingsService, provider_id: UUID
) -> OpenAICompatibleProvider:
    """与 API 运行时采用相同的配置协商，不把明文密钥持久化到测试数据库。"""

    config = service.get_provider(provider_id)
    return OpenAICompatibleProvider(
        name=config.name,
        base_url=config.base_url,
        api_key=service.decrypt_api_key(config.id),
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
        structured_mode=resolve_structured_mode(config.preset, config.structured_mode),
        fallback_on=config.fallback_on,
        input_price_per_million=config.input_price_per_million,
        output_price_per_million=config.output_price_per_million,
        request_overrides={"enable_thinking": False}
        if config.preset == ProviderPreset.QWEN
        else {},
    )


@pytest.mark.real_provider
@pytest.mark.asyncio
@pytest.mark.parametrize("case", REAL_PROVIDER_CASES, ids=lambda case: case.env_name.lower())
async def test_real_provider_compatibility_when_explicitly_enabled(
    tmp_path: Path, case: RealProviderCase
) -> None:
    """逐项验证真实配置、聊天、意图、Agent 和流式协议，不将跳过记为通过。"""

    if os.getenv("YUWANG_RUN_REAL_PROVIDER_TEST") != "1":
        pytest.skip("未显式启用真实 Provider 验收")
    base_url, api_key, model = _required_env(case)
    repository = SQLiteRepository(tmp_path / f"{case.env_name.lower()}.db")
    service = SettingsService(repository, SecretCipher(Fernet.generate_key().decode()))
    view = service.create_provider(
        ProviderConfigInput(
            name=f"真实验收-{case.label}",
            preset=case.preset,
            base_url=base_url,
            model=model,
            api_key=api_key,
            is_default=True,
            max_retries=0,
        )
    )
    provider_id = view.id
    saved = service.get_provider(provider_id)
    assert saved.preset == case.preset
    assert saved.base_url == base_url.rstrip("/")
    assert saved.model == model
    assert saved.encrypted_api_key != api_key

    provider = _provider_from_saved_config(service, provider_id)

    # 连接测试、普通聊天、严格意图判断和流式输出必须都经过正式客户端。
    connection = await provider.test_connection()
    assert connection.request_count >= 1
    answer = await provider.generate_text(
        [{"role": "user", "content": "请只用一句中文说明持续集成的作用。"}],
        system_prompt="你是简洁的技术助手。",
    )
    assert answer.strip()
    intent = await classify_new_message(
        provider,
        "请解释持续集成，不要执行任务。",
        has_attachments=False,
        recent_messages=[],
    )
    assert intent.kind == "chat"
    chunks = [
        chunk
        async for chunk in provider.stream_text(
            [{"role": "user", "content": "请回复：流式连接正常。"}],
            system_prompt="只返回简短中文。",
        )
    ]
    assert "".join(chunks).strip()

    # 直接建议模式不调用工具，仍要求 Agent 经历 Task Brief 和结构化 Action 两次真实调用。
    profile = AgentProfileVersion(
        **AgentProfileInput(
            name=f"真实验收 Agent-{case.env_name}",
            planning_strategy="direct",
            completion_mode="advisory",
            workflow={"preset": "direct"},
            default_provider_id=provider_id,
        ).model_dump(),
        version=1,
    )
    engine = AgentEngine(
        repository,
        provider,
        ToolRegistry(),
        PolicyEngine(),
        profile=profile,
        artifact_root=tmp_path / "artifacts",
    )
    thread = repository.save_thread(Thread(title=f"真实验收-{case.env_name}"))
    run = repository.save_run(Run(thread_id=thread.id, provider_config_id=provider_id))
    await engine.run(
        run.id,
        TaskSpec(body="请用一句中文说明持续集成的目的。不要调用工具或外部服务。"),
    )
    finished = repository.get_run(run.id)
    assert finished and finished.status == RunStatus.COMPLETED
    assert finished.validation_status == "unverified"
    assert len(repository.list_model_calls(run.id)) >= 2
    assert repository.get_report(run.id) is not None
