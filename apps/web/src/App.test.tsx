import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

class FakeEventSource {
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = 1;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror = null;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  close() {
    this.readyState = 2;
  }
}

describe("App", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    const stored = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
      clear: () => stored.clear(),
    });
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
              agent: true,
            },
            version: "0.4.2",
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

  it("刷新后为运行中的任务恢复事件订阅", async () => {
    const now = new Date().toISOString();
    window.localStorage.setItem("yuwang.currentThreadId", "t-running");
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/setup/status"))
        return Response.json({
          configured: true,
          checks: {
            database: true,
            master_key: true,
            admin: true,
            provider: true,
            agent: true,
          },
          version: "0.4.2",
        });
      if (url.endsWith("/admin/session"))
        return Response.json({
          authenticated: true,
          csrf_token: "csrf-test",
          expires_at: null,
        });
      if (url.endsWith("/providers")) return Response.json([]);
      if (url.endsWith("/agent-profiles"))
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
      if (url.endsWith("/threads"))
        return Response.json([
          {
            id: "t-running",
            title: "恢复中的任务",
            mode: "normal",
            agent_profile_id: "a1",
            agent_profile_version: 1,
            archived: false,
            created_at: now,
            updated_at: now,
          },
        ]);
      if (url.endsWith("/threads/t-running/memories"))
        return Response.json([]);
      if (url.endsWith("/threads/t-running"))
        return Response.json({
          id: "t-running",
          title: "恢复中的任务",
          mode: "normal",
          agent_profile_id: "a1",
          agent_profile_version: 1,
          archived: false,
          messages: [],
          runs: [
            {
              id: "r-running",
              thread_id: "t-running",
              status: "running",
              provider: "测试 Provider",
              agent_profile_id: "a1",
              agent_profile_version: 1,
              completion_mode: "advisory",
              validation_status: "pending",
              evidence_level: "none",
              attempt: 1,
              stop_requested: false,
              created_at: now,
              started_at: now,
              finished_at: null,
            },
          ],
          artifacts: [],
          created_at: now,
          updated_at: now,
        });
      if (url.endsWith("/runs/r-running/events")) return Response.json([]);
      if (url.endsWith("/runs/r-running/audit"))
        return Response.json({
          run: {
            provider: "测试 Provider",
            agent_profile_id: "a1",
            agent_profile_version: 1,
            validation_status: "pending",
            evidence_level: "none",
          },
          usage: {},
          limits: {},
          profile: null,
          model_calls: [],
          tool_calls: [],
          evidence: [],
          checkpoints: [],
        });
      return Response.json({});
    });

    render(<App />);

    expect(await screen.findByTestId("thread-heading")).toHaveTextContent(
      "恢复中的任务",
    );
    await waitFor(() =>
      expect(
        FakeEventSource.instances.some(
          (source) => source.url === "/api/v1/runs/r-running/events/stream",
        ),
      ).toBe(true),
    );
  });
});
