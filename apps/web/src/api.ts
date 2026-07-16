/** 浏览器 API 边界：统一凭据、CSRF、错误结构和 JSON 编解码。 */
import type {
  AgentDefaults,
  AgentProfile,
  AgentProfileInput,
  AgentProfileSummary,
  Artifact,
  Event,
  MemoryRecord,
  ProviderConfig,
  ProviderConfigInput,
  Report,
  Run,
  RunAudit,
  RunControl,
  AgentPlan,
  PlanRevision,
  SetupStatus,
  Thread,
  ThreadDetail,
} from "./types";

const API = "/api/v1";
const adminHeaders = (csrf: string) => ({ "X-CSRF-Token": csrf });
let sessionCsrf = "";
export const setSessionCsrf = (value: string) => {
  sessionCsrf = value;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 所有页面都经过此函数访问后端：输入是相对 API 路径和 fetch 参数，
  // 输出是后端契约类型 T。新增页面应复用这里，避免漏掉 Cookie、CSRF 或统一错误。
  const method = init?.method ?? "GET";
  const csrfHeaders =
    !["GET", "HEAD", "OPTIONS"].includes(method) && sessionCsrf
      ? adminHeaders(sessionCsrf)
      : {};
  const response = await fetch(`${API}${path}`, {
    ...init,
    credentials: "same-origin",
    headers: {
      ...(init?.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...csrfHeaders,
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ error: { message: "请求失败" } }));
    throw new Error(body.error?.message ?? `HTTP ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  setupStatus: () => request<SetupStatus>("/setup/status"),
  listThreads: () => request<Thread[]>("/threads"),
  listAgentProfiles: () => request<AgentProfileSummary[]>("/agent-profiles"),
  createThread: (
    title: string,
    mode: string,
    agentProfileId: string,
    planMode: "auto" | "approval",
  ) =>
    request<Thread>("/threads", {
      method: "POST",
      body: JSON.stringify({
        title,
        mode,
        agent_profile_id: agentProfileId,
        plan_mode: planMode,
      }),
    }),
  detail: (id: string) => request<ThreadDetail>(`/threads/${id}`),
  message: (id: string, content: string, artifactIds: string[]) =>
    request(`/threads/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, artifact_ids: artifactIds }),
    }),
  upload: async (id: string, file: File) => {
    const form = new FormData();
    form.append("upload", file);
    return request<Artifact>(`/threads/${id}/artifacts`, {
      method: "POST",
      body: form,
    });
  },
  start: (id: string, providerConfigId: string, successPattern: string) =>
    request<Run>(`/threads/${id}/runs`, {
      method: "POST",
      body: JSON.stringify({
        provider_config_id: providerConfigId || null,
        verification_rules: successPattern
          ? [{ kind: "regex", value: successPattern }]
          : [],
      }),
    }),
  turn: (
    id: string,
    content: string,
    artifactIds: string[],
    providerConfigId: string,
    successPattern: string,
  ) =>
    request<Run>(`/threads/${id}/turns`, {
      method: "POST",
      body: JSON.stringify({
        content,
        artifact_ids: artifactIds,
        provider_config_id: providerConfigId || null,
        verification_rules: successPattern
          ? [{ kind: "regex", value: successPattern }]
          : [],
      }),
    }),
  stop: (id: string) => request<Run>(`/runs/${id}/stop`, { method: "POST" }),
  retry: (id: string) => request<Run>(`/runs/${id}/retry`, { method: "POST" }),
  submitInput: (id: string, content: string) =>
    request<Run>(`/runs/${id}/input`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  control: (id: string) => request<RunControl>(`/runs/${id}/control`),
  submitClarification: (
    id: string,
    content: string,
    expectedBriefVersion: number,
    requestId: string,
  ) =>
    request<Run>(`/runs/${id}/clarification`, {
      method: "POST",
      body: JSON.stringify({
        content,
        expected_brief_version: expectedBriefVersion,
        request_id: requestId,
      }),
    }),
  editPlan: (
    id: string,
    plan: AgentPlan,
    expectedVersion: number,
    reason: string,
    requestId: string,
  ) =>
    request<PlanRevision>(`/runs/${id}/plan`, {
      method: "PUT",
      body: JSON.stringify({
        plan,
        expected_version: expectedVersion,
        reason,
        request_id: requestId,
      }),
    }),
  decidePlan: (
    id: string,
    decision: "approve" | "reject",
    expectedVersion: number,
    reason: string,
    requestId: string,
  ) =>
    request<Run>(`/runs/${id}/plan/${decision}`, {
      method: "POST",
      body: JSON.stringify({
        expected_version: expectedVersion,
        reason,
        request_id: requestId,
      }),
    }),
  events: (id: string) => request<Event[]>(`/runs/${id}/events`),
  audit: (id: string) => request<RunAudit>(`/runs/${id}/audit`),
  report: (id: string) => request<Report>(`/runs/${id}/report`),
  reportUrl: (id: string, format: "md" | "json") =>
    `${API}/runs/${id}/report.${format}`,
  artifactUrl: (id: string) => `${API}/artifacts/${id}/download`,
  memories: (id: string) => request<MemoryRecord[]>(`/threads/${id}/memories`),
  toggleMemories: (id: string, enabled: boolean) =>
    request<void>(`/threads/${id}/memories`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  clearMemories: (id: string) =>
    request<void>(`/threads/${id}/memories`, { method: "DELETE" }),
  deleteMemory: (threadId: string, memoryId: string) =>
    request<void>(`/threads/${threadId}/memories/${memoryId}`, {
      method: "DELETE",
    }),
  listProviders: () => request<ProviderConfig[]>("/providers"),
  providerPresets: () =>
    request<
      Record<
        string,
        {
          base_url: string;
          model: string;
          models?: string[];
          structured_modes?: string[];
        }
      >
    >("/provider-presets"),

  createAdminSession: (token: string) =>
    request<{ csrf_token: string; expires_at: number }>("/admin/session", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  adminSession: async () => {
    const value = await request<{
      authenticated: boolean;
      csrf_token: string;
      expires_at: number | null;
    }>("/admin/session");
    setSessionCsrf(value.csrf_token);
    return value;
  },
  deleteAdminSession: (csrf: string) =>
    request<void>("/admin/session", {
      method: "DELETE",
      headers: adminHeaders(csrf),
    }),
  updateThread: (id: string, value: { title?: string; archived?: boolean }) =>
    request<Thread>(`/threads/${id}`, {
      method: "PATCH",
      body: JSON.stringify(value),
    }),
  deleteThread: (id: string) =>
    request<void>(`/threads/${id}`, { method: "DELETE" }),
  adminProviders: (csrf: string) =>
    request<ProviderConfig[]>("/admin/settings/providers", {
      headers: adminHeaders(csrf),
    }),
  createProvider: (csrf: string, value: ProviderConfigInput) =>
    request<ProviderConfig>("/admin/settings/providers", {
      method: "POST",
      headers: adminHeaders(csrf),
      body: JSON.stringify(value),
    }),
  updateProvider: (csrf: string, id: string, value: ProviderConfigInput) =>
    request<ProviderConfig>(`/admin/settings/providers/${id}`, {
      method: "PUT",
      headers: adminHeaders(csrf),
      body: JSON.stringify(value),
    }),
  deleteProvider: (csrf: string, id: string) =>
    request<void>(`/admin/settings/providers/${id}`, {
      method: "DELETE",
      headers: adminHeaders(csrf),
    }),
  testProvider: (csrf: string, id: string) =>
    request<{
      status: string;
      model: string;
      structured_mode: string;
      latency_ms: number;
    }>(`/admin/settings/providers/${id}/test`, {
      method: "POST",
      headers: adminHeaders(csrf),
    }),
  discoverProviderModels: (csrf: string, id: string) =>
    request<{ models: string[]; manual_model_supported: boolean }>(
      `/admin/settings/providers/${id}/models`,
      { headers: adminHeaders(csrf) },
    ),
  agentDefaults: (csrf: string) =>
    request<AgentDefaults>("/admin/settings/agent", {
      headers: adminHeaders(csrf),
    }),
  saveAgentDefaults: (csrf: string, value: AgentDefaults) =>
    request<AgentDefaults>("/admin/settings/agent", {
      method: "PUT",
      headers: adminHeaders(csrf),
      body: JSON.stringify(value),
    }),
  adminProfiles: (csrf: string) =>
    request<AgentProfile[]>("/admin/settings/agent-profiles", {
      headers: adminHeaders(csrf),
    }),
  createProfile: (csrf: string, value: AgentProfileInput) =>
    request<AgentProfile>("/admin/settings/agent-profiles", {
      method: "POST",
      headers: adminHeaders(csrf),
      body: JSON.stringify(value),
    }),
  updateProfile: (csrf: string, id: string, value: AgentProfileInput) =>
    request<AgentProfile>(`/admin/settings/agent-profiles/${id}`, {
      method: "PUT",
      headers: adminHeaders(csrf),
      body: JSON.stringify(value),
    }),
  copyProfile: (csrf: string, id: string, name: string) =>
    request<AgentProfile>(`/admin/settings/agent-profiles/${id}/copy`, {
      method: "POST",
      headers: adminHeaders(csrf),
      body: JSON.stringify({ name }),
    }),
  profileVersions: (csrf: string, id: string) =>
    request<AgentProfile[]>(`/admin/settings/agent-profiles/${id}/versions`, {
      headers: adminHeaders(csrf),
    }),
  rollbackProfile: (csrf: string, id: string, version: number) =>
    request<AgentProfile>(
      `/admin/settings/agent-profiles/${id}/rollback/${version}`,
      { method: "POST", headers: adminHeaders(csrf) },
    ),
  defaultProfile: (csrf: string, id: string) =>
    request<AgentProfile>(`/admin/settings/agent-profiles/${id}/default`, {
      method: "POST",
      headers: adminHeaders(csrf),
    }),
  deleteProfile: (csrf: string, id: string) =>
    request<void>(`/admin/settings/agent-profiles/${id}`, {
      method: "DELETE",
      headers: adminHeaders(csrf),
    }),
  previewTemplate: (csrf: string, template: string) =>
    request<{ rendered: string }>(
      "/admin/settings/agent-profiles/template-preview",
      {
        method: "POST",
        headers: adminHeaders(csrf),
        body: JSON.stringify({
          template,
          values: {
            task: "示例任务",
            scenario: "general",
            thread_summary: "线程摘要",
            current_plan: "计划",
            observations: "观察",
            remaining_budget: "预算",
          },
        }),
      },
    ),
  exportProfiles: (csrf: string) =>
    request<{ schema_version: string; profiles: AgentProfileInput[] }>(
      "/admin/settings/agent-profiles/export",
      { headers: adminHeaders(csrf) },
    ),
  importProfiles: (csrf: string, bundle: unknown) =>
    request<AgentProfile[]>("/admin/settings/agent-profiles/import", {
      method: "POST",
      headers: adminHeaders(csrf),
      body: JSON.stringify(bundle),
    }),
};
