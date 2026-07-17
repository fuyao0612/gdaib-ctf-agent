import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
            version: "0.5.0",
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
            interaction_mode: "agent",
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
            interaction_mode: "agent",
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
      await screen.findByText("开始一段新对话"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("创建第一个对话"));
    fireEvent.change(screen.getByLabelText("默认回复方式"), {
      target: { value: "agent" },
    });
    await screen.findByRole("option", { name: /默认 Agent/ });
    fireEvent.change(screen.getByLabelText("执行限制"), {
      target: { value: "competition" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() =>
      expect(screen.getByText("测试任务")).toBeInTheDocument(),
    );
    expect(screen.getByText("竞赛限制")).toBeInTheDocument();
  });

  it("支持使用 Esc 关闭设置弹层", async () => {
    render(<App />);
    await screen.findByText("开始一段新对话");
    fireEvent.click(screen.getByRole("button", { name: /设置中心/ }));
    expect(
      screen.getByRole("dialog", { name: "设置中心" }),
    ).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(
      screen.queryByRole("dialog", { name: "设置中心" }),
    ).not.toBeInTheDocument();
  });

  it("新建任务默认选择计划确认", async () => {
    render(<App />);
    await screen.findByText("开始一段新对话");
    fireEvent.click(screen.getByText("创建第一个对话"));
    expect(screen.getByLabelText("默认回复方式")).toHaveValue("chat");
    expect(screen.queryByLabelText("计划控制")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("默认回复方式"), {
      target: { value: "agent" },
    });
    expect(screen.getByLabelText("计划控制")).toHaveValue("approval");
  });

  it("默认对话发送你好并显示自然语言回复且不创建 Run", async () => {
    const now = new Date().toISOString();
    const requestedUrls: string[] = [];
    let chatCompleted = false;
    window.localStorage.setItem("yuwang.currentThreadId", "t-chat");
    vi.mocked(fetch).mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        requestedUrls.push(url);
        if (url.endsWith("/setup/status"))
          return Response.json({ configured: true, checks: {}, version: "0.5.0" });
        if (url.endsWith("/admin/session"))
          return Response.json({ authenticated: true, csrf_token: "csrf-test" });
        if (url.endsWith("/providers"))
          return Response.json([
            {
              id: "provider-1",
              name: "千问",
              model: "qwen-test",
              enabled: true,
              is_default: true,
            },
          ]);
        if (url.endsWith("/agent-profiles")) return Response.json([]);
        if (url.endsWith("/threads"))
          return Response.json([
            {
              id: "t-chat",
              title: "新对话",
              mode: "normal",
              interaction_mode: "chat",
              agent_profile_id: null,
              agent_profile_version: null,
              plan_mode: "approval",
              archived: false,
              created_at: now,
              updated_at: now,
            },
          ]);
        if (url.endsWith("/threads/t-chat/memories")) return Response.json([]);
        if (url.endsWith("/threads/t-chat/chat") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          expect(body.content).toBe("你好");
          expect(body.provider_config_id).toBe("provider-1");
          expect(body).not.toHaveProperty("verification_rules");
          chatCompleted = true;
          const user = {
            id: "message-user",
            role: "user",
            content: "你好",
            artifact_ids: [],
            timestamp: now,
          };
          const assistant = {
            id: "message-assistant",
            role: "assistant",
            content: "你好，很高兴见到你。",
            artifact_ids: [],
            created_at: now,
          };
          const sse = [
            `event: reply_start\ndata: ${JSON.stringify({ request_id: body.request_id, user_message: user })}`,
            `event: text_delta\ndata: ${JSON.stringify({ text: "你好，很高兴见到你。" })}`,
            `event: reply_complete\ndata: ${JSON.stringify({ message: assistant })}`,
            "",
          ].join("\n\n");
          return new Response(sse, {
            headers: { "content-type": "text/event-stream" },
          });
        }
        if (url.endsWith("/threads/t-chat"))
          return Response.json({
            id: "t-chat",
            title: chatCompleted ? "你好" : "新对话",
            mode: "normal",
            interaction_mode: "chat",
            agent_profile_id: null,
            agent_profile_version: null,
            plan_mode: "approval",
            archived: false,
            messages: chatCompleted
              ? [
                  {
                    id: "message-user",
                    role: "user",
                    content: "你好",
                    artifact_ids: [],
                    created_at: now,
                  },
                  {
                    id: "message-assistant",
                    role: "assistant",
                    content: "你好，很高兴见到你。",
                    artifact_ids: [],
                    created_at: now,
                  },
                ]
              : [],
            runs: [],
            artifacts: [],
            created_at: now,
            updated_at: now,
          });
        return Response.json({});
      },
    );

    render(<App />);
    const input = await screen.findByLabelText("消息");
    expect(screen.getByRole("button", { name: "对话" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.queryByText("任务设置")).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "你好" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("你好，很高兴见到你。")).toBeInTheDocument();
    expect(requestedUrls.some((url) => url.endsWith("/turns"))).toBe(false);
    expect(screen.queryByTestId("run-progress")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "运行审计" })).not.toBeInTheDocument();
  });

  it("刷新后为排队任务恢复订阅并在启动事件后展示运行控制", async () => {
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
          version: "0.5.0",
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
            interaction_mode: "agent",
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
          interaction_mode: "agent",
          agent_profile_id: "a1",
          agent_profile_version: 1,
          archived: false,
          messages: [],
          runs: [
            {
              id: "r-running",
              thread_id: "t-running",
              status: "queued",
              provider: "测试 Provider",
              agent_profile_id: "a1",
              agent_profile_version: 1,
              completion_mode: "advisory",
              validation_status: "pending",
              evidence_level: "none",
              attempt: 1,
              stop_requested: false,
              created_at: now,
              started_at: null,
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
          (source) =>
            source.url === "/api/v1/runs/r-running/events/stream?after=0",
        ),
      ).toBe(true),
    );
    const source = FakeEventSource.instances.find((item) =>
      item.url.includes("/runs/r-running/events/stream"),
    );
    act(() =>
      source?.onmessage?.(
        new MessageEvent("message", {
          data: JSON.stringify({
            event_id: "event-started",
            run_id: "r-running",
            sequence: 1,
            type: "run_started",
            created_at: now,
            summary: "Agent 运行已开始",
            payload: {},
          }),
        }),
      ),
    );
    expect(
      await screen.findByRole("button", { name: "安全暂停" }),
    ).toBeEnabled();
    expect(screen.getByLabelText("追加指引")).toBeEnabled();
  });

  it("刷新后恢复暂停状态和按序指引且不重开事件订阅", async () => {
    const now = new Date().toISOString();
    window.localStorage.setItem("yuwang.currentThreadId", "t-paused");
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/setup/status"))
        return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session"))
        return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/providers")) return Response.json([]);
      if (url.endsWith("/agent-profiles")) return Response.json([]);
      if (url.endsWith("/threads"))
        return Response.json([
          {
            id: "t-paused",
            title: "暂停中的任务",
            mode: "normal",
            interaction_mode: "agent",
            agent_profile_id: null,
            agent_profile_version: null,
            plan_mode: "approval",
            archived: false,
            created_at: now,
            updated_at: now,
          },
        ]);
      if (url.endsWith("/threads/t-paused/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-paused"))
        return Response.json({
          id: "t-paused",
          title: "暂停中的任务",
          mode: "normal",
          interaction_mode: "agent",
          agent_profile_id: null,
          agent_profile_version: null,
          plan_mode: "approval",
          archived: false,
          messages: [],
          runs: [
            {
              id: "r-paused",
              thread_id: "t-paused",
              status: "paused",
              provider: "测试 Provider",
              agent_profile_id: null,
              agent_profile_version: null,
              plan_mode: "approval",
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
      if (url.endsWith("/runs/r-paused/events"))
        return Response.json([
          {
            event_id: "event-paused",
            run_id: "r-paused",
            sequence: 1,
            type: "run_paused",
            timestamp: now,
            summary: "运行已在安全检查点暂停",
            payload: {},
          },
        ]);
      if (url.endsWith("/runs/r-paused/audit"))
        return Response.json({
          run: { provider: "测试 Provider" },
          usage: {},
          limits: {},
          model_calls: [],
          tool_calls: [],
          evidence: [],
          checkpoints: [],
        });
      if (url.endsWith("/runs/r-paused/control"))
        return Response.json({
          status: "paused",
          plan_mode: "approval",
          task_briefs: [],
          plans: [],
          guidance: [
            {
              id: "guidance-1",
              run_id: "r-paused",
              sequence: 1,
              content: "刷新后仍需保留这条指引",
              created_at: now,
              consumed_at: null,
            },
          ],
        });
      return Response.json({});
    });

    render(<App />);

    expect(await screen.findByRole("button", { name: "从检查点继续" })).toBeEnabled();
    expect(screen.getByText("刷新后仍需保留这条指引")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "任务已暂停" })).toBeInTheDocument();
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
