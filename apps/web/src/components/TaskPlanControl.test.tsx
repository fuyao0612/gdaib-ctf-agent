import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Run, RunControl } from "../types";
import TaskPlanControl from "./TaskPlanControl";

const run = (status: Run["status"]): Run => ({
  id: "run-1",
  thread_id: "thread-1",
  status,
  provider: "Provider",
  agent_profile_id: "profile-1",
  agent_profile_version: 1,
  plan_mode: "approval",
  completion_mode: "advisory",
  validation_status: "pending",
  evidence_level: "none",
  attempt: 1,
  stop_requested: false,
});

const control: RunControl = {
  status: "waiting_approval",
  plan_mode: "approval",
  task_briefs: [
    {
      id: "brief-1",
      run_id: "run-1",
      version: 1,
      original_request: "整理方案",
      goal: "生成可审核方案",
      authorized_scope: ["本地项目"],
      constraints: ["不扩大范围"],
      success_criteria: ["用户确认"],
      expected_output: "Markdown",
      known_information: [],
      assumptions: [],
      risks: [],
      needs_clarification: false,
      clarification_questions: [],
      source: "agent",
      created_at: "2026-07-16T00:00:00Z",
    },
  ],
  plans: [
    {
      id: "plan-1",
      run_id: "run-1",
      version: 1,
      source: "agent_initial",
      change_reason: "",
      based_on_version: null,
      created_at: "2026-07-16T00:00:00Z",
      plan: {
        summary: "先确认后执行",
        steps: ["确认范围", "生成结果"],
        success_approach: "用户确认",
        expected_results: [],
        verification_methods: [],
        risks: [],
        dependencies: [],
      },
    },
  ],
  guidance: [],
};

describe("TaskPlanControl", () => {
  it("展示 Task Brief 并提交澄清", () => {
    const onClarify = vi.fn();
    render(
      <TaskPlanControl
        run={run("waiting_clarification")}
        control={{
          ...control,
          status: "waiting_clarification",
          task_briefs: [
            {
              ...control.task_briefs[0],
              needs_clarification: true,
              clarification_questions: ["目标受众是谁？"],
            },
          ],
        }}
        busy={false}
        onClarify={onClarify}
        onEdit={vi.fn()}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByText("生成可审核方案")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务澄清"), {
      target: { value: "目标受众是新成员" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交澄清并继续" }));
    expect(onClarify).toHaveBeenCalledWith("目标受众是新成员", 1);
  });

  it("编辑、拒绝和批准都携带当前计划版本", () => {
    const onEdit = vi.fn();
    const onDecide = vi.fn();
    render(
      <TaskPlanControl
        run={run("waiting_approval")}
        control={control}
        busy={false}
        onClarify={vi.fn()}
        onEdit={onEdit}
        onDecide={onDecide}
      />,
    );
    fireEvent.change(screen.getByLabelText("计划意见"), {
      target: { value: "增加回滚步骤" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存新版本" }));
    expect(onEdit.mock.calls[0][1]).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "拒绝并重新规划" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并执行" }));
    expect(onDecide).toHaveBeenNthCalledWith(1, "reject", 1, "增加回滚步骤");
    expect(onDecide).toHaveBeenNthCalledWith(2, "approve", 1, "增加回滚步骤");
  });
});
