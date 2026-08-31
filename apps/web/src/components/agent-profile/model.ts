/** Agent 配置表单的默认值与纯转换规则，不包含 React 或网络请求。 */
import type {
  AgentProfile,
  AgentProfileInput,
  VerificationRule,
} from "../../types";

export const WIZARD_STEPS = [
  "基础",
  "模型与预算",
  "提示词与上下文",
  "工作流与验证",
  "预览保存",
] as const;

export type ProfilePreset = "standard" | "fast" | "deep";

export const PROFILE_PRESETS: ReadonlyArray<{
  id: ProfilePreset;
  label: string;
  description: string;
}> = [
  { id: "standard", label: "标准自主（推荐）", description: "规划、工具执行、验证与失败后调整，适合比赛演示。" },
  { id: "fast", label: "快速分析", description: "减少规划开销，适合简单问答和快速确认。" },
  { id: "deep", label: "深度验证", description: "更大的预算和更完整的重规划，适合复杂取证。" },
];

export const BUDGET_FIELDS: ReadonlyArray<{
  key: keyof AgentProfileInput["budget"];
  label: string;
  min: number;
  max: number;
  step?: number;
}> = [
  { key: "max_steps", label: "最大步骤", min: 1, max: 100 },
  { key: "max_model_calls", label: "模型调用", min: 1, max: 50 },
  { key: "max_tool_calls", label: "工具调用", min: 1, max: 50 },
  { key: "max_tokens", label: "最大 Token", min: 1, max: 200000 },
  { key: "max_model_cost", label: "最大模型费用", min: 0, max: 100000, step: 0.01 },
  { key: "max_duration_seconds", label: "总时长（秒）", min: 0.1, max: 3600, step: 0.1 },
  { key: "step_timeout_seconds", label: "单步超时（秒）", min: 0.1, max: 300, step: 0.1 },
];

/** 每次新建都返回独立对象，避免嵌套策略在多次编辑之间共享引用。 */
export function createEmptyProfile(): AgentProfileInput {
  return {
    name: "新的 Agent",
    description: "",
    run_mode: "normal",
    default_provider_id: null,
    fallback_provider_ids: [],
    tool_selection_mode: "all",
    tool_ids: [],
    user_prompt_template: "请处理以下任务：{task}",
    planning_strategy: "dynamic",
    budget: {
      max_steps: 60,
      max_model_calls: 20,
      max_tool_calls: 20,
      max_tokens: 120000,
      max_model_cost: 20,
      max_duration_seconds: 600,
      step_timeout_seconds: 180,
    },
    context_policy: {
      recent_message_limit: 20,
      include_thread_summary: true,
      include_run_summaries: true,
      include_memories: true,
      text_attachment_char_limit: 20000,
    },
    memory_policy: {
      enabled: true,
      persist_important_facts: false,
      max_facts: 100,
    },
    completion_mode: "evidence",
    validation_policy: {
      require_external_evidence: true,
      json_schema: null,
      evidence_rules: [],
    },
    intervention_policy: {
      normal_mode: "wait",
      competition_mode: "fail",
      max_requests: 2,
    },
    workflow: { preset: "verified" },
    report_template: "# {task}\n\n{observations}",
    enabled: true,
    is_default: false,
  };
}

