import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ToolSelector from "./ToolSelector";
import type { ToolSpec } from "../types";

const tools: ToolSpec[] = [
  {
    id: "ctf.file_inspect",
    namespace: "ctf",
    name: "file_inspect",
    display_name: "文件检查",
    version: "1.0.0",
    author: "测试",
    source: "builtin",
    description: "检查附件",
    source_type: "builtin",
    capabilities: [],
    scenarios: [],
    risk: "low",
    permissions: [],
    requires_network: false,
    allowed_target_types: [],
    timeout_seconds: 5,
    error_codes: [],
    idempotent: true,
    artifact_types: [],
    input_schema: { type: "object" },
    output_schema: { type: "object" },
    config_schema: { type: "object" },
    min_platform_version: "0.5.0",
    max_platform_version: null,
    supports_cancellation: false,
    supports_progress: false,
    enabled: true,
    health: { status: "healthy", checked_at: "", last_error: null },
  },
  {
    id: "ctf.disabled",
    namespace: "ctf",
    name: "disabled",
    display_name: "已停用工具",
    version: "1.0.0",
    author: "测试",
    source: "builtin",
    description: "不应被选择",
    source_type: "builtin",
    capabilities: [],
    scenarios: [],
    risk: "low",
    permissions: [],
    requires_network: false,
    allowed_target_types: [],
    timeout_seconds: 5,
    error_codes: [],
    idempotent: true,
    artifact_types: [],
    input_schema: { type: "object" },
    output_schema: { type: "object" },
    config_schema: { type: "object" },
    min_platform_version: "0.5.0",
    max_platform_version: null,
    supports_cancellation: false,
    supports_progress: false,
    enabled: false,
    health: { status: "disabled", checked_at: "", last_error: null },
  },
];

describe("ToolSelector", () => {
  it("切换白名单并只报告已启用工具", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ToolSelector tools={tools} mode="inherit" value={[]} disabled={false} onChange={onChange} />,
    );

    fireEvent.change(screen.getByLabelText("本次任务工具范围"), {
      target: { value: "selected" },
    });
    expect(onChange).toHaveBeenCalledWith("selected", []);

    rerender(
      <ToolSelector tools={tools} mode="selected" value={[]} disabled={false} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "文件检查" }));
    expect(onChange).toHaveBeenLastCalledWith("selected", ["ctf.file_inspect"]);
    expect(screen.queryByText("已停用工具")).not.toBeInTheDocument();
  });
});
