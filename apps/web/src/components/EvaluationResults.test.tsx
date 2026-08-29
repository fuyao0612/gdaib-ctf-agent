import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import EvaluationResults from "./EvaluationResults";

const record = {
  id: "record-1",
  case_id: "intent-explicit-task",
  category: "意图判断",
  difficulty: "基础",
  provider: "本地 Provider",
  model: "test-model",
  attempt: 1,
  started_at: "2026-07-26T00:00:00Z",
  finished_at: "2026-07-26T00:00:01Z",
  duration_ms: 1000,
  model_calls: 2,
  tool_calls: 1,
  input_tokens: 20,
  output_tokens: 5,
  estimated_cost: 0.01,
  success: true,
  status: "passed" as const,
  submitted_flag: null,
  flag_verified: false,
  finish_reason: "断言全部通过",
  failure_category: null,
  run_id: "run-1",
  trace_path: "/api/v1/runs/run-1/events",
  report_path: "/api/v1/runs/run-1/report",
};

const retryRecord = { ...record, id: "record-2", attempt: 2, status: "failed" as const, success: false, duration_ms: 2000 };

describe("EvaluationResults", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("读取统计，按状态筛选，并默认折叠单条详情", async () => {
    const fetch = vi.fn(async (input: string) => {
      if (input.includes("/statistics")) {
        return Response.json({
          total: 1,
          passed: 1,
          failed: 0,
          skipped: 0,
          success_rate: 1,
          pass_at_1: 1,
          pass_at_3: 1,
          average_duration_ms: 1000,
          median_duration_ms: 1000,
          average_tokens: 25,
          average_cost: 0.01,
          average_tool_calls: 1,
          average_replans: 0,
          average_manual_interventions: 0,
          failure_categories: {},
        });
      }
      return Response.json([record]);
    });
    vi.stubGlobal("fetch", fetch);

    render(<EvaluationResults onError={vi.fn()} />);

    expect(await screen.findByText("intent-explicit-task")).toBeInTheDocument();
    expect(screen.getAllByText("100%")).toHaveLength(3);
    const summary = screen.getByText("intent-explicit-task");
    const row = summary.closest("details");
    expect(row).not.toHaveAttribute("open");
    fireEvent.click(summary);
    expect(row).toHaveAttribute("open");
    fireEvent.change(screen.getByLabelText("状态"), { target: { value: "passed" } });
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("status=passed"),
        expect.anything(),
      ),
    );
  });

  it("比较同一任务的多次持久化运行", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string) => Response.json(
      input.includes("/statistics")
        ? { total: 2, passed: 1, failed: 1, skipped: 0, success_rate: 0.5, pass_at_1: 0.5, pass_at_3: 1, average_duration_ms: 1500, median_duration_ms: 1500, average_tokens: 25, average_cost: 0.01, average_tool_calls: 1, average_replans: 0, average_manual_interventions: 0, failure_categories: {} }
        : [record, retryRecord],
    )));
    render(<EvaluationResults onError={vi.fn()} />);

    const selector = await screen.findByLabelText("比较同一任务的运行");
    fireEvent.change(selector, { target: { value: record.case_id } });
    const table = await screen.findByRole("table", { name: `${record.case_id} 运行比较` });
    expect(table).toHaveTextContent("2.0 秒");
  });
});
