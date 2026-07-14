import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Event, Report, Run, RunAudit, RunStatus } from "../types";
import { ResultCard, RunProgress } from "./RunSummary";
import { presentPhases, tokenUsageLabel } from "./run-presentation";

const started = "2026-07-14T00:00:00Z";

function makeRun(status: RunStatus): Run {
  return {
    id: `run-${status}`,
    thread_id: "thread-1",
    status,
    provider: "DeepSeek",
    agent_profile_id: "agent-1",
    agent_profile_version: 2,
    completion_mode: "evidence",
    validation_status: status === "completed" ? "validated" : "pending",
    evidence_level: status === "completed" ? "external" : "none",
    attempt: 1,
    stop_requested: status === "stopped",
    error: status === "failed" ? "模型服务不可用" : undefined,
    started_at: started,
    finished_at: status === "completed" ? "2026-07-14T00:00:04Z" : null,
  };
}

const events: Event[] = [
  {
    event_id: "e1",
    run_id: "run-running",
    sequence: 1,
    type: "run_started",
    timestamp: started,
    summary: "Agent 运行已开始",
    payload: {},
  },
  {
    event_id: "e2",
    run_id: "run-running",
    sequence: 2,
    type: "plan_updated",
    timestamp: "2026-07-14T00:00:01Z",
    summary: "先读取输入，再验证结果",
    payload: {},
  },
  {
    event_id: "e3",
    run_id: "run-running",
    sequence: 3,
    type: "tool_started",
    timestamp: "2026-07-14T00:00:02Z",
    summary: "开始调用参考工具",
    payload: {},
  },
];

const audit: RunAudit = {
  run: {
    provider: "DeepSeek",
    agent_profile_id: "agent-1",
    agent_profile_version: 2,
    validation_status: "pending",
    evidence_level: "none",
  },
  usage: { steps: 4, model_calls: 2, tool_calls: 1, tokens: 321, elapsed_seconds: 3 },
  limits: { max_steps: 20, max_model_calls: 8, max_tool_calls: 8, max_tokens: 8000 },
  profile: {
    name: "默认安全 Agent",
    version: 2,
    completion_mode: "evidence",
    planning_strategy: "dynamic",
    workflow_preset: "verified",
    default_provider_id: null,
    fallback_provider_ids: [],
    context_policy: {
      recent_message_limit: 20,
      include_thread_summary: true,
      include_run_summaries: true,
      include_memories: true,
      text_attachment_char_limit: 20000,
    },
    memory_policy: { enabled: true, persist_important_facts: true, max_facts: 100 },
    intervention_policy: { normal_mode: "wait", competition_mode: "fail", max_requests: 2 },
  },
  model_calls: [
    {
      id: "m1",
      provider: "DeepSeek",
      model: "deepseek-v4-flash",
      duration_ms: 100,
      input_tokens: 200,
      output_tokens: 121,
      status: "succeeded",
      error_category: null,
      metadata: { usage_reported: false },
    },
  ],
  evidence: [],
  checkpoints: [
    { checkpoint_sequence: 1, node: "plan", elapsed_seconds: 1, created_at: started },
    { checkpoint_sequence: 2, node: "execute_tool", elapsed_seconds: 2, created_at: started },
  ],
};

const report: Report = {
  markdown: "# 完整报告\n\n已完成。",
  data: {
    final_answer: "已验证答案",
    evidence: ["参考工具返回匹配结果"],
  },
};

describe("Agent 五阶段进度", () => {
  it("从现有事件和检查点派生当前执行阶段", () => {
    const phases = presentPhases(makeRun("running"), events, audit);
    expect(phases.map((phase) => phase.state)).toEqual([
      "completed",
      "completed",
      "active",
      "pending",
      "pending",
    ]);
  });

  it("展示 Provider、模型、Agent、预算和厂商未提供的 Token", () => {
    render(<RunProgress run={makeRun("running")} events={events} audit={audit} />);
    expect(screen.getAllByText("执行动作")).toHaveLength(2);
    expect(screen.getByText(/DeepSeek \/ deepseek-v4-flash/)).toBeInTheDocument();
    expect(screen.getByText(/默认安全 Agent · v2/)).toBeInTheDocument();
    expect(tokenUsageLabel(audit)).toContain("厂商未提供");
  });
});

describe("统一任务结果卡片", () => {
  it.each([
    ["completed", "任务成功", "已验证答案"],
    ["failed", "任务失败", "模型服务不可用"],
    ["stopped", "任务已停止", "确认任务范围"],
    ["waiting_input", "等待用户补充", "在下方补充"],
  ] as const)("展示 %s 状态", (status, title, expected) => {
    const run = makeRun(status);
    render(
      <ResultCard
        run={run}
        events={events}
        audit={audit}
        report={status === "completed" ? report : null}
        messages={[]}
      />,
    );
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.getByText(new RegExp(expected))).toBeInTheDocument();
  });
});
