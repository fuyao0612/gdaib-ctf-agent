import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ThreadSidebar from "./ThreadSidebar";
import type { Thread } from "../types";

const threads: Thread[] = [
  {
    id: "1",
    title: "漏洞分析",
    mode: "normal",
    scenario: "general",
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
    scenario: "ctf",
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
    const onToggleArchive = vi.fn();
    const onDelete = vi.fn();
    render(
      <ThreadSidebar
        threads={threads}
        onSelect={vi.fn()}
        onRename={onRename}
        onToggleArchive={onToggleArchive}
        onDelete={onDelete}
      />,
    );
    expect(screen.queryByText("历史任务")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "显示已归档任务" }));
    fireEvent.click(screen.getByRole("button", { name: "搜索任务" }));
    fireEvent.change(screen.getByRole("textbox", { name: "搜索任务" }), {
      target: { value: "历史" },
    });
    expect(screen.getByText("历史任务")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "管理任务 历史任务" }));
    expect(screen.getByRole("group", { name: "历史任务 操作" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    expect(onRename).toHaveBeenCalledWith(threads[1]);
    fireEvent.click(screen.getByRole("button", { name: "管理任务 历史任务" }));
    fireEvent.click(screen.getByRole("button", { name: "恢复任务" }));
    expect(onToggleArchive).toHaveBeenCalledWith(threads[1]);
    fireEvent.click(screen.getByRole("button", { name: "管理任务 历史任务" }));
    fireEvent.click(screen.getByRole("button", { name: "删除任务" }));
    expect(onDelete).toHaveBeenCalledWith(threads[1]);
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
    fireEvent.click(screen.getByRole("button", { name: "搜索任务" }));
    fireEvent.change(screen.getByRole("textbox", { name: "搜索任务" }), {
      target: { value: "不存在的任务" },
    });
    expect(screen.getByText("没有匹配的任务。")).toBeInTheDocument();
  });

  it("用 aria-current 标记当前项目任务", () => {
    render(
      <ThreadSidebar
        threads={threads}
        selectedId="1"
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onToggleArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { current: "page" })).toHaveAccessibleName(/漏洞分析/);
  });

  it("任务菜单消费 Esc，不会继续关闭底层工作台界面", () => {
    const underlayEscape = vi.fn();
    window.addEventListener("keydown", underlayEscape);
    render(
      <ThreadSidebar
        threads={threads}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onToggleArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "管理任务 漏洞分析" }));
    fireEvent.keyDown(screen.getByRole("button", { name: "重命名" }), { key: "Escape" });
    window.removeEventListener("keydown", underlayEscape);

    expect(screen.queryByRole("group", { name: "漏洞分析 操作" })).not.toBeInTheDocument();
    expect(underlayEscape).not.toHaveBeenCalled();
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
