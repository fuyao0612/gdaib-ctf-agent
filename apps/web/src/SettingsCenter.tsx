import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import AgentProfileCenter from "./components/AgentProfileCenter";
import ProviderSettings from "./components/ProviderSettings";
import SkillSettings from "./components/SkillSettings";
import SetupProgress from "./components/SetupProgress";
import ToolExtensionsCenter from "./components/ToolExtensionsCenter";
import { useAdminSession } from "./hooks/useAdminSession";
import type { AgentDefaults, ProviderConfig, SkillDefinition, SetupStatus } from "./types";
import "./settings.css";

interface Props { onClose: () => void; onChanged: () => Promise<void>; initialSetup?: boolean; }
type Category = "quick" | "providers" | "agents" | "extensions" | "runtime";

const categories: { id: Category; label: string; hint: string }[] = [
  { id: "quick", label: "快速配置", hint: "首次连接与默认值" },
  { id: "providers", label: "模型连接", hint: "Provider 与健康状态" },
  { id: "agents", label: "Agent 配置", hint: "配置档案与继承关系" },
  { id: "extensions", label: "扩展能力", hint: "工具、MCP、Skills" },
  { id: "runtime", label: "运行与安全", hint: "系统默认预算与边界" },
];

export default function SettingsCenter({ onClose, onChanged, initialSetup = false }: Props) {
  const [category, setCategory] = useState<Category>("quick");
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [agentDefaults, setAgentDefaults] = useState<AgentDefaults | null>(null);
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const session = useAdminSession();

  const load = useCallback(async (csrf: string) => {
    const [items, defaults, status, configuredSkills] = await Promise.all([
      api.adminProviders(csrf), api.agentDefaults(csrf), api.setupStatus(), api.adminSkills(csrf),
    ]);
    setProviders(items); setAgentDefaults(defaults); setSetupStatus(status);
    setSkills(Array.isArray(configuredSkills) ? configuredSkills : []);
  }, []);

  useEffect(() => { void api.setupStatus().then(setSetupStatus).catch(() => undefined); }, []);
  useEffect(() => { if (session.csrf) void load(session.csrf).catch((cause) => setError(String(cause))); }, [session.csrf, load]);

  async function saveAgentDefaults(event: FormEvent) {
    event.preventDefault(); if (!agentDefaults) return;
    setBusy(true); setError("");
    try { setAgentDefaults(await api.saveAgentDefaults(session.csrf, agentDefaults)); setNotice("系统默认值已保存"); await onChanged(); }
    catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  }
  async function syncPublicState() { setSetupStatus(await api.setupStatus()); await onChanged(); }

  const current = categories.find((item) => item.id === category) ?? categories[0];
  return (
    <div className="settings-backdrop" role="dialog" aria-modal="true" aria-label="设置中心">
      <section className="settings-panel">
        <header className="settings-panel-header">
          <div><span className="eyebrow">AGENT SETTINGS</span><h2>设置中心</h2></div>
          <button type="button" onClick={onClose}>关闭</button>
        </header>
        <div className="settings-layout">
          <nav className="settings-nav" aria-label="设置分类">
            {categories.map((item) => (
              <button key={item.id} type="button" className={item.id === category ? "active" : ""}
                aria-current={item.id === category ? "page" : undefined}
                onClick={() => { setCategory(item.id); setShowAdvanced(false); }}>
                <strong>{item.label}</strong><small>{item.hint}</small>
              </button>
            ))}
          </nav>
          <div className="settings-scroll">
            <div className="settings-content">
              <div className="settings-breadcrumb"><div><span className="eyebrow">当前分类</span><h3>{current.label}</h3><p>{current.hint}</p></div>
                {category !== "quick" && category !== "runtime" && <button type="button" className={showAdvanced ? "active" : ""} aria-pressed={showAdvanced} onClick={() => setShowAdvanced((value) => !value)}>高级选项</button>}</div>
              <SetupProgress authenticated={session.authenticated} status={setupStatus} />
              {initialSetup && category === "quick" && <p className="setup-hint">先完成 Provider 连接测试，再选择默认 Agent，即可开始第一个任务。</p>}
              {!session.authenticated ? <div className="admin-login" role={session.error ? "alert" : "status"}>{session.error ? `无法建立本地安全会话：${session.error}` : "正在建立本地安全会话..."}</div> : (
                <>
                  {category === "quick" && <section className="quick-config">
                    <div className="quick-grid">
                      <article><span className="quick-label">默认 Provider</span><strong>{providers.find((item) => item.is_default)?.name ?? "尚未设置"}</strong><small>{providers.length ? `${providers.length} 个可用连接` : "连接模型后显示"}</small><button type="button" onClick={() => setCategory("providers")}>管理模型连接</button></article>
                      <article><span className="quick-label">默认 Agent</span><strong>{setupStatus?.checks.agent ? "已就绪" : "待完成"}</strong><small>系统默认 → Agent 配置 → 单次任务</small><button type="button" onClick={() => setCategory("agents")}>管理 Agent 配置</button></article>
                      <article><span className="quick-label">连接健康</span><strong>{providers.filter((item) => item.connection_status === "ok").length}/{providers.length || 0}</strong><small>仅显示真实服务端状态</small><button type="button" onClick={() => setCategory("providers")}>测试连接</button></article>
                    </div>
                  </section>}
                  {category === "providers" && <ProviderSettings csrf={session.csrf} providers={providers} onRefresh={() => load(session.csrf)} onChanged={onChanged} onNotice={setNotice} onError={setError} mode={showAdvanced ? "advanced" : "beginner"} />}
                  {category === "agents" && <AgentProfileCenter csrf={session.csrf} providers={providers} onChanged={syncPublicState} mode="advanced" />}
                  {category === "extensions" && <><ToolExtensionsCenter csrf={session.csrf} mode={showAdvanced ? "advanced" : "beginner"} onChanged={syncPublicState} onNotice={setNotice} onError={setError} /><SkillSettings csrf={session.csrf} skills={skills} onRefresh={async () => { await load(session.csrf); await onChanged(); }} onNotice={setNotice} onError={setError} /></>}
                  {category === "runtime" && agentDefaults && <section><div className="settings-title"><div><h3>系统默认预算与上下文</h3><p className="muted">这些是系统默认值；Agent 配置可以覆盖它们，单次任务的明确配置优先级最高。</p></div></div><form className="settings-form" onSubmit={saveAgentDefaults}><div className="form-grid">{([['最大步骤','max_steps'],['模型调用','max_model_calls'],['工具调用','max_tool_calls'],['最大 Token','max_tokens'],['最大模型费用','max_model_cost'],['总时长（秒）','max_duration_seconds'],['单步超时（秒）','step_timeout_seconds']] as const).map(([label,key]) => <label key={key}>{label}<input type="number" value={agentDefaults.budget[key]} onChange={(event) => setAgentDefaults({ ...agentDefaults, budget: { ...agentDefaults.budget, [key]: Number(event.target.value) } })} /></label>)}<label>Provider 重试预算<input type="number" value={agentDefaults.provider_retry_budget} onChange={(event) => setAgentDefaults({ ...agentDefaults, provider_retry_budget: Number(event.target.value) })} /></label><label>上下文窗口 Token<input type="number" min={32768} value={agentDefaults.context_token_budget} onChange={(event) => setAgentDefaults({ ...agentDefaults, context_token_budget: Number(event.target.value) })} /></label><label>观察字符预算<input type="number" value={agentDefaults.observation_char_budget} onChange={(event) => setAgentDefaults({ ...agentDefaults, observation_char_budget: Number(event.target.value) })} /></label></div><p className="settings-inheritance">系统默认值 → Agent 配置覆盖 → 单次任务选择</p><button className="primary" disabled={busy}>保存系统默认值</button></form></section>}
                </>
              )}
            </div>
          </div>
        </div>
        {(notice || error) && <div className="settings-feedback">{notice && <div className="settings-notice" aria-live="polite">{notice}</div>}{error && <div role="alert" className="settings-error">{error}</div>}</div>}
      </section>
    </div>
  );
}
