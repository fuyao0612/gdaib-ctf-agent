import { describe, expect, it } from "vitest";
import type { ProviderConfig } from "../../types";
import {
  createEmptyProvider,
  explainProviderFailure,
  providerToInput,
  selectProviderPreset,
} from "./model";

describe("Provider 表单纯转换规则", () => {
  it("编辑配置不会回填 API Key", () => {
    const provider: ProviderConfig = {
      ...createEmptyProvider(),
      id: "provider-1",
      resolved_structured_mode: "json_object",
      has_api_key: true,
      created_at: "2026-07-13T00:00:00Z",
      updated_at: "2026-07-13T00:00:00Z",
      connection_status: "ok",
      last_tested_at: "2026-07-13T00:00:00Z",
      last_test_error: null,
      actual_model: "deepseek-v4-flash",
    };
    expect(providerToInput(provider).api_key).toBe("");
  });

  it("选择预设时同步 Base URL 和模型", () => {
    const result = selectProviderPreset(createEmptyProvider(), "qwen", {
      qwen: {
        base_url: "https://dashscope.example/v1",
        model: "qwen-tested",
      },
    });
    expect(result.preset).toBe("qwen");
    expect(result.base_url).toBe("https://dashscope.example/v1");
    expect(result.model).toBe("qwen-tested");
    expect(result.name).toBe("阿里云百炼 / 千问");
  });

  it.each([
    ["连接测试失败：模型鉴权失败，请检查 API Key", "API Key 错误"],
    ["连接测试失败：模型请求超时", "请求超时"],
    ["模型未返回符合配置的结构化 JSON", "服务不兼容结构化输出"],
    ["模型或接口不存在，请检查 Base URL", "API 地址或模型错误"],
  ])("把 Provider 错误转换为可操作提示", (message, expected) => {
    expect(explainProviderFailure(message)).toContain(expected);
  });
});
