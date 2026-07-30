/** 与后端公开 JSON 契约一一对应的工作台类型。 */
export type Mode = "normal" | "competition";
/** @deprecated 历史数据兼容字段；新建任务不再选择或使用交互模式。 */
export type InteractionMode = "chat" | "agent";
export type PlanMode = "auto" | "approval";
export type SettingsMode = "beginner" | "advanced";
export type ToolSourceType = "builtin" | "python_plugin" | "mcp";
export type ToolRisk = "low" | "medium" | "high";
export type ToolHealthStatus = "healthy" | "degraded" | "unavailable" | "disabled";
export type ProfileToolSelectionMode = "all" | "selected";
export type ThreadToolSelectionMode = "inherit" | "selected";
export interface ToolSpec {
  id: string;
  namespace: string;
  name: string;
  display_name: string;
  version: string;
  author: string;
  source: string;
  source_type: ToolSourceType;
  description: string;
  capabilities: string[];
  scenarios: string[];
  risk: ToolRisk;
  permissions: string[];
  requires_network: boolean;
  allowed_target_types: string[];
  timeout_seconds: number;
  error_codes: string[];
  idempotent: boolean;
  artifact_types: string[];
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  config_schema: Record<string, unknown>;
  min_platform_version: string;
  max_platform_version: string | null;
  supports_cancellation: boolean;
  supports_progress: boolean;
  enabled: boolean;
  health: { status: ToolHealthStatus; checked_at: string; last_error: string | null };
}
export type McpTransport = "stdio" | "streamable_http";
export interface McpServerInput {
  name: string;
  transport: McpTransport;
  command: string | null;
  args: string[];
  url: string | null;
  auth_token?: string | null;
  enabled: boolean;
  connect_timeout_seconds: number;
  call_timeout_seconds: number;
  allowed_tools: string[];
  blocked_tools: string[];
}
export interface McpServerView extends Omit<McpServerInput, "auth_token"> {
  id: string;
  has_auth: boolean;
  health_status: "healthy" | "degraded" | "unavailable" | "disabled" | "untested";
  last_connected_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}
export interface McpDeletionImpact {
  id: string;
  name: string;
  active_run_count: number;
  historical_snapshot_count: number;
  blocking_reasons: string[];
}
export type RunStatus =
  | "queued"
  | "running"
  | "waiting_input"
  | "waiting_clarification"
  | "waiting_approval"
  | "paused"
  | "completed"
  | "failed"
  | "stopped";
export interface Thread {
  id: string;
  title: string;
  mode: Mode;
  interaction_mode?: InteractionMode;
  provider_config_id: string | null;
  provider_fallback_notice: string | null;
  skill_ids?: string[];
  tool_selection_mode: ThreadToolSelectionMode;
  tool_ids: string[];
  agent_profile_id: string | null;
  agent_profile_version: number | null;
  plan_mode: PlanMode;
  archived: boolean;
  created_at: string;
  updated_at: string;
}
export interface Message {
  id: string;
  role: "user" | "agent" | "assistant" | "system";
  content: string;
  artifact_ids: string[];
  run_id?: string | null;
  provider?: string | null;
  model?: string | null;
  model_is_fallback?: boolean;
  created_at: string;
}
export interface Run {
  id: string;
  thread_id: string;
  status: RunStatus;
  provider: string;
  model?: string | null;
  agent_profile_id: string | null;
  agent_profile_version: number | null;
  plan_mode: PlanMode;
  completion_mode: CompletionMode;
  validation_status: "pending" | "unverified" | "partial" | "validated" | "failed";
  evidence_level: "none" | "model" | "structured" | "external";
  attempt: number;
  stop_requested: boolean;
  error?: string;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
}
export interface Artifact {
  id: string;
  filename: string;
  size: number;
  mime_type: string;
  sha256: string;
  kind: string;
}
export interface Event {
  event_id: string;
  run_id: string;
  sequence: number;
  type: string;
  timestamp: string;
  summary: string;
  payload: Record<string, unknown>;
}
export interface ThreadDetail extends Thread {
  messages: Message[];
  runs: Run[];
  artifacts: Artifact[];
}
export interface FlagCandidate {
  candidate: string;
  source_call_id?: string;
  source_kind?: string;
  source_step?: number;
  /** schema 2.1：候选格式匹配结果。 */
  format_status?: string;
  /** schema 2.0 及以前的兼容字段。 */
  validation_status?: string;
  platform_verified: boolean;
  verification_summary?: string;
  location?: string;
  discovery_source?: string;
  verification_scope?: "none" | "format" | "deterministic_rule" | "platform";
  deterministic_validation_status?: "not_run" | "passed" | "failed";
  platform_validation_status?: "not_run" | "passed" | "failed";
}
export interface ReportData extends Record<string, unknown> {
  final_answer?: string;
  evidence?: string[];
  flag_candidates?: FlagCandidate[];
  failure_analysis?: { summary?: string; causes?: string[]; next_steps?: string[] };
}
export interface Report {
  markdown: string;
  data: ReportData;
}
export interface AgentPlan {
  summary: string;
  steps: string[];
  success_approach: string;
  expected_results: string[];
  verification_methods: string[];
  risks: string[];
  dependencies: string[];
}
export interface TaskBrief {
  id: string;
  run_id: string;
  version: number;
  original_request: string;
  goal: string;
  authorized_scope: string[];
  constraints: string[];
  success_criteria: string[];
  expected_output: string;
  known_information: string[];
  assumptions: string[];
  risks: string[];
  needs_clarification: boolean;
  clarification_questions: string[];
  source: "agent" | "user_clarification";
  created_at: string;
}
export interface PlanRevision {
  id: string;
  run_id: string;
  version: number;
  plan: AgentPlan;
  source: "agent_initial" | "user_edit" | "agent_replan";
  change_reason: string;
  based_on_version: number | null;
  created_at: string;
}
export interface RunGuidance {
  id: string;
  run_id: string;
  sequence: number;
  content: string;
  created_at: string;
  consumed_at: string | null;
  /** 任务已在最后一个安全检查点后结束时的明确结算时间。 */
  discarded_at?: string | null;
}
export interface RunControl {
  status: RunStatus;
  plan_mode: PlanMode;
  task_briefs: TaskBrief[];
  plans: PlanRevision[];
  guidance: RunGuidance[];
  approval?: { kind: "medium_risk_tool"; tool: string; risk: "medium" } | null;
}
export type ProviderPreset = "deepseek" | "qwen" | "glm" | "custom";
export type StructuredMode =
  | "auto"
  | "json_schema"
  | "json_object"
  | "prompt_json";
