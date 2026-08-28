import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import type { ProviderConfig } from "../types";
import CreateThreadDialog from "./CreateThreadDialog";

const provider: ProviderConfig = {
  id: "provider-1",
  name: "安全模型服务",
  preset: "custom",
  base_url: "https://provider.test/v1",
  model: "security-model",
  enabled: true,
  is_default: true,
  fallback_order: 0,
  timeout_seconds: 30,
  max_retries: 0,
  structured_mode: "json_object",
  tool_call_mode: "structured",
  input_price_per_million: 0,
  output_price_per_million: 0,
  resolved_structured_mode: "json_object",
  fallback_on: [],
  has_api_key: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  connection_status: "ok",
  last_tested_at: null,
  last_test_error: null,
  actual_model: "security-model",
};

function renderDialog(
  overrides: Partial<ComponentProps<typeof CreateThreadDialog>> = {},
) {
  const props: ComponentProps<typeof CreateThreadDialog> = {
    title: "授权漏洞分析",
    prompt: "分析已授权目标并给出可验证的修复建议。",
    scenario: "general",
    providerConfigId: provider.id,
    authorizedTarget: "",
    providers: [provider],
    busy: false,
    onTitleChange: vi.fn(),
    onPromptChange: vi.fn(),
    onScenarioChange: vi.fn(),
    onProviderChange: vi.fn(),
    onAuthorizedTargetChange: vi.fn(),
    onOpenSettings: vi.fn(),
    onCancel: vi.fn(),
    onSubmit: vi.fn(),
    ...overrides,
  };
  const view = render(<CreateThreadDialog {...props} />);
  return { ...view, props };
}

const blockedSubmitCases: Array<
  [string, Partial<ComponentProps<typeof CreateThreadDialog>>]
> = [
  ["没有 Provider", { providers: [], providerConfigId: "" }],
  ["正在启动", { busy: true }],
];

describe("新建任务启动页", () => {
  it("以 aria-pressed 标记当前安全场景并回传场景切换", () => {
    const { props } = renderDialog();
    const general = screen.getByRole("button", {
      name: "通用研判 从材料中识别风险并给出处置建议",
    });
    const ctf = screen.getByRole("button", {
      name: "CTF 题目 解码、取证、分析与 Flag 验证",
    });

    expect(general).toHaveAttribute("aria-pressed", "true");
    expect(ctf).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(ctf);
    expect(props.onScenarioChange).toHaveBeenCalledWith("ctf");
  });

  it("没有可用 Provider 时禁止启动并引导进入设置", () => {
    const { props } = renderDialog({
      providers: [],
      providerConfigId: "",
    });

    expect(screen.getByRole("button", { name: "创建并开始" })).toBeDisabled();
    const settingsButton = screen.getByRole("button", {
      name: "尚无可用模型，去设置中心配置",
    });
    fireEvent.click(settingsButton);
    expect(props.onOpenSettings).toHaveBeenCalledWith("providers");
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it("分别进入模型连接与 Skills、MCP 能力广场", () => {
    const { props } = renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "管理模型连接" }));
    fireEvent.click(
      screen.getByRole("button", { name: "打开 Skills 与 MCP 能力广场" }),
    );
    expect(props.onOpenSettings).toHaveBeenNthCalledWith(1, "providers");
    expect(props.onOpenSettings).toHaveBeenNthCalledWith(2, "marketplace");
  });

  it.each(blockedSubmitCases)("%s 时表单提交不能绕过启动保护", (_label, overrides) => {
    const { props } = renderDialog(overrides);
    const form = screen.getByLabelText("任务名称").closest("form");

    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it("配置完整时可启动，并回传模型、授权目标与取消操作", () => {
    const alternateProvider: ProviderConfig = {
      ...provider,
      id: "provider-2",
      name: "备用模型服务",
      model: "backup-model",
      actual_model: "backup-model",
      is_default: false,
    };
    const { props } = renderDialog({ providers: [provider, alternateProvider] });

    const start = screen.getByRole("button", { name: "创建并开始" });
    expect(start).toBeEnabled();
    fireEvent.change(screen.getByLabelText("本次使用的模型"), {
      target: { value: alternateProvider.id },
    });
    fireEvent.change(screen.getByLabelText("本次授权目标"), {
      target: { value: "https://authorized.example" },
    });
    fireEvent.click(screen.getByRole("button", { name: "返回当前任务" }));
    fireEvent.click(start);

    expect(props.onProviderChange).toHaveBeenCalledWith(alternateProvider.id);
    expect(props.onAuthorizedTargetChange).toHaveBeenCalledWith(
      "https://authorized.example",
    );
    expect(props.onCancel).toHaveBeenCalledTimes(1);
    expect(props.onSubmit).toHaveBeenCalledTimes(1);
  });

  it("所有 Lucide 装饰图标都不会污染控件的可访问名称", () => {
    const { container } = renderDialog();
    const icons = Array.from(container.querySelectorAll("svg"));

    expect(icons.length).toBeGreaterThan(0);
    for (const icon of icons) expect(icon).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("button", { name: "创建并开始" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回当前任务" })).toBeInTheDocument();
  });
});
