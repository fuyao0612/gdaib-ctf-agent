import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsCenter from "./SettingsCenter";

const defaults = {
  budget: {
    max_steps: 20,
    max_model_calls: 8,
    max_tool_calls: 8,
    max_tokens: 8000,
    max_model_cost: 10,
    max_duration_seconds: 120,
    step_timeout_seconds: 15,
  },
  provider_retry_budget: 2,
  context_token_budget: 32000,
  observation_char_budget: 20000,
};

const defaultProfile = {
  profile_id: "agent-1",
  version: 1,
  schema_version: "1.0",
  created_at: "2026-07-14T00:00:00Z",
  name: "默认安全 Agent",
  description: "正式默认配置",
  run_mode: "normal",
  default_provider_id: null,
  fallback_provider_ids: [],
  user_prompt_template: "请处理以下任务：{task}",
  planning_strategy: "dynamic",
  budget: defaults.budget,
  context_policy: {
    recent_message_limit: 20,
    include_thread_summary: true,
    include_run_summaries: true,
    include_memories: true,
    text_attachment_char_limit: 20000,
  },
  memory_policy: {
    enabled: true,
    persist_important_facts: true,
    max_facts: 100,
  },
  completion_mode: "evidence",
  validation_policy: { require_external_evidence: true, json_schema: null },
  intervention_policy: {
    normal_mode: "wait",
    competition_mode: "fail",
    max_requests: 2,
  },
  workflow: { preset: "verified" },
  report_template: "# {task}\n\n{observations}",
  enabled: true,
  is_default: true,
};

describe("SettingsCenter", () => {
  const storageSet = vi.fn();
  beforeEach(() => {
    storageSet.mockClear();
    vi.stubGlobal("localStorage", {
      setItem: storageSet,
      getItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn(),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.endsWith("/provider-presets"))
          return Response.json({
            deepseek: {
              base_url: "https://api.deepseek.com",
              model: "deepseek-v4-flash",
            },
          });
        if (input.endsWith("/setup/status"))
          return Response.json({
            configured: false,
            checks: {
              database: true,
              master_key: true,
              admin: true,
              provider: false,
              agent: false,
            },
            version: "0.5.0",
          });
        if (
          input.endsWith("/admin/session") &&
          init?.method === "POST" &&
          init.body === JSON.stringify({ token: "wrong-token" })
        )
          return Response.json(
            { error: { message: "管理员鉴权失败" } },
            { status: 401 },
          );
        if (input.endsWith("/admin/session") && init?.method === "POST")
          return Response.json({
            csrf_token: "csrf-test",
            expires_at: Date.now() / 1000 + 3600,
          });
        if (input.endsWith("/admin/settings/providers") && !init?.method)
          return Response.json([]);
        if (input.endsWith("/admin/settings/skills") && !init?.method)
          return Response.json([]);
        if (input.endsWith("/admin/settings/agent-profiles") && !init?.method)
          return Response.json([defaultProfile]);
        if (input.endsWith("/admin/settings/agent") && !init?.method)
          return Response.json(defaults);
        if (
          input.endsWith("/admin/settings/providers") &&
          init?.method === "POST"
        )
          return Response.json(
            {
              id: "p1",
              name: "DeepSeek",
              preset: "deepseek",
              base_url: "https://api.deepseek.com",
              model: "deepseek-v4-flash",
              enabled: true,
              is_default: true,
              fallback_order: 0,
              timeout_seconds: 60,
              max_retries: 2,
              input_price_per_million: 0,
              output_price_per_million: 0,
              structured_mode: "auto",
              resolved_structured_mode: "json_object",
              fallback_on: ["timeout"],
              has_api_key: true,
              created_at: "",
              updated_at: "",
            },
            { status: 201 },
          );
        return Response.json({});
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the admin token in memory and creates a masked provider", async () => {
    const changed = vi.fn(async () => undefined);
    render(<SettingsCenter onClose={() => undefined} onChanged={changed} />);
    fireEvent.change(screen.getByLabelText("管理员令牌"), {
      target: { value: "admin-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "进入设置" }));
    await screen.findByText("模型 Provider");
    const keyInput = screen.getByPlaceholderText("输入 Provider API Key");
    expect(keyInput).toHaveAttribute("type", "password");
    fireEvent.change(keyInput, { target: { value: "provider-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "创建 Provider" }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(storageSet).not.toHaveBeenCalled();
    const providerRequest = vi
      .mocked(fetch)
      .mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/admin/settings/providers") &&
          init?.method === "POST",
      );
    const payload = JSON.parse(String(providerRequest?.[1]?.body));
    expect(payload).toMatchObject({
      name: "DeepSeek",
      enabled: true,
      is_default: true,
    });
  });

  it("新手和高级模式编辑同一份 Provider 表单数据", async () => {
    render(
      <SettingsCenter
        onClose={() => undefined}
        onChanged={async () => undefined}
      />,
    );
    fireEvent.change(screen.getByLabelText("管理员令牌"), {
      target: { value: "admin-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "进入设置" }));

    const keyInput = await screen.findByPlaceholderText("输入 Provider API Key");
    fireEvent.change(keyInput, { target: { value: "provider-secret" } });
    fireEvent.change(screen.getByLabelText("模型"), {
      target: { value: "deepseek-chat" },
    });
    expect(screen.queryByLabelText("超时（秒）")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "高级模式" }));
    expect(screen.getByLabelText("模型")).toHaveValue("deepseek-chat");
    fireEvent.change(screen.getByLabelText("超时（秒）"), {
      target: { value: "90" },
    });

    fireEvent.click(screen.getByRole("button", { name: "新手模式" }));
    expect(screen.getByLabelText("模型")).toHaveValue("deepseek-chat");
    fireEvent.click(screen.getByRole("button", { name: "创建 Provider" }));

    await waitFor(() => {
      const request = vi
        .mocked(fetch)
        .mock.calls.find(
          ([url, init]) =>
            String(url).endsWith("/admin/settings/providers") &&
            init?.method === "POST",
        );
      expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
        model: "deepseek-chat",
        timeout_seconds: 90,
      });
    });
  });

  it("管理员令牌错误时给出可操作提示且不泄露输入", async () => {
    render(
      <SettingsCenter
        onClose={() => undefined}
        onChanged={async () => undefined}
      />,
    );
    fireEvent.change(screen.getByLabelText("管理员令牌"), {
      target: { value: "wrong-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "进入设置" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("YUWANG_ADMIN_TOKEN");
    expect(alert).toHaveTextContent("重启服务");
    expect(alert).not.toHaveTextContent("wrong-token");
    expect(screen.queryByText("模型 Provider")).not.toBeInTheDocument();
  });
});
