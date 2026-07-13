"""用于离线评估 Agent 运行质量的轻量指标。"""

from pydantic import BaseModel, Field


class RunMetrics(BaseModel):
    """Stable evaluation contract consumed by reports and future benchmarks."""

    success: bool
    duration_ms: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    tool_failures: int = Field(ge=0)
    tokens: int = Field(ge=0)
