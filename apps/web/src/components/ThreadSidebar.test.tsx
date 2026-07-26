import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ThreadSidebar from "./ThreadSidebar";
import type { Thread } from "../types";

const threads: Thread[] = [
  {
    id: "1",
    title: "漏洞分析",
    mode: "normal",
    interaction_mode: "chat",
    provider_config_id: null,
    provider_fallback_notice: null,
    tool_selection_mode: "inherit",
    tool_ids: [],
    agent_profile_id: null,
    agent_profile_version: null,
    plan_mode: "auto",
    archived: false,
    created_at: "",
    updated_at: "",
  },
  {
    id: "2",
    title: "历史任务",
    mode: "competition",
    interaction_mode: "agent",
    provider_config_id: null,
    provider_fallback_notice: null,
    tool_selection_mode: "inherit",
    tool_ids: [],
    agent_profile_id: null,
    agent_profile_version: null,
    plan_mode: "auto",
    archived: true,
    created_at: "",
    updated_at: "",
  },
];

describe("ThreadSidebar", () => {
  it("支持搜索、查看归档并传递管理意图", () => {
    const onRename = vi.fn();
    render(
      <ThreadSidebar
        threads={threads}
        onSelect={vi.fn()}
        onRename={onRename}
        onToggleArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByText("历史任务")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("显示已归档"));
    fireEvent.change(screen.getByLabelText("搜索任务"), {
      target: { value: "历史" },
    });
    expect(screen.getByText("历史任务")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("重命名 历史任务"));
    expect(onRename).toHaveBeenCalledWith(threads[1]);
  });

  it("无匹配结果时显示明确空状态", () => {
    render(
      <ThreadSidebar
        threads={threads}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onToggleArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("搜索任务"), {
      target: { value: "不存在的任务" },
    });
    expect(screen.getByText("没有匹配的任务。")).toBeInTheDocument();
  });

  it("渲染一百条历史任务时保留稳定行，不把标题或操作压缩到同一行", () => {
    const history = Array.from({ length: 100 }, (_, index) => ({
      ...threads[0],
      id: `history-${index}`,
      title: `第 ${index + 1} 条历史任务：用于验证长标题不会挤压操作区`,
    }));
    const { container } = render(
      <ThreadSidebar
        threads={history}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onToggleArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(container.querySelectorAll(".thread-row")).toHaveLength(100);
    expect(screen.getByText(/第 1 条历史任务/)).toBeInTheDocument();
    expect(screen.getByText(/第 100 条历史任务/)).toBeInTheDocument();
    expect(container.querySelector(".thread-list")).toHaveClass("thread-list");
  });
});
