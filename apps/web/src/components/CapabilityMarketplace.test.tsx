import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import CapabilityMarketplace from "./CapabilityMarketplace";

describe("CapabilityMarketplace", () => {
  afterEach(() => vi.restoreAllMocks());

  it("可搜索并一键安装声明式 Skill", async () => {
    const create = vi.spyOn(api, "createSkill").mockResolvedValue({
      id: "skill-1",
      name: "应急响应时间线",
      description: "日志分析",
      prompt: "建立时间线",
      steps: [],
      checklist: [],
      enabled: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
    const changed = vi.fn(async () => undefined);
    render(<CapabilityMarketplace csrf="csrf" skills={[]} onSkillsChanged={changed} onConfigureMcp={vi.fn()} onNotice={vi.fn()} onError={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("搜索能力"), { target: { value: "时间线" } });
    expect(screen.getByText("应急响应时间线")).toBeInTheDocument();
    expect(screen.queryByText("CTF 证据化解题")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "一键安装" }));

    await waitFor(() => expect(create).toHaveBeenCalledWith("csrf", expect.objectContaining({ name: "应急响应时间线", enabled: true })));
    expect(changed).toHaveBeenCalledOnce();
  });

  it("MCP 卡片只打开受控配置草稿", () => {
    const configure = vi.fn();
    render(<CapabilityMarketplace csrf="csrf" skills={[]} onSkillsChanged={vi.fn()} onConfigureMcp={configure} onNotice={vi.fn()} onError={vi.fn()} />);

    fireEvent.click(screen.getByRole("tab", { name: "MCP 接入模板" }));
    expect(screen.getByText("远程 HTTPS MCP")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "开始配置" })[0]);
    expect(configure).toHaveBeenCalledWith(expect.objectContaining({ id: "remote-http", input: expect.objectContaining({ transport: "streamable_http", url: "" }) }));
  });
});
