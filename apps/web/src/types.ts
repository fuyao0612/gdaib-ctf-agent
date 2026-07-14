/** 与后端公开 JSON 契约一一对应的工作台类型。 */
export type Mode = "normal" | "competition";
export type RunStatus =
  | "queued"
  | "running"
  | "waiting_input"
  | "completed"
  | "failed"
  | "stopped";
export interface Thread {
  id: string;
  title: string;
  mode: Mode;
  agent_profile_id: string | null;
  agent_profile_version: number | null;
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
  completion_mode: CompletionMode;
  validation_status: "pending" | "unverified" | "validated" | "failed";
  evidence_level: "none" | "model" | "structured" | "external";
  attempt: number;
  stop_requested: boolean;
  error?: string;
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
}
