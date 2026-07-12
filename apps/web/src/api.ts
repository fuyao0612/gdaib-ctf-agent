import type { AgentDefaults, Artifact, Event, ProviderConfig, ProviderConfigInput, Report, Run, Thread, ThreadDetail } from './types'

const API = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...init, headers: { ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...init?.headers } })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: { message: '请求失败' } }))
    throw new Error(body.error?.message ?? `HTTP ${response.status}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  listThreads: () => request<Thread[]>('/threads'),
  createThread: (title: string, mode: string) => request<Thread>('/threads', { method: 'POST', body: JSON.stringify({ title, mode }) }),
  detail: (id: string) => request<ThreadDetail>(`/threads/${id}`),
  message: (id: string, content: string, artifactIds: string[]) => request(`/threads/${id}/messages`, { method: 'POST', body: JSON.stringify({ content, artifact_ids: artifactIds }) }),
  upload: async (id: string, file: File) => { const form = new FormData(); form.append('upload', file); return request<Artifact>(`/threads/${id}/artifacts`, { method: 'POST', body: form }) },
  start: (id: string, providerConfigId: string, successPattern: string) => request<Run>(`/threads/${id}/runs`, { method: 'POST', body: JSON.stringify({ provider_config_id: providerConfigId, verification_rules: [{ kind: 'regex', value: successPattern }] }) }),
  stop: (id: string) => request<Run>(`/runs/${id}/stop`, { method: 'POST' }),
  retry: (id: string) => request<Run>(`/runs/${id}/retry`, { method: 'POST' }),
  events: (id: string) => request<Event[]>(`/runs/${id}/events`),
  report: (id: string) => request<Report>(`/runs/${id}/report`),
  reportUrl: (id: string, format: 'md' | 'json') => `${API}/runs/${id}/report.${format}`,
  artifactUrl: (id: string) => `${API}/artifacts/${id}/download`,
  listProviders: () => request<ProviderConfig[]>('/providers'),
  providerPresets: () => request<Record<string, { base_url: string; model: string }>>('/provider-presets'),
  adminProviders: (token: string) => request<ProviderConfig[]>('/admin/settings/providers', { headers: { Authorization: `Bearer ${token}` } }),
  createProvider: (token: string, value: ProviderConfigInput) => request<ProviderConfig>('/admin/settings/providers', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify(value) }),
  updateProvider: (token: string, id: string, value: ProviderConfigInput) => request<ProviderConfig>(`/admin/settings/providers/${id}`, { method: 'PUT', headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify(value) }),
  deleteProvider: (token: string, id: string) => request<void>(`/admin/settings/providers/${id}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } }),
  testProvider: (token: string, id: string) => request<{ status: string }>(`/admin/settings/providers/${id}/test`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } }),
  discoverProviderModels: (token: string, id: string) => request<{ models: string[]; manual_model_supported: boolean }>(`/admin/settings/providers/${id}/models`, { headers: { Authorization: `Bearer ${token}` } }),
  agentDefaults: (token: string) => request<AgentDefaults>('/admin/settings/agent', { headers: { Authorization: `Bearer ${token}` } }),
  saveAgentDefaults: (token: string, value: AgentDefaults) => request<AgentDefaults>('/admin/settings/agent', { method: 'PUT', headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify(value) }),
}
