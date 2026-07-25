"""可直接运行的最小 Python 工具模板。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from yuwang.tooling import ToolPlugin, ToolSpec


class CharacterCountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10_000)


class CharacterCountOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characters: int


class CharacterCountTool(ToolPlugin[CharacterCountInput, CharacterCountOutput]):
    input_model = CharacterCountInput
    output_model = CharacterCountOutput

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            namespace="example",
            name="character_count",
            version="1.0.0",
            description="统计输入文本的字符数",
            capabilities=["text"],
            scenarios=["general"],
            risk="low",
            permissions=[],
            requires_network=False,
            allowed_target_types=[],
            timeout_seconds=3,
            error_codes=["invalid_input"],
            idempotent=True,
            artifact_types=[],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute(self, value: CharacterCountInput) -> CharacterCountOutput:
        return CharacterCountOutput(characters=len(value.text))


def create_tool() -> CharacterCountTool:
    return CharacterCountTool()
