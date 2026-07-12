export type Mode = 'normal' | 'competition'
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'stopped'
export interface Thread { id: string; title: string; mode: Mode; archived: boolean; created_at: string; updated_at: string }
export interface Message { id: string; role: 'user' | 'agent' | 'system'; content: string; artifact_ids: string[]; created_at: string }
export interface Run { id: string; thread_id: string; status: RunStatus; provider: string; attempt: number; stop_requested: boolean; error?: string }
export interface Artifact { id: string; filename: string; size: number; mime_type: string; sha256: string; kind: string }
export interface Event { event_id: string; run_id: string; sequence: number; type: string; timestamp: string; summary: string; payload: Record<string, unknown> }
export interface ThreadDetail extends Thread { messages: Message[]; runs: Run[]; artifacts: Artifact[] }
export interface Report { markdown: string; data: Record<string, unknown> }
export type ProviderPreset = 'deepseek' | 'qwen' | 'glm' | 'custom'
export type StructuredMode = 'auto' | 'json_schema' | 'json_object' | 'prompt_json'
export type FallbackCategory = 'rate_limit' | 'timeout' | 'invalid_output' | 'service'
export interface ProviderConfig {
  id: string; name: string; preset: ProviderPreset; base_url: string; model: string
  enabled: boolean; is_default: boolean; fallback_order: number | null
  timeout_seconds: number; max_retries: number; structured_mode: StructuredMode
  input_price_per_million: number; output_price_per_million: number
  resolved_structured_mode: string; fallback_on: FallbackCategory[]
  has_api_key: boolean; created_at: string; updated_at: string
}
export interface ProviderConfigInput {
  name: string; preset: ProviderPreset; base_url: string; model: string; api_key?: string | null
  enabled: boolean; is_default: boolean; fallback_order: number | null
  timeout_seconds: number; max_retries: number; structured_mode: StructuredMode
  input_price_per_million: number; output_price_per_million: number
  fallback_on: FallbackCategory[]
}
export interface AgentDefaults {
  budget: { max_steps: number; max_model_calls: number; max_tool_calls: number; max_tokens: number; max_model_cost: number; max_duration_seconds: number; step_timeout_seconds: number }
  provider_retry_budget: number; context_token_budget: number; observation_char_budget: number
}
