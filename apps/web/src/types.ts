export type Mode = 'normal' | 'competition'
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'stopped'
export interface Thread { id: string; title: string; mode: Mode; archived: boolean; created_at: string; updated_at: string }
export interface Message { id: string; role: 'user' | 'agent' | 'system'; content: string; artifact_ids: string[]; created_at: string }
export interface Run { id: string; thread_id: string; status: RunStatus; provider: string; attempt: number; stop_requested: boolean; error?: string }
export interface Artifact { id: string; filename: string; size: number; mime_type: string; sha256: string; kind: string }
export interface Event { event_id: string; run_id: string; sequence: number; type: string; timestamp: string; summary: string; payload: Record<string, unknown> }
export interface ThreadDetail extends Thread { messages: Message[]; runs: Run[]; artifacts: Artifact[] }
export interface Report { markdown: string; data: Record<string, unknown> }