export type ToolCallMode = "structured" | "native" | "disabled";
export type FallbackCategory =
  | "rate_limit"
  | "timeout"
  | "invalid_output"
  | "service";
export interface ProviderConfig {
  id: string;
  name: string;
  preset: ProviderPreset;
  base_url: string;
  model: string;
  enabled: boolean;
  is_default: boolean;
  fallback_order: number | null;
  timeout_seconds: number;
  max_retries: number;
  context_window_tokens?: number | null;
  structured_mode: StructuredMode;
  tool_call_mode: ToolCallMode;
  input_price_per_million: number;
  output_price_per_million: number;
  resolved_structured_mode: string;
  fallback_on: FallbackCategory[];
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
  connection_status: "untested" | "ok" | "failed";
  last_tested_at: string | null;
  last_test_error: string | null;
  actual_model: string | null;
}
export interface ProviderConfigInput {
  name: string;
  preset: ProviderPreset;
  base_url: string;
  model: string;
  api_key?: string | null;
  enabled: boolean;
  is_default: boolean;
  fallback_order: number | null;
  timeout_seconds: number;
  max_retries: number;
  context_window_tokens?: number | null;
  structured_mode: StructuredMode;
  tool_call_mode: ToolCallMode;
  input_price_per_million: number;
  output_price_per_million: number;
  fallback_on: FallbackCategory[];
}
export interface ProviderDeletionImpact {
  id: string;
  name: string;
  model: string;
  affected_thread_count: number;
  fallback_provider: { id: string; name: string; model: string } | null;
  blocking_reasons: string[];
}
export interface SkillDefinition {
  id: string;
  name: string;
  description: string;
  prompt: string;
  steps: string[];
  checklist: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}
export interface SkillInput {
  name: string;
  description: string;
  prompt: string;
  steps: string[];
  checklist: string[];
  enabled: boolean;
}
export interface AgentDefaults {
  budget: {
    max_steps: number;
    max_model_calls: number;
    max_tool_calls: number;
    max_tokens: number;
    max_model_cost: number;
    max_duration_seconds: number;
    step_timeout_seconds: number;
  };
  provider_retry_budget: number;
  context_token_budget: number;
  observation_char_budget: number;
}
export type UnifiedMessageEvent =
  | { type: "execution_started"; data: { run: Run; user_message: Message } }
  | {
      type: "execution_stopped";
      data: { run?: Run; user_message?: Message | null };
    }
  | {
      type: "guidance_queued";
      data: { run: Run; guidance: RunGuidance | null; user_message: Message | null };
    }
  | { type: "input_received"; data: { run: Run; user_message: Message | null } }
  | {
      type: "clarification_received";
      data: { run: Run; user_message: Message | null };
    };
