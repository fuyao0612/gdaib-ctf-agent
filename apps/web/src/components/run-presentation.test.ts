import { describe, expect, it } from "vitest";
import { presentActivities } from "./run-presentation";
import type { Event } from "../types";

const event = (type: string, summary: string, payload: Record<string, unknown> = {}): Event => ({
  event_id: `${type}-1`,
  run_id: "run-1",
  sequence: 1,
  type,
  timestamp: "2026-08-31T08:00:00Z",
  summary,
  payload,
});

describe("实时活动流公开映射", () => {
  it("将工具开始、返回和重规划映射为用户可读活动", () => {
    const activities = presentActivities([
      event("tool_started", "开始调用 ctf.ioc_extract", { tool: "ctf.ioc_extract" }),
      event("tool_finished", "发现 1 个有效 IOC", { tool: "ctf.ioc_extract", evidence_count: 1 }),
      event("replanned", "改用 IOC 提取和内容搜索", { reason: "避免重复执行同一无效路径" }),
    ]);

    expect(activities.map((item) => item.title)).toEqual([
      "正在调用工具",
      "工具返回结果",
      "已重新规划后续动作",
    ]);
    expect(activities[1].stage).toBe("观察结果");
    expect(activities[1].evidenceCount).toBe(1);
    expect(activities[1].publicDetails).toEqual({
      tool: "ctf.ioc_extract",
      evidence_count: 1,
    });
    expect(activities[2].status).toBe("info");
  });

  it("未知事件仍保留真实摘要，不猜测内部状态", () => {
    const [activity] = presentActivities([event("custom_event", "已记录公开活动")]);
    expect(activity.stage).toBe("执行过程");
    expect(activity.detail).toBe("已记录公开活动");
  });
});
