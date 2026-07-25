import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ProviderConfig, ProviderDeletionImpact } from "../types";
import ProviderSettings from "./ProviderSettings";

const provider: ProviderConfig = {
  id: "provider-1",
  name: "会话模型",
  preset: "custom",
  base_url: "https://provider.test/v1",
  model: "chat-model",
  enabled: true,
  is_default: false,
  fallback_order: 1,
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
  actual_model: "chat-model",
};

const safeImpact: ProviderDeletionImpact = {
  id: provider.id,
  name: provider.name,
  model: provider.model,
  affected_thread_count: 1,
  fallback_provider: {
    id: "provider-default",
    name: "全局默认",
    model: "default-model",
  },
  blocking_reasons: [],
};

function renderSettings() {
  const onRefresh = vi.fn().mockResolvedValue(undefined);
  const onChanged = vi.fn().mockResolvedValue(undefined);
  const onNotice = vi.fn();
  const onError = vi.fn();
  render(
    <ProviderSettings
      csrf="csrf-test"
      providers={[provider]}
      mode="beginner"
      onRefresh={onRefresh}
      onChanged={onChanged}
      onNotice={onNotice}
      onError={onError}
    />,
  );
  return { onRefresh, onChanged, onNotice, onError };
}

describe("Provider 删除确认", () => {
  afterEach(() => vi.restoreAllMocks());

  it("普通设置中可发现删除入口，并在两次确认后才删除", async () => {
    vi.spyOn(api, "providerPresets").mockResolvedValue({});
    vi.spyOn(api, "providerDeletionImpact").mockResolvedValue(safeImpact);
    const remove = vi.spyOn(api, "deleteProvider").mockResolvedValue(undefined);
    const { onChanged, onRefresh } = renderSettings();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(
      await screen.findByRole("dialog", { name: "删除模型配置" }),
    ).toBeInTheDocument();
    expect(screen.getByText("会话模型 · chat-model")).toBeInTheDocument();
    expect(screen.getByText(/1 个会话会回退到 全局默认 · default-model/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "我已了解影响，继续" }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认永久删除" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("csrf-test", provider.id));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("有阻断引用时显示原因且不发送删除请求", async () => {
    vi.spyOn(api, "providerPresets").mockResolvedValue({});
    vi.spyOn(api, "providerDeletionImpact").mockResolvedValue({
      ...safeImpact,
      blocking_reasons: ["仍被 1 个活动 Run 使用"],
    });
    const remove = vi.spyOn(api, "deleteProvider").mockResolvedValue(undefined);
    renderSettings();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("当前不能安全删除");
    expect(screen.getByText("仍被 1 个活动 Run 使用")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认永久删除" })).not.toBeInTheDocument();
    expect(remove).not.toHaveBeenCalled();
  });

  it("影响查询失败时把 409 或 404 错误反馈给设置页", async () => {
    vi.spyOn(api, "providerPresets").mockResolvedValue({});
    vi.spyOn(api, "providerDeletionImpact").mockRejectedValue(
      new Error("HTTP 409：仍被活动 Run 使用"),
    );
    const { onError } = renderSettings();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() =>
      expect(onError).toHaveBeenCalledWith("Error: HTTP 409：仍被活动 Run 使用"),
    );
    expect(screen.queryByRole("dialog", { name: "删除模型配置" })).not.toBeInTheDocument();
  });
});
