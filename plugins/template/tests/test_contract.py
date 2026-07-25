import pytest
from template_tool import CharacterCountTool

from yuwang.tooling import assert_tool_execution_contract


@pytest.mark.asyncio
async def test_character_count_contract() -> None:
    await assert_tool_execution_contract(CharacterCountTool(), {"text": "hello"})
