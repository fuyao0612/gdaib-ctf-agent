import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

class FakeEventSource {
  static CLOSED = 2;
  readyState = 1;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror = null;
  constructor(public url: string) {}
  close() {
    this.readyState = 2;
  }
}

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.endsWith("/setup/status"))
          return Response.json({
            configured: true,
            checks: {
              database: true,
              master_key: true,
              admin: true,
              provider: true,
            },
            version: "0.4.0",
          });
        if (input.endsWith("/threads") && !init?.method)
          return Response.json([]);
        if (input.endsWith("/providers") && !init?.method)
          return Response.json([]);
        if (input.endsWith("/agent-profiles") && !input.includes("/admin/"))
          return Response.json([
            {
              profile_id: "a1",
              version: 1,
              name: "默认 Agent",
              description: "",
              run_mode: "normal",
              completion_mode: "advisory",
              is_default: true,
            },
          ]);
        if (input.endsWith("/threads/t1/memories")) return Response.json([]);
        if (input.endsWith("/provider-presets")) return Response.json({});
        if (input.endsWith("/threads") && init?.method === "POST")
          return Response.json({
            id: "t1",
            title: "测试任务",
            mode: "competition",
            agent_profile_id: "a1",
            agent_profile_version: 1,
            archived: false,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          });
        if (input.endsWith("/threads/t1"))
          return Response.json({
            id: "t1",
            title: "测试任务",
            mode: "competition",
            agent_profile_id: "a1",
            agent_profile_version: 1,
            archived: false,
            messages: [],
            runs: [],
            artifacts: [],
            created_at: "",
            updated_at: "",
          });
        return Response.json({});
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("creates and selects a competition thread", async () => {
    render(<App />);
    expect(
      await screen.findByText("从一个可审计的任务开始"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("创建第一个任务"));
    await screen.findByRole("option", { name: /默认 Agent/ });
    fireEvent.change(screen.getByLabelText("运行模式"), {
      target: { value: "competition" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() =>
      expect(screen.getByText("测试任务")).toBeInTheDocument(),
    );
    expect(screen.getAllByText("competition").length).toBeGreaterThan(0);
  });

  it("支持使用 Esc 关闭设置弹层", async () => {
    render(<App />);
    await screen.findByText("从一个可审计的任务开始");
    fireEvent.click(screen.getByRole("button", { name: /设置中心/ }));
    expect(
      screen.getByRole("dialog", { name: "设置中心" }),
    ).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(
      screen.queryByRole("dialog", { name: "设置中心" }),
    ).not.toBeInTheDocument();
  });
});
