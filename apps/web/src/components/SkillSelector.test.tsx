import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SkillDefinition } from "../types";
import SkillSelector from "./SkillSelector";

const skills: SkillDefinition[] = [
  {
    id: "skill-review",
    name: "代码评审",
    description: "检查变更风险",
    prompt: "按清单检查代码。",
    steps: ["阅读改动"],
    checklist: ["说明风险"],
    enabled: true,
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
  },
  {
    id: "skill-disabled",
    name: "已停用模板",
    description: "不应进入会话选择",
    prompt: "不会使用。",
    steps: [],
    checklist: [],
    enabled: false,
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
  },
];

describe("SkillSelector", () => {
  it("只展示已启用 Skill，并将选择结果交给会话更新", () => {
    const onChange = vi.fn();
    render(
      <SkillSelector
        skills={skills}
        value={[]}
        disabled={false}
        onChange={onChange}
      />,
    );

    expect(screen.getByText("代码评审")).toBeInTheDocument();
    expect(screen.queryByText("已停用模板")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "代码评审" }));
    expect(onChange).toHaveBeenCalledWith(["skill-review"]);
  });

  it("运行中禁用选择，避免影响活动 Run 的快照", () => {
    const onChange = vi.fn();
    render(
      <SkillSelector
        skills={skills}
        value={["skill-review"]}
        disabled
        onChange={onChange}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "代码评审" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "代码评审" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