/** 预设只覆盖行为与预算，不改名称、Provider、工具授权和用户自定义模板。 */
export function applyProfilePreset(
  form: AgentProfileInput,
  preset: ProfilePreset,
): AgentProfileInput {
  const common = {
    memory_policy: { ...form.memory_policy, enabled: true, persist_important_facts: false },
    intervention_policy: { ...form.intervention_policy, normal_mode: "wait" as const, competition_mode: "fail" as const, max_requests: 2 },
  };
  if (preset === "fast") {
    return {
      ...form,
      ...common,
      planning_strategy: "direct",
      completion_mode: "advisory",
      workflow: { preset: "direct" },
      budget: { ...form.budget, max_steps: 20, max_model_calls: 8, max_tool_calls: 8, max_tokens: 32_000, max_model_cost: 10, max_duration_seconds: 180, step_timeout_seconds: 90 },
    };
  }
  if (preset === "deep") {
    return {
      ...form,
      ...common,
      planning_strategy: "hybrid",
      completion_mode: "evidence",
      workflow: { preset: "verified" },
      budget: { ...form.budget, max_steps: 80, max_model_calls: 30, max_tool_calls: 30, max_tokens: 200_000, max_model_cost: 40, max_duration_seconds: 900, step_timeout_seconds: 240 },
    };
  }
  return {
    ...form,
    ...common,
    planning_strategy: "dynamic",
    completion_mode: "evidence",
    workflow: { preset: "verified" },
    budget: { ...form.budget, max_steps: 60, max_model_calls: 20, max_tool_calls: 20, max_tokens: 120_000, max_model_cost: 20, max_duration_seconds: 600, step_timeout_seconds: 180 },
  };
}

/** 去掉服务端只读字段，得到可提交给创建/更新接口的输入。 */
export function profileToInput(value: AgentProfile): AgentProfileInput {
  const {
    profile_id: _id,
    version: _version,
    schema_version: _schema,
    created_at: _created,
    ...input
  } = value;
  void _id;
  void _version;
  void _schema;
  void _created;
  return input;
}

/** 将文本 Schema 合并回配置；JSON 无效时由调用方统一显示错误。 */
export function buildProfilePayload(
  form: AgentProfileInput,
  schemaText: string,
): AgentProfileInput {
  return {
    ...form,
    validation_policy: {
      ...form.validation_policy,
      json_schema: schemaText.trim() ? JSON.parse(schemaText) : null,
    },
  };
}

/**
 * 证据规则文本框只编辑正则规则。替换时保留 SHA-256 等不可由该文本框表达的
 * 规则，并尽量维持原有规则的相对位置，避免保存一次表单意外降低验证强度。
 */
export function replaceRegexEvidenceRules(
  existing: VerificationRule[],
  text: string,
): VerificationRule[] {
  const replacement = text
    .split("\n")
    .map((value) => value.trim())
    .filter(Boolean)
    .map((value) => ({ kind: "regex" as const, value }));
  let replacementIndex = 0;
  const merged = existing.flatMap((rule) => {
    if (rule.kind !== "regex") return [rule];
    const next = replacement[replacementIndex++];
    return next ? [next] : [];
  });
  return [...merged, ...replacement.slice(replacementIndex)];
}

/** 规划策略和工作流存在约束，在一个纯函数中同步调整可避免各表单写出冲突组合。 */
export function changePlanningStrategy(
  form: AgentProfileInput,
  strategy: AgentProfileInput["planning_strategy"],
): AgentProfileInput {
  return {
    ...form,
    planning_strategy: strategy,
    completion_mode:
      strategy === "direct" && form.completion_mode === "evidence"
        ? "advisory"
        : form.completion_mode,
    workflow: { preset: strategy === "direct" ? "direct" : "verified" },
  };
}

export function changeCompletionMode(
  form: AgentProfileInput,
  mode: AgentProfileInput["completion_mode"],
): AgentProfileInput {
  return {
    ...form,
    completion_mode: mode,
    planning_strategy:
      mode === "evidence" && form.planning_strategy === "direct"
        ? "dynamic"
        : form.planning_strategy,
    workflow:
      mode === "evidence" && form.workflow.preset === "direct"
        ? { preset: "verified" }
        : form.workflow,
  };
}

export function changeWorkflowPreset(
  form: AgentProfileInput,
  preset: AgentProfileInput["workflow"]["preset"],
): AgentProfileInput {
  return {
    ...form,
    workflow: { preset },
    planning_strategy:
      preset === "direct"
        ? "direct"
        : preset === "planned"
          ? "dynamic"
          : form.planning_strategy === "direct"
            ? "dynamic"
            : form.planning_strategy,
    completion_mode:
      preset === "direct" && form.completion_mode === "evidence"
        ? "advisory"
        : form.completion_mode,
  };
}
