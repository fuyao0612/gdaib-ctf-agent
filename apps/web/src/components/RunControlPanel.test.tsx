import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Event, Run, RunControl, RunStatus } from "../types";
import RunControlPanel from "./RunControlPanel";

const now = "2026-07-17T08:00:00Z";

function makeRun(status: RunStatus): Run {
  return {
    id: "run-1",
    thread_id: "thread-1",
    status,
    provider: "测试 Provider",
    agent_profile_id: "agent-1",
    agent_profile_version: 1,
    plan_mode: "approval",
    completion_mode: "advisory",
    validation_status: "pending",
    evidence_level: "none",
    attempt: 1,
    stop_requested: false,
  };
}

const control: RunControl = {
  status: "running",
  plan_mode: "approval",
  task_briefs: [],
  plans: [],
  guidance: [
    {
      id: "guidance-1",
      run_id: "run-1",
      sequence: 1,
      content: "先保留原授权范围",
      created_at: now,
      consumed_at: null,
    },
    {
      id: "guidance-2",
      run_id: "run-1",
      sequence: 2,
      content: "再核对验证证据",
      created_at: now,
      consumed_at: "2026-07-17T08:01:00Z",
    },
  ],
};

const noEvents: Event[] = [];

describe("运行控制面板", () => {
  it("立即锁定重复暂停并显示安全检查点语义", async () => {
    let finish: ((success: boolean) => void) | undefined;
    const onPause = vi.fn(
      () => new Promise<boolean>((resolve) => { finish = resolve; }),
    );
    render(
      <RunControlPanel
        run={makeRun("running")}
        control={{ ...control, guidance: [] }}
        events={noEvents}
        busy={false}
        onPause={onPause}
        onResume={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "安全暂停" }));
    expect(screen.getByRole("button", { name: "暂停已排队" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "暂停已排队" }));
    expect(onPause).toHaveBeenCalledTimes(1);
    finish?.(true);
  });

  it("按顺序展示已排队和已在检查点应用，不把消费指引误写成重规划", () => {
    render(
      <RunControlPanel
        run={makeRun("paused")}
        control={{ ...control, status: "paused" }}
        events={noEvents}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn().mockResolvedValue(true)}
      />,
    );

    const records = screen.getAllByRole("listitem");
    expect(records[0]).toHaveTextContent("#1");
    expect(records[0]).toHaveTextContent("已排队");
    expect(records[1]).toHaveTextContent("#2");
    expect(records[1]).toHaveTextContent("已在检查点应用");
    expect(records[1]).not.toHaveTextContent("已重规划");
    expect(screen.getByRole("button", { name: "从检查点继续" })).toBeEnabled();
  });

  it("只有事件明确关联本指引时才显示因本指引重规划", () => {
    const events: Event[] = [
      {
        event_id: "event-1",
        run_id: "run-1",
        sequence: 3,
        type: "replanned",
        // 即使事件时间更晚，只关联 #1 也不能把 #2 标成重规划原因。
        timestamp: "2099-07-17T08:02:00Z",
        summary: "根据其他指引重新规划",
        payload: { guidance_sequences: [1] },
      },
    ];
    const { rerender } = render(
      <RunControlPanel
        run={makeRun("paused")}
        control={{ ...control, status: "paused" }}
        events={events}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("listitem")[1]).not.toHaveTextContent("重规划");

    rerender(
      <RunControlPanel
        run={makeRun("paused")}
        control={{ ...control, status: "paused" }}
        events={[
          ...events,
          {
            ...events[0],
            event_id: "event-2",
            sequence: 4,
            payload: { guidance_sequences: [2] },
          },
        ]}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("listitem")[1]).toHaveTextContent(
      "因本指引重规划",
    );
  });
});
