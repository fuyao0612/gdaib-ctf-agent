import { describe, expect, it } from "vitest";
import type { AgentProfile } from "../../types";
import {
  buildProfilePayload,
  changeCompletionMode,
  changePlanningStrategy,
  changeWorkflowPreset,
  createEmptyProfile,
  profileToInput,
  replaceRegexEvidenceRules,
} from "./model";

describe("Agent 配置纯转换规则", () => {
  it("每次创建独立的嵌套配置", () => {
    const first = createEmptyProfile();
    const second = createEmptyProfile();
    first.context_policy.recent_message_limit = 1;
    expect(second.context_policy.recent_message_limit).toBe(20);
  });

  it("直接规划会同步为直接工作流并退出证据模式", () => {
    const result = changePlanningStrategy(createEmptyProfile(), "direct");
    expect(result.workflow.preset).toBe("direct");
    expect(result.completion_mode).toBe("advisory");
  });

  it("证据模式会修正不兼容的直接策略", () => {
    const direct = changePlanningStrategy(createEmptyProfile(), "direct");
    const result = changeCompletionMode(direct, "evidence");
    expect(result.planning_strategy).toBe("dynamic");
    expect(result.workflow.preset).toBe("verified");
  });

  it("直接工作流会修正证据模式", () => {
    const result = changeWorkflowPreset(createEmptyProfile(), "direct");
    expect(result.planning_strategy).toBe("direct");
    expect(result.completion_mode).toBe("advisory");
  });

  it("只把有效 JSON Schema 合并到提交对象", () => {
    const result = buildProfilePayload(
      { ...createEmptyProfile(), completion_mode: "structured" },
      '{"type":"object"}',
    );
    expect(result.validation_policy.json_schema).toEqual({ type: "object" });
    expect(() => buildProfilePayload(result, "{")).toThrow();
  });

  it("编辑输入不包含服务端版本字段", () => {
    const profile: AgentProfile = {
      ...createEmptyProfile(),
      profile_id: "profile-1",
      version: 2,
      schema_version: "1.0",
      created_at: "2026-07-13T00:00:00Z",
    };
    expect(profileToInput(profile)).not.toHaveProperty("profile_id");
    expect(profileToInput(profile).name).toBe("新的 Agent");
  });

  it("编辑证据正则时保留未编辑的 SHA-256 规则", () => {
    const result = replaceRegexEvidenceRules(
      [
        { kind: "regex", value: "FLAG\\{旧规则\\}" },
        { kind: "sha256", value: "a".repeat(64) },
        { kind: "regex", value: "旧的第二条" },
      ],
      "FLAG\\{新规则\\}\nTOKEN-[A-Z]+",
    );

    expect(result).toEqual([
      { kind: "regex", value: "FLAG\\{新规则\\}" },
      { kind: "sha256", value: "a".repeat(64) },
      { kind: "regex", value: "TOKEN-[A-Z]+" },
    ]);
  });
});
