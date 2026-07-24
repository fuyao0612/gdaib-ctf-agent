import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ToolSpec } from "../../types";
import ToolSelectionFields from "./ToolSelectionFields";
import { createEmptyProfile } from "./model";

const tool: ToolSpec = {
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
};

describe("ToolSelectionFields", () => {
  it("选中工具后保留明确的 Profile 白名单", () => {
    const onChange = vi.fn();
    const form = createEmptyProfile();
    render(<ToolSelectionFields form={form} tools={[tool]} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Agent 工具范围"), {
      target: { value: "selected" },
    });
    expect(onChange).toHaveBeenCalledWith({
      ...form,
      tool_selection_mode: "selected",
      tool_ids: [],
    });

    const selectedForm = { ...form, tool_selection_mode: "selected" as const };
    const onSelectedChange = vi.fn();
    render(
      <ToolSelectionFields form={selectedForm} tools={[tool]} onChange={onSelectedChange} />,
    );
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expect(onSelectedChange).toHaveBeenCalledWith({
      ...selectedForm,
      tool_ids: [tool.id],
    });
  });
});
