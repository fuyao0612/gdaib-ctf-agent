import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrajectoryReplay } from "./TrajectoryReplay";

const valid = {
  schema_version: "2.0",
  execution_mode: "动态执行（未预生成固定计划）",
  steps: [{
    run_id: "r1", sequence: 1, call_id: "c1", goal: "读取附件", action_kind: "tool_call",
    action_summary: "调用文件检查", tool_id: "ctf.file", tool_name: "文件检查", arguments: {},
    observation_status: "success", observation_summary: "发现文本", preview: "ok", error: null,
    decision: "继续", artifact_ids: [], evidence_ids: [], started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:01Z", duration_ms: 1,
  }],
};

describe("TrajectoryReplay", () => {
  it("在浏览器本地校验并只读回放轨迹", async () => {
    render(<TrajectoryReplay />);
    fireEvent.click(screen.getByRole("button", { name: "导入轨迹" }));
    const input = screen.getByLabelText("选择轨迹 JSON 文件") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File([JSON.stringify(valid)], "trace.json", { type: "application/json" })] } });
    await waitFor(() => expect(screen.getByText("读取附件")).toBeInTheDocument());
    expect(screen.getByText(/只读回放/)).toBeInTheDocument();
  });

  it("拒绝不支持的轨迹版本", async () => {
    render(<TrajectoryReplay />);
    fireEvent.click(screen.getByRole("button", { name: "导入轨迹" }));
    fireEvent.change(screen.getByLabelText("选择轨迹 JSON 文件"), { target: { files: [new File(["{}"], "bad.json")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("schema 2.0");
  });
});
