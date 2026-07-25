import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ToolExtensionsCenter from "./ToolExtensionsCenter";

const tool = {
  id: "builtin.file_metadata",
  namespace: "builtin",
  name: "file_metadata",
  display_name: "文件元数据",
  version: "1.0.0",
  author: "御网智元",
  source: "builtin",
  source_type: "builtin",
  description: "读取受控附件元数据",
  capabilities: ["file"],
  scenarios: ["forensics"],
  risk: "low",
  permissions: ["artifact:read"],
  requires_network: false,
  allowed_target_types: ["artifact"],
  timeout_seconds: 5,
  error_codes: [],
  idempotent: true,
  artifact_types: [],
  input_schema: { type: "object" },
  output_schema: { type: "object" },
  config_schema: { type: "object" },
  supports_cancellation: false,
  supports_progress: false,
  enabled: true,
  health: { status: "healthy", checked_at: "2026-01-01T00:00:00Z", last_error: null },
};

const server = {
  id: "mcp-1",
  name: "本地测试 MCP",
  transport: "streamable_http",
  command: null,
  args: [],
  url: "https://mcp.example.test/mcp",
  has_auth: false,
  enabled: true,
  connect_timeout_seconds: 10,
  call_timeout_seconds: 30,
  allowed_tools: [],
  blocked_tools: [],
  health_status: "untested",
  last_connected_at: null,
  last_error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("ToolExtensionsCenter", () => {
  const onNotice = vi.fn();
  const onError = vi.fn();
  const onChanged = vi.fn(async () => undefined);

  beforeEach(() => {
    onNotice.mockClear();
    onError.mockClear();
    onChanged.mockClear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.endsWith("/tools")) return Response.json([tool]);
        if (input.endsWith("/mcp-servers/stdio-commands"))
          return Response.json({ commands: ["C:\\Python\\python.exe"] });
        if (input.endsWith("/mcp-servers") && !init?.method)
          return Response.json([server]);
        if (input.endsWith("/refresh") && init?.method === "POST")
          return Response.json({ tools: [tool] });
        if (input.endsWith("/deletion-impact"))
          return Response.json({
            id: server.id,
            name: server.name,
            active_run_count: 0,
            historical_snapshot_count: 2,
            blocking_reasons: [],
          });
        if (input.endsWith("/mcp-1") && init?.method === "PUT")
          return Response.json({ ...server, enabled: false });
        if (input.endsWith("/mcp-1") && init?.method === "DELETE")
          return new Response(null, { status: 204 });
        return Response.json({});
      }),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("展示统一工具，并能检查、停用和在影响检查后删除 MCP 服务", async () => {
    render(
      <ToolExtensionsCenter
        csrf="csrf"
        mode="advanced"
        onChanged={onChanged}
        onNotice={onNotice}
        onError={onError}
      />,
    );

    await screen.findByText("文件元数据");
    expect(screen.getByText("内置")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "检查并刷新" }));
    await waitFor(() =>
      expect(onNotice).toHaveBeenCalledWith("已检查“本地测试 MCP”，发现 1 个可用工具"),
    );

    fireEvent.click(screen.getByRole("button", { name: "停用" }));
    await waitFor(() =>
      expect(onNotice).toHaveBeenCalledWith("MCP 服务“本地测试 MCP”已停用"),
    );

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await screen.findByText("删除“本地测试 MCP”前检查");
    expect(screen.getByText(/历史工具快照：2/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() =>
      expect(onNotice).toHaveBeenCalledWith("MCP 服务“本地测试 MCP”已删除；历史 Run 快照保持不变"),
    );
    expect(onError).not.toHaveBeenCalled();
  });
});
