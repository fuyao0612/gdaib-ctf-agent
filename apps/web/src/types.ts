/** 与后端公开 JSON 契约一一对应的工作台类型。 */
export type Mode = "normal" | "competition";
export type InteractionMode = "chat" | "agent";
export type PlanMode = "auto" | "approval";
export type SettingsMode = "beginner" | "advanced";
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
  interaction_mode: InteractionMode;
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
  created_at: string;
}
export interface Run {
  id: string;
  thread_id: string;
  status: RunStatus;
  provider: string;
  agent_profile_id: string | null;
  agent_profile_version: number | null;
  plan_mode: PlanMode;
  completion_mode: CompletionMode;
  validation_status: "pending" | "unverified" | "validated" | "failed";
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
export interface Report {
  markdown: string;
  data: Record<string, unknown>;
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
}
export type ProviderPreset = "deepseek" | "qwen" | "glm" | "custom";
export type StructuredMode =
  | "auto"
  | "json_schema"
  | "json_object"
  | "prompt_json";
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
  structured_mode: StructuredMode;
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
  structured_mode: StructuredMode;
  input_price_per_million: number;
  output_price_per_million: number;
  fallback_on: FallbackCategory[];
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
export interface ChatDefaults {
  default_provider_id: string | null;
  default_mode: InteractionMode;
  system_prompt: string;
  stream_enabled: boolean;
  recent_message_limit: number;
  context_token_limit: number;
  attachment_char_limit: number;
  sidebar_expanded: boolean;
  audit_expanded: boolean;
  theme: "light";
}
export type ChatEvent =
  | { type: "reply_start"; data: { request_id: string; user_message: Message } }
  | { type: "text_delta"; data: { text: string } }
  | { type: "reply_complete"; data: { message: Message } }
  | { type: "reply_failed"; data: { message: string; retryable: boolean } };
export type UnifiedMessageEvent =
  | ChatEvent
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
    provider: string;
    agent_profile_id: string | null;
    agent_profile_version: number | null;
    validation_status: string;
    evidence_level: string;
  };
  usage: Record<string, number>;
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
  checkpoints?: Array<{
    checkpoint_sequence: number;
    node: string;
    elapsed_seconds: number;
    created_at: string;
  }>;
}
