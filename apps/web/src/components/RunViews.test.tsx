import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RunAudit } from "../types";
import { InspectorPanel } from "./RunViews";

const audit: RunAudit = {
  run: {
    provider: "DeepSeek 正式配置",
    agent_profile_id: "profile-1",
    agent_profile_version: 2,
    execution_status: "completed",
    validation_status: "unverified",
    evidence_level: "none",
  },
  usage: { tokens: 800, model_calls: 1, steps: 2 },
  limits: { max_tokens: 8000, max_model_calls: 8, max_steps: 20 },
  profile: null,
  history: {
    model: "deepseek-chat",
    started_at: "2026-07-23T09:00:00Z",
    finished_at: "2026-07-23T09:00:05Z",
    token_source: "estimated",
    cost_source: "estimated",
    manual_interventions: 2,
    execution_status: "completed",
    validation_status: "unverified",
  },
};

describe("InspectorPanel", () => {
  it("明确标出本地估算和人工介入次数", () => {
    render(
      <InspectorPanel
        open
        metrics={{ events: 0, tools: 0, replans: 0 }}
        audit={audit}
        events={[]}
        detail={null}
        memories={[]}
        onClose={vi.fn()}
        onToggleMemory={vi.fn()}
        onDeleteMemory={vi.fn()}
        onClearMemories={vi.fn()}
      />,
    );

    expect(screen.getByText("模型：deepseek-chat")).toBeInTheDocument();
    expect(screen.getByText("本地估算 / 本地估算")).toBeInTheDocument();
    expect(screen.getByText("2 次")).toBeInTheDocument();
  });
});
