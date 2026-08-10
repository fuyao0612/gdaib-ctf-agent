import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { KnowledgeDocument } from "../types";
import KnowledgeBaseSettings from "./KnowledgeBaseSettings";

const builtin: KnowledgeDocument = {
  id: "knowledge-1",
  title: "应急响应日志分析基线",
  source_uri: "builtin://knowledge/incident-response-v1",
  tags: ["IOC", "时间线"],
  scenarios: ["incident_response"],
  enabled: true,
  allow_provider_context: true,
  origin: "builtin",
  sha256: "a".repeat(64),
  size_chars: 800,
  chunk_count: 2,
  created_at: "2026-08-10T00:00:00Z",
  updated_at: "2026-08-10T00:00:00Z",
};

describe("KnowledgeBaseSettings", () => {
  afterEach(() => vi.restoreAllMocks());

  it("导入文档时显式保存 Provider 出站选择和场景", async () => {
    vi.spyOn(api, "knowledgeDocuments").mockResolvedValue([builtin]);
    const create = vi.spyOn(api, "createKnowledgeDocument").mockResolvedValue(builtin);
    const notice = vi.fn();
    render(<KnowledgeBaseSettings csrf="csrf" onNotice={notice} onError={vi.fn()} />);

    await screen.findByText("应急响应日志分析基线");
    fireEvent.change(screen.getByLabelText("文档标题"), { target: { value: "内部处置手册" } });
    fireEvent.change(screen.getByLabelText("适用场景"), { target: { value: "incident_response" } });
    fireEvent.change(screen.getByLabelText("文档正文"), { target: { value: "先保全证据，再构建事件时间线。" } });
    fireEvent.click(screen.getByLabelText("允许命中片段进入模型上下文"));
    fireEvent.click(screen.getByRole("button", { name: "导入并建立索引" }));

    await waitFor(() => expect(create).toHaveBeenCalledWith("csrf", expect.objectContaining({
      title: "内部处置手册",
      scenarios: ["incident_response"],
      allow_provider_context: true,
    })));
    expect(notice).toHaveBeenCalledWith("知识文档已切分并建立本地检索索引");
  });

  it("内置文档只能停用且可以关闭模型上下文", async () => {
    vi.spyOn(api, "knowledgeDocuments").mockResolvedValue([builtin]);
    const update = vi.spyOn(api, "updateKnowledgeDocument").mockResolvedValue({
      ...builtin,
      allow_provider_context: false,
    });
    render(<KnowledgeBaseSettings csrf="csrf" onNotice={vi.fn()} onError={vi.fn()} />);

    await screen.findByText("应急响应日志分析基线");
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    const toggles = screen.getAllByRole("checkbox");
    fireEvent.click(toggles[toggles.length - 1]);
    await waitFor(() => expect(update).toHaveBeenCalledWith(
      "csrf",
      "knowledge-1",
      { allow_provider_context: false },
    ));
  });
});
