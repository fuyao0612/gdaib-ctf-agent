import os

import pytest

from yuwang.model_providers import OpenAICompatibleProvider


@pytest.mark.real_provider
@pytest.mark.asyncio
async def test_real_provider_connection_when_explicitly_enabled():
    if os.getenv("YUWANG_RUN_REAL_PROVIDER_TEST") != "1":
        pytest.skip("未显式启用真实 Provider 冒烟测试")
    required = {
        "base_url": os.getenv("YUWANG_REAL_PROVIDER_BASE_URL"),
        "api_key": os.getenv("YUWANG_REAL_PROVIDER_API_KEY"),
        "model": os.getenv("YUWANG_REAL_PROVIDER_MODEL"),
    }
    if not all(required.values()):
        pytest.skip("未配置真实 Provider 环境变量")
    provider = OpenAICompatibleProvider(
        name="real-smoke",
        base_url=str(required["base_url"]),
        api_key=str(required["api_key"]),
        model=str(required["model"]),
        timeout_seconds=30,
        max_retries=1,
        structured_mode="json_object",
    )
    await provider.test_connection()
