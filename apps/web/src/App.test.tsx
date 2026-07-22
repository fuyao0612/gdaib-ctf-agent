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
});
