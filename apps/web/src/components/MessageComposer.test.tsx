import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ProviderConfig, Run, RunStatus } from "../types";
import MessageComposer from "./MessageComposer";

const provider: ProviderConfig = {
  id: "provider-1",
  name: "测试模型",
  preset: "custom",
  base_url: "https://provider.test/v1",
  model: "test-model",
  enabled: true,
  is_default: true,
  fallback_order: 0,
  timeout_seconds: 30,
  max_retries: 0,
  structured_mode: "json_object",
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
  actual_model: "test-model",
};

function run(status: RunStatus): Run {
  return {
    id: "run-1",
    thread_id: "thread-1",
    status,
    provider: "测试 Provider",
    agent_profile_id: "profile-1",
    agent_profile_version: 1,
    plan_mode: "auto",
    completion_mode: "evidence",
    validation_status: "pending",
    evidence_level: "none",
    attempt: 1,
    stop_requested: false,
  };
}

function renderComposer(status: RunStatus) {
  const onMessageChange = vi.fn();
  const onSend = vi.fn();
  render(
    <MessageComposer
      activeRun={run(status)}
      message="补充范围"
      pendingArtifacts={[]}
      providers={[provider]}
      providerConfigId={provider.id}
      uploading={false}
      chatGenerating={false}
      chatCanRetry={false}
      onMessageChange={onMessageChange}
      onProviderChange={vi.fn()}
      onUpload={vi.fn()}
      onSend={onSend}
      onStop={vi.fn()}
      onRetry={vi.fn()}
      onChatRetry={vi.fn()}
    />,
  );
  return { onMessageChange, onSend };
}

describe("统一消息输入框", () => {
  it("显示会话级已启用模型，并在切换时回传 Provider ID", () => {
    const onProviderChange = vi.fn();
    render(
      <MessageComposer
        activeRun={null}
        message=""
        pendingArtifacts={[]}
        providers={[provider]}
        providerConfigId={provider.id}
        uploading={false}
        chatGenerating={false}
        chatCanRetry={false}
        onMessageChange={vi.fn()}
        onProviderChange={onProviderChange}
        onUpload={vi.fn()}
        onSend={vi.fn()}
        onStop={vi.fn()}
        onRetry={vi.fn()}
        onChatRetry={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("当前对话模型")).toHaveValue(provider.id);
    expect(screen.getByRole("option", { name: /测试模型 · test-model（可用）/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("当前对话模型"), {
      target: { value: provider.id },
    });
    expect(onProviderChange).toHaveBeenCalledWith(provider.id);
  });

  it("运行中保持可编辑，并将发送语义标为追加指引", () => {
    const { onMessageChange, onSend } = renderComposer("running");
    const input = screen.getByLabelText("消息");
    expect(input).toBeEnabled();
    expect(screen.getByText(/作为追加指引按顺序应用/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "追加指引" })).toBeEnabled();
    fireEvent.change(input, { target: { value: "先核对附件" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onMessageChange).toHaveBeenCalledWith("先核对附件");
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("等待补充时仍保留同一输入框、附件和停止入口", () => {
    renderComposer("waiting_input");
    expect(screen.getByLabelText("消息")).toBeEnabled();
    expect(screen.getByLabelText("上传附件")).toBeEnabled();
    expect(screen.getByRole("button", { name: "补充并继续" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "停止任务" })).toBeEnabled();
  });

  it.each([
    ["waiting_clarification", "提交澄清"],
    ["waiting_approval", "追加计划反馈"],
    ["paused", "保存指引"],
  ] as const)("%s 状态保持一个输入框并显示正确发送语义", (status, label) => {
    renderComposer(status);
    expect(screen.getByLabelText("消息")).toBeEnabled();
    expect(screen.getByRole("button", { name: label })).toBeEnabled();
  });

  it("附件上传中仍可编辑消息，但会明确阻止按钮和 Enter 的过早发送", () => {
    const onSend = vi.fn();
    render(
      <MessageComposer
        activeRun={run("running")}
        message="等附件完成后发送"
        pendingArtifacts={[]}
        providers={[provider]}
        providerConfigId={provider.id}
        uploading
        chatGenerating={false}
        chatCanRetry={false}
        onMessageChange={vi.fn()}
        onProviderChange={vi.fn()}
        onUpload={vi.fn()}
        onSend={onSend}
        onStop={vi.fn()}
        onRetry={vi.fn()}
        onChatRetry={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("消息")).toBeEnabled();
    expect(screen.getByLabelText("上传附件")).toBeDisabled();
    expect(screen.getByRole("button", { name: "正在发送…" })).toBeDisabled();
    expect(screen.getByText(/附件正在上传/)).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText("消息"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("任务请求尚未返回时仍保留停止任务语义", () => {
    render(
      <MessageComposer
        activeRun={run("running")}
        message=""
        pendingArtifacts={[]}
        providers={[provider]}
        providerConfigId={provider.id}
        uploading={false}
        chatGenerating
        chatCanRetry={false}
        onMessageChange={vi.fn()}
        onProviderChange={vi.fn()}
        onUpload={vi.fn()}
        onSend={vi.fn()}
        onStop={vi.fn()}
        onRetry={vi.fn()}
        onChatRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "停止任务" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "停止生成" })).not.toBeInTheDocument();
  });

  it("停止入口不会遮住可重试回复，停止处理中会禁用重复停止", () => {
    const stopPending = { ...run("running"), stop_requested: true };
    render(
      <MessageComposer
        activeRun={stopPending}
        message=""
        pendingArtifacts={[]}
        providers={[provider]}
        providerConfigId={provider.id}
        uploading={false}
        chatGenerating={false}
        chatCanRetry
        onMessageChange={vi.fn()}
        onProviderChange={vi.fn()}
        onUpload={vi.fn()}
        onSend={vi.fn()}
        onStop={vi.fn()}
        onRetry={vi.fn()}
        onChatRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "重试回复" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "停止请求处理中" })).toBeDisabled();
    expect(screen.getByText(/停止请求处理中，仍在接收任务状态更新/)).toBeInTheDocument();
  });
});
