import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ThreadSidebar from "./ThreadSidebar";
import type { Thread } from "../types";

const threads: Thread[] = [
  {
    id: "1",
    title: "漏洞分析",
    mode: "normal",
    agent_profile_id: null,
    agent_profile_version: null,
    archived: false,
    created_at: "",
    updated_at: "",
  },
  {
    id: "2",
    title: "历史任务",
    mode: "competition",
    agent_profile_id: null,
    agent_profile_version: null,
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
    fireEvent.change(screen.getByLabelText("搜索对话"), {
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
    fireEvent.change(screen.getByLabelText("搜索对话"), {
      target: { value: "不存在的任务" },
    });
    expect(screen.getByText("没有匹配的任务。")).toBeInTheDocument();
  });
});