export type CompletionMode = "advisory" | "structured" | "evidence";
export interface AgentProfileSummary {
  profile_id: string;
  version: number;
  name: string;
  description: string;
  run_mode: Mode;
  completion_mode: CompletionMode;
  is_default: boolean;
}
export interface SetupStatus {
  configured: boolean;
  checks: {
    database: boolean;
    master_key: boolean;
    admin: boolean;
    provider: boolean;
    agent: boolean;
  };
  version: string;
}
export interface AgentProfileInput {
  name: string;
  description: string;
  run_mode: Mode;
  default_provider_id: string | null;
  fallback_provider_ids: string[];
  tool_selection_mode: ProfileToolSelectionMode;
  tool_ids: string[];
  user_prompt_template: string;
  planning_strategy: "dynamic" | "direct" | "hybrid";
  budget: AgentDefaults["budget"];
  context_policy: {
    recent_message_limit: number;
    include_thread_summary: boolean;
    include_run_summaries: boolean;
    include_memories: boolean;
    text_attachment_char_limit: number;
  };
  memory_policy: {
    enabled: boolean;
    persist_important_facts: boolean;
    max_facts: number;
  };
  completion_mode: CompletionMode;
  validation_policy: {
    require_external_evidence: boolean;
    json_schema: Record<string, unknown> | null;
    evidence_rules: VerificationRule[];
  };
  intervention_policy: {
    normal_mode: "wait" | "fail";
    competition_mode: "replan" | "fail";
    max_requests: number;
  };
  workflow: { preset: "direct" | "planned" | "verified" };
  report_template: string;
  enabled: boolean;
  is_default: boolean;
}

export interface VerificationRule {
  kind: "regex" | "sha256";
  value: string;
}
export interface AgentProfile extends AgentProfileInput {
  profile_id: string;
  version: number;
  schema_version: string;
  created_at: string;
}
export interface MemoryRecord {
  id: string;
  thread_id: string;
  kind: string;
  content: string;
  enabled: boolean;
  source_run_id: string | null;
  created_at: string;
}
export interface RunAudit {
  run: {
    execution_status?: string;
    provider: string;
    agent_profile_id: string | null;
    agent_profile_version: number | null;
    validation_status: string;
    evidence_level: string;
  };
  usage: Record<string, number>;
  metrics?: Record<string, number | string>;
  history?: {
    model: string | null;
    started_at: string | null;
    finished_at: string | null;
    token_source: "provider" | "estimated" | "mixed" | "unavailable";
    cost_source: "provider" | "estimated" | "mixed" | "unavailable";
    manual_interventions: number;
    execution_status: string;
    validation_status: string;
  };
  limits: Record<string, number>;
  profile: {
    name: string;
    version: number;
    completion_mode: CompletionMode;
    planning_strategy: AgentProfileInput["planning_strategy"];
    workflow_preset: AgentProfileInput["workflow"]["preset"];
    default_provider_id: string | null;
    fallback_provider_ids: string[];
    context_policy: AgentProfileInput["context_policy"];
    memory_policy: AgentProfileInput["memory_policy"];
    intervention_policy: AgentProfileInput["intervention_policy"];
  } | null;
  model_calls?: Array<{
    id: string;
    provider: string;
    model: string;
    duration_ms: number;
    input_tokens: number;
    output_tokens: number;
    status: string;
    error_category: string | null;
    metadata: Record<string, unknown>;
  }>;
  tool_calls?: Array<{
    id: string;
    tool_name: string;
    result_summary: string | null;
    duration_ms: number;
    status: string;
    error: string | null;
  }>;
  evidence?: Array<{
    id: string;
    verified: boolean;
    verification_summary: string;
    location: string;
  }>;
  steps?: ExecutionStep[];
  checkpoints?: Array<{
    checkpoint_sequence: number;
    node: string;
    elapsed_seconds: number;
    created_at: string;
  }>;
}

export interface ExecutionStep {
  run_id: string;
  sequence: number;
  call_id: string | null;
  goal: string;
  action_kind: string;
  action_summary: string;
  action_reason?: string | null;
  tool_id: string | null;
  tool_name: string | null;
  arguments: Record<string, unknown>;
  observation_status: "running" | "success" | "error" | "timeout" | "blocked" | "stopped";
  observation_summary: string | null;
  observation_facts?: string[];
  observation_details?: Record<string, unknown>;
  reproduction_hint?: string | null;
  preview: string | null;
  error: string | null;
  decision: string | null;
  artifact_ids: string[];
  evidence_ids: string[];
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

export type EvaluationStatus = "passed" | "failed" | "skipped";

export interface EvaluationRecord {
  id: string;
  case_id: string;
  category: string;
  difficulty: string;
  provider: string | null;
  model: string | null;
  attempt: number;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  success: boolean;
  status: EvaluationStatus;
  submitted_flag: string | null;
  flag_verified: boolean;
  finish_reason: string;
  failure_category: string | null;
  run_id: string | null;
  trace_path: string | null;
  report_path: string | null;
}

export interface EvaluationStatistics {
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  success_rate: number;
  average_duration_ms: number;
  average_tokens: number;
  average_cost: number;
  failure_categories: Record<string, number>;
}
