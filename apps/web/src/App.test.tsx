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

const now = new Date().toISOString();
const preferences = {
  default_provider_id: null,
  default_mode: "chat",
  system_prompt: "",
  stream_enabled: true,
  recent_message_limit: 12,
  context_token_limit: 4000,
  attachment_char_limit: 1000,
  sidebar_expanded: true,
  audit_expanded: false,
  theme: "light",
};

function thread(id: string, title: string) {
  return {
    id,
    title,
    mode: "normal",
    interaction_mode: "chat",
    agent_profile_id: null,
    agent_profile_version: null,
    plan_mode: "auto",
    archived: false,
    created_at: now,
    updated_at: now,
  };
}

function detail(id: string, title: string, overrides = {}) {
  return { ...thread(id, title), messages: [], runs: [], artifacts: [], ...overrides };
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
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/setup/status"))
          return Response.json({ configured: true, checks: {}, version: "0.5.0" });
        if (url.endsWith("/admin/session"))
          return Response.json({ authenticated: true, csrf_token: "csrf-test" });
        if (url.endsWith("/threads") && !init?.method) return Response.json([]);
        if (url.endsWith("/providers") || url.endsWith("/agent-profiles"))
          return Response.json([]);
        if (url.endsWith("/settings/chat")) return Response.json(preferences);
        if (url.endsWith("/threads") && init?.method === "POST")
          return Response.json(thread("t1", "测试任务"));
        if (url.endsWith("/threads/t1/memories")) return Response.json([]);
        if (url.endsWith("/threads/t1")) return Response.json(detail("t1", "测试任务"));
        return Response.json({});
      }),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("新建对话只要求名称，不展示模式选择", async () => {
    render(<App />);
    await screen.findByText("开始一段新对话");
    fireEvent.click(screen.getByText("创建第一个对话"));
    expect(screen.getByLabelText("对话名称")).toBeInTheDocument();
    expect(screen.queryByLabelText("默认回复方式")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    expect(await screen.findByText("测试任务")).toBeInTheDocument();
  });

  it("支持使用 Esc 关闭设置弹层", async () => {
    render(<App />);
    await screen.findByText("开始一段新对话");
    fireEvent.click(screen.getByRole("button", { name: /设置中心/ }));
    expect(screen.getByRole("dialog", { name: "设置中心" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "设置中心" })).not.toBeInTheDocument();
  });

  it("普通消息走统一入口并显示自然语言回复，不创建任务卡", async () => {
    window.localStorage.setItem("yuwang.currentThreadId", "t-chat");
    let completed = false;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/setup/status"))
        return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session"))
        return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/providers") || url.endsWith("/agent-profiles")) return Response.json([]);
      if (url.endsWith("/settings/chat")) return Response.json(preferences);
      if (url.endsWith("/threads")) return Response.json([thread("t-chat", "新对话")]);
      if (url.endsWith("/threads/t-chat/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-chat/message") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        expect(body.content).toBe("你好");
        expect(body).not.toHaveProperty("provider_config_id");
        completed = true;
        const user = { id: "u1", role: "user", content: "你好", artifact_ids: [], created_at: now };
        const assistant = { id: "a1", role: "assistant", content: "你好，很高兴见到你。", artifact_ids: [], created_at: now };
        const sse = [
          `event: reply_start\ndata: ${JSON.stringify({ request_id: body.request_id, user_message: user })}`,
          `event: text_delta\ndata: ${JSON.stringify({ text: assistant.content })}`,
          `event: reply_complete\ndata: ${JSON.stringify({ message: assistant })}`,
          "",
        ].join("\n\n");
        return new Response(sse, { headers: { "content-type": "text/event-stream" } });
      }
      if (url.endsWith("/threads/t-chat"))
        return Response.json(
          detail("t-chat", completed ? "你好" : "新对话", completed ? {
            messages: [
              { id: "u1", role: "user", content: "你好", artifact_ids: [], created_at: now },
              { id: "a1", role: "assistant", content: "你好，很高兴见到你。", artifact_ids: [], created_at: now },
            ],
          } : {}),
        );
      return Response.json({});
    });

    render(<App />);
    const input = await screen.findByLabelText("消息");
    expect(screen.queryByText("Agent 任务")).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "你好" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("你好，很高兴见到你。")).toBeInTheDocument();
    expect(screen.queryByTestId("run-progress")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "运行审计" })).not.toBeInTheDocument();
  });

  it("明确执行请求自动创建 Run 并订阅短进度", async () => {
    window.localStorage.setItem("yuwang.currentThreadId", "t-task");
    const run = {
      id: "r1", thread_id: "t-task", status: "queued", provider: "测试 Provider",
      agent_profile_id: "a1", agent_profile_version: 1, plan_mode: "auto",
      completion_mode: "advisory", validation_status: "pending", evidence_level: "none",
      attempt: 1, stop_requested: false, created_at: now, started_at: null, finished_at: null,
    };
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/setup/status")) return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session")) return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/providers") || url.endsWith("/agent-profiles")) return Response.json([]);
      if (url.endsWith("/settings/chat")) return Response.json(preferences);
      if (url.endsWith("/threads")) return Response.json([thread("t-task", "授权任务")]);
      if (url.endsWith("/threads/t-task/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-task/message") && init?.method === "POST") {
        const user = { id: "u-task", role: "user", content: "完成这道授权 CTF 题", artifact_ids: [], created_at: now };
        return new Response(
          `event: execution_started\ndata: ${JSON.stringify({ run, user_message: user })}\n\n`,
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (url.endsWith("/threads/t-task"))
        return Response.json(detail("t-task", "授权任务", { runs: [run] }));
      if (url.endsWith("/runs/r1/events")) return Response.json([]);
      if (url.endsWith("/runs/r1/audit"))
        return Response.json({ run: { provider: "测试 Provider" }, usage: {}, limits: {}, model_calls: [], tool_calls: [], evidence: [], checkpoints: [] });
      if (url.endsWith("/runs/r1/control")) return Response.json({ status: "queued", plan_mode: "auto", task_briefs: [], plans: [], guidance: [] });
      return Response.json({});
    });

    render(<App />);
    const input = await screen.findByLabelText("消息");
    fireEvent.change(input, { target: { value: "完成这道授权 CTF 题" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByTestId("run-progress")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "运行审计" })).toBeInTheDocument();
    await waitFor(() =>
      expect(FakeEventSource.instances.some((source) => source.url.includes("/runs/r1/events/stream"))).toBe(true),
    );
  });

  it("刷新界面偏好不会关闭用户已打开的运行审计", async () => {
    window.localStorage.setItem("yuwang.currentThreadId", "t-inspector");
    const run = {
      id: "r-inspector", thread_id: "t-inspector", status: "running", provider: "测试 Provider",
      agent_profile_id: "a1", agent_profile_version: 1, plan_mode: "auto",
      completion_mode: "advisory", validation_status: "pending", evidence_level: "none",
      attempt: 1, stop_requested: false, created_at: now, started_at: now, finished_at: null,
    };
    let publicPreferenceRequests = 0;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/setup/status")) return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session")) return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/admin/settings/providers") || url.endsWith("/admin/settings/agent-profiles")) return Response.json([]);
      if (url.endsWith("/admin/settings/agent")) return Response.json({});
      if (url.endsWith("/admin/settings/chat")) return Response.json(preferences);
      if (url.endsWith("/settings/chat")) {
        publicPreferenceRequests += 1;
        return Response.json(preferences);
      }
      if (url.endsWith("/threads")) return Response.json([thread("t-inspector", "审计抽屉")]);
      if (url.endsWith("/threads/t-inspector/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-inspector"))
        return Response.json(detail("t-inspector", "审计抽屉", { runs: [run] }));
      if (url.endsWith("/runs/r-inspector/events")) return Response.json([]);
      if (url.endsWith("/runs/r-inspector/audit"))
        return Response.json({ run: { provider: "测试 Provider" }, usage: {}, limits: {}, model_calls: [], tool_calls: [], evidence: [], checkpoints: [] });
      if (url.endsWith("/runs/r-inspector/control"))
        return Response.json({ status: "running", plan_mode: "auto", task_briefs: [], plans: [], guidance: [] });
      return Response.json({});
    });

    render(<App />);
    await screen.findByRole("button", { name: "运行审计" });
    fireEvent.click(screen.getByRole("button", { name: "运行审计" }));
    expect(document.querySelector(".inspector.open")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /设置中心/ }));
    const saveChat = await screen.findByRole("button", { name: "保存聊天设置" });
    fireEvent.click(saveChat);
    await waitFor(() => expect(publicPreferenceRequests).toBeGreaterThan(1));
    expect(document.querySelector(".inspector.open")).toBeInTheDocument();
  });

  it("运行中从主输入框按顺序追加指引，并把它们写入同一时间线", async () => {
    window.localStorage.setItem("yuwang.currentThreadId", "t-guidance");
    const run = {
      id: "r-guidance", thread_id: "t-guidance", status: "running", provider: "测试 Provider",
      agent_profile_id: "a1", agent_profile_version: 1, plan_mode: "auto",
      completion_mode: "advisory", validation_status: "pending", evidence_level: "none",
      attempt: 1, stop_requested: false, created_at: now, started_at: now, finished_at: null,
    };
    const messages: Array<Record<string, unknown>> = [];
    const guidance: Array<Record<string, unknown>> = [];
    const messageUrls: string[] = [];
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/setup/status")) return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session")) return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/providers") || url.endsWith("/agent-profiles")) return Response.json([]);
      if (url.endsWith("/settings/chat")) return Response.json(preferences);
      if (url.endsWith("/threads")) return Response.json([thread("t-guidance", "运行中纠偏")]);
      if (url.endsWith("/threads/t-guidance/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-guidance/message") && init?.method === "POST") {
        messageUrls.push(url);
        const body = JSON.parse(String(init.body));
        const user = {
          id: `u-${guidance.length + 1}`,
          role: "user",
          content: body.content,
          artifact_ids: [],
          created_at: now,
        };
        const item = {
          id: `g-${guidance.length + 1}`,
          run_id: "r-guidance",
          sequence: guidance.length + 1,
          content: body.content,
          created_at: now,
          consumed_at: null,
        };
        messages.push(user);
        guidance.push(item);
        return new Response(
          `event: guidance_queued\ndata: ${JSON.stringify({ run, guidance: item, user_message: user })}\n\n`,
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (url.endsWith("/threads/t-guidance"))
        return Response.json(detail("t-guidance", "运行中纠偏", { runs: [run], messages }));
      if (url.endsWith("/runs/r-guidance/events")) return Response.json([]);
      if (url.endsWith("/runs/r-guidance/audit"))
        return Response.json({ run: { provider: "测试 Provider" }, usage: {}, limits: {}, model_calls: [], tool_calls: [], evidence: [], checkpoints: [] });
      if (url.endsWith("/runs/r-guidance/control"))
        return Response.json({ status: "running", plan_mode: "auto", task_briefs: [], plans: [], guidance });
      return Response.json({});
    });

    render(<App />);
    const input = await screen.findByLabelText("消息");
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: "第一条：先核对证据来源" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("第一条：先核对证据来源")).toBeInTheDocument();
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: "第二条：保留原授权范围" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("第二条：保留原授权范围")).toBeInTheDocument();
    expect(messageUrls).toHaveLength(2);
    const timelineMessages = Array.from(document.querySelectorAll(".message.user"));
    expect(timelineMessages).toHaveLength(2);
    expect(timelineMessages.map((item) => item.textContent)).toEqual([
      expect.stringContaining("第一条：先核对证据来源"),
      expect.stringContaining("第二条：保留原授权范围"),
    ]);
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).not.toContain(
      "/api/v1/runs/r-guidance/guidance",
    );
  });

  it("输入停止通过统一入口立即显示终态，不调用旧停止路由", async () => {
    window.localStorage.setItem("yuwang.currentThreadId", "t-stop");
    const running = {
      id: "r-stop", thread_id: "t-stop", status: "running", provider: "测试 Provider",
      agent_profile_id: "a1", agent_profile_version: 1, plan_mode: "auto",
      completion_mode: "advisory", validation_status: "pending", evidence_level: "none",
      attempt: 1, stop_requested: false, created_at: now, started_at: now, finished_at: null,
    };
    const stopped = { ...running, status: "stopped", stop_requested: true, finished_at: now };
    const stopMessage = {
      id: "u-stop",
      role: "user",
      content: "停止",
      artifact_ids: [],
      created_at: now,
    };
    const requestedUrls: string[] = [];
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requestedUrls.push(url);
      if (url.endsWith("/setup/status")) return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session")) return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/providers") || url.endsWith("/agent-profiles")) return Response.json([]);
      if (url.endsWith("/settings/chat")) return Response.json(preferences);
      if (url.endsWith("/threads")) return Response.json([thread("t-stop", "停止任务")]);
      if (url.endsWith("/threads/t-stop/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-stop/message") && init?.method === "POST") {
        expect(JSON.parse(String(init.body)).content).toBe("停止");
        return new Response(
          `event: execution_stopped\ndata: ${JSON.stringify({ run: stopped, user_message: stopMessage })}\n\n`,
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (url.endsWith("/threads/t-stop"))
        return Response.json(detail("t-stop", "停止任务", {
          runs: [requestedUrls.some((value) => value.endsWith("/message")) ? stopped : running],
          messages: requestedUrls.some((value) => value.endsWith("/message")) ? [stopMessage] : [],
        }));
      if (url.endsWith("/runs/r-stop/events")) return Response.json([]);
      if (url.endsWith("/runs/r-stop/audit"))
        return Response.json({ run: { provider: "测试 Provider" }, usage: {}, limits: {}, model_calls: [], tool_calls: [], evidence: [], checkpoints: [] });
      if (url.endsWith("/runs/r-stop/control"))
        return Response.json({ status: "stopped", plan_mode: "auto", task_briefs: [], plans: [], guidance: [] });
      return Response.json({});
    });

    render(<App />);
    const input = await screen.findByLabelText("消息");
    fireEvent.change(input, { target: { value: "停止" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("已停止")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(screen.getAllByText("停止")).toHaveLength(1);
    expect(requestedUrls.some((url) => url.endsWith("/runs/r-stop/stop"))).toBe(false);
  });

  it("断线重试复用原 request_id，且运行停止入口不会遮住重试回复", async () => {
    window.localStorage.setItem("yuwang.currentThreadId", "t-retry");
    const running = {
      id: "r-retry", thread_id: "t-retry", status: "running", provider: "测试 Provider",
      agent_profile_id: "a1", agent_profile_version: 1, plan_mode: "auto",
      completion_mode: "advisory", validation_status: "pending", evidence_level: "none",
      attempt: 1, stop_requested: false, created_at: now, started_at: now, finished_at: null,
    };
    const requestBodies: Array<{ request_id: string; retry: boolean }> = [];
    const user = {
      id: "u-retry", role: "user", content: "保留范围", artifact_ids: [], created_at: now,
    };
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/setup/status")) return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session")) return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/providers") || url.endsWith("/agent-profiles")) return Response.json([]);
      if (url.endsWith("/settings/chat")) return Response.json(preferences);
      if (url.endsWith("/threads")) return Response.json([thread("t-retry", "可重试会话")]);
      if (url.endsWith("/threads/t-retry/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-retry/message") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        requestBodies.push(body);
        if (requestBodies.length === 1) throw new Error("连接中断");
        return new Response(
          `event: guidance_queued\ndata: ${JSON.stringify({ run: running, guidance: null, user_message: user })}\n\n`,
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (url.endsWith("/threads/t-retry"))
        return Response.json(detail("t-retry", "可重试会话", {
          runs: [running],
          messages: requestBodies.length > 1 ? [user] : [],
        }));
      if (url.endsWith("/runs/r-retry/events")) return Response.json([]);
      if (url.endsWith("/runs/r-retry/audit"))
        return Response.json({ run: { provider: "测试 Provider" }, usage: {}, limits: {}, model_calls: [], tool_calls: [], evidence: [], checkpoints: [] });
      if (url.endsWith("/runs/r-retry/control"))
        return Response.json({ status: "running", plan_mode: "auto", task_briefs: [], plans: [], guidance: [] });
      return Response.json({});
    });

    render(<App />);
    const input = await screen.findByLabelText("消息");
    fireEvent.change(input, { target: { value: "保留范围" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByRole("button", { name: "重试回复" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "停止任务" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "重试回复" }));
    await waitFor(() => expect(requestBodies).toHaveLength(2));
    expect(requestBodies[1]).toMatchObject({
      request_id: requestBodies[0].request_id,
      retry: true,
    });
    expect(await screen.findByText("保留范围")).toBeInTheDocument();
  });

  it("切换会话后忽略旧消息请求的迟到结果，也不把旧草稿变成新会话的重试", async () => {
    window.localStorage.setItem("yuwang.currentThreadId", "t-old");
    let resolveOldMessage: ((value: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/setup/status")) return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session")) return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/providers") || url.endsWith("/agent-profiles")) return Response.json([]);
      if (url.endsWith("/settings/chat")) return Response.json(preferences);
      if (url.endsWith("/threads"))
        return Response.json([thread("t-old", "旧会话"), thread("t-new", "新会话")]);
      if (url.endsWith("/threads/t-old/memories") || url.endsWith("/threads/t-new/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-old/message") && init?.method === "POST")
        return new Promise<Response>((resolve) => {
          resolveOldMessage = resolve;
        });
      if (url.endsWith("/threads/t-old")) return Response.json(detail("t-old", "旧会话"));
      if (url.endsWith("/threads/t-new")) return Response.json(detail("t-new", "新会话"));
      return Response.json({});
    });

    render(<App />);
    const input = await screen.findByLabelText("消息");
    fireEvent.change(input, { target: { value: "旧会话迟到回复" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(resolveOldMessage).toBeDefined());
    fireEvent.click(screen.getByText("新会话"));
    await waitFor(() =>
      expect(screen.getByTestId("thread-heading")).toHaveTextContent("新会话"),
    );

    resolveOldMessage?.(
      new Response(
        [
          `event: reply_start\ndata: ${JSON.stringify({ request_id: "old-request", user_message: { id: "old-user", role: "user", content: "旧会话迟到回复", artifact_ids: [], created_at: now } })}`,
          `event: reply_complete\ndata: ${JSON.stringify({ message: { id: "old-assistant", role: "assistant", content: "不应写入新会话", artifact_ids: [], created_at: now } })}`,
          "",
        ].join("\n\n"),
        { headers: { "content-type": "text/event-stream" } },
      ),
    );

    await waitFor(() => expect(screen.getByLabelText("消息")).toHaveValue(""));
    expect(screen.getByTestId("thread-heading")).toHaveTextContent("新会话");
    expect(screen.queryByText("旧会话迟到回复")).not.toBeInTheDocument();
    expect(screen.queryByText("不应写入新会话")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试回复" })).not.toBeInTheDocument();
  });

  it("快速连续切换时不会让较慢的旧会话详情覆盖最新选择", async () => {
    window.localStorage.setItem("yuwang.currentThreadId", "t-first");
    let resolveSlowDetail: ((value: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/setup/status")) return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session")) return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/providers") || url.endsWith("/agent-profiles")) return Response.json([]);
      if (url.endsWith("/settings/chat")) return Response.json(preferences);
      if (url.endsWith("/threads"))
        return Response.json([thread("t-first", "第一会话"), thread("t-slow", "慢会话")]);
      if (url.endsWith("/threads/t-first/memories") || url.endsWith("/threads/t-slow/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-first")) return Response.json(detail("t-first", "第一会话"));
      if (url.endsWith("/threads/t-slow"))
        return new Promise<Response>((resolve) => {
          resolveSlowDetail = resolve;
        });
      return Response.json({});
    });

    render(<App />);
    await screen.findByLabelText("消息");
    fireEvent.click(screen.getByText("慢会话"));
    await waitFor(() => expect(resolveSlowDetail).toBeDefined());
    fireEvent.click(screen.getByText("第一会话"));
    await waitFor(() =>
      expect(screen.getByTestId("thread-heading")).toHaveTextContent("第一会话"),
    );

    resolveSlowDetail?.(Response.json(detail("t-slow", "慢会话")));
    await waitFor(() =>
      expect(screen.getByTestId("thread-heading")).toHaveTextContent("第一会话"),
    );
  });

  it("停止响应为 running + stop_requested 时显示处理中并继续订阅终态", async () => {
    window.localStorage.setItem("yuwang.currentThreadId", "t-stop-pending");
    const running = {
      id: "r-stop-pending", thread_id: "t-stop-pending", status: "running", provider: "测试 Provider",
      agent_profile_id: "a1", agent_profile_version: 1, plan_mode: "auto",
      completion_mode: "advisory", validation_status: "pending", evidence_level: "none",
      attempt: 1, stop_requested: false, created_at: now, started_at: now, finished_at: null,
    };
    const stopPending = { ...running, stop_requested: true };
    const stopped = { ...stopPending, status: "stopped", finished_at: now };
    let stopRequestSent = false;
    let terminalEventReceived = false;
    const stopMessage = {
      id: "u-stop-pending", role: "user", content: "停止任务", artifact_ids: [], created_at: now,
    };
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/setup/status")) return Response.json({ configured: true, checks: {}, version: "0.5.0" });
      if (url.endsWith("/admin/session")) return Response.json({ authenticated: true, csrf_token: "csrf-test" });
      if (url.endsWith("/providers") || url.endsWith("/agent-profiles")) return Response.json([]);
      if (url.endsWith("/settings/chat")) return Response.json(preferences);
      if (url.endsWith("/threads")) return Response.json([thread("t-stop-pending", "停止处理中")]);
      if (url.endsWith("/threads/t-stop-pending/memories")) return Response.json([]);
      if (url.endsWith("/threads/t-stop-pending/message") && init?.method === "POST") {
        expect(JSON.parse(String(init.body)).content).toBe("停止任务");
        stopRequestSent = true;
        return new Response(
          `event: execution_stopped\ndata: ${JSON.stringify({ run: stopPending, user_message: stopMessage })}\n\n`,
          { headers: { "content-type": "text/event-stream" } },
        );
      }
      if (url.endsWith("/threads/t-stop-pending"))
        return Response.json(detail("t-stop-pending", "停止处理中", {
          runs: [terminalEventReceived ? stopped : stopRequestSent ? stopPending : running],
          messages: terminalEventReceived ? [stopMessage] : [],
        }));
      if (url.endsWith("/runs/r-stop-pending/events")) return Response.json([]);
      if (url.endsWith("/runs/r-stop-pending/audit"))
        return Response.json({ run: { provider: "测试 Provider" }, usage: {}, limits: {}, model_calls: [], tool_calls: [], evidence: [], checkpoints: [] });
      if (url.endsWith("/runs/r-stop-pending/control"))
        return Response.json({ status: terminalEventReceived ? "stopped" : "running", plan_mode: "auto", task_briefs: [], plans: [], guidance: [] });
      return Response.json({});
    });

    render(<App />);
    await screen.findByLabelText("消息");
    const isStopRunStream = (source: FakeEventSource) =>
      source.url.includes("/runs/r-stop-pending/events/stream");
    await waitFor(() =>
      expect(FakeEventSource.instances.some(isStopRunStream)).toBe(true),
    );
    const initialStream = FakeEventSource.instances.find(isStopRunStream);
    expect(initialStream).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "停止任务" }));
    expect(
      await screen.findByRole(
        "button",
        { name: "停止请求处理中" },
        { timeout: 5_000 },
      ),
    ).toBeDisabled();
    expect(screen.getByText(/仍在接收任务状态更新/)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        FakeEventSource.instances.some(
          (source) =>
            source !== initialStream &&
            source.readyState !== FakeEventSource.CLOSED &&
            isStopRunStream(source),
        ),
      ).toBe(true),
    );
    const resumedStream = FakeEventSource.instances.find(
      (source) =>
        source !== initialStream &&
        source.readyState !== FakeEventSource.CLOSED &&
        isStopRunStream(source),
    );
    expect(resumedStream).toBeDefined();

    terminalEventReceived = true;
    resumedStream?.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({
          event_id: "event-stop-pending",
          run_id: "r-stop-pending",
          sequence: 1,
          type: "run_stopped",
          timestamp: now,
          summary: "运行已停止",
          payload: {},
        }),
      }),
    );

    expect(await screen.findByTestId("result-stopped")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
