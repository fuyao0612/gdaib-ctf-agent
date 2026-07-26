/** 任务型 Agent 的设置中心。 */
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import AgentProfileCenter from "./components/AgentProfileCenter";
import EvaluationResults from "./components/EvaluationResults";
import ProviderSettings from "./components/ProviderSettings";
import SkillSettings from "./components/SkillSettings";
import SetupProgress from "./components/SetupProgress";
import ToolExtensionsCenter from "./components/ToolExtensionsCenter";
import { useAdminSession } from "./hooks/useAdminSession";
import type {
  AgentDefaults,
  ProviderConfig,
  SettingsMode,
  SkillDefinition,
  SetupStatus,
} from "./types";
import "./settings.css";

interface Props {
  onClose: () => void;
  onChanged: () => Promise<void>;
  initialSetup?: boolean;
}

export default function SettingsCenter({
  onClose,
  onChanged,
  initialSetup = false,
}: Props) {
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [agentDefaults, setAgentDefaults] = useState<AgentDefaults | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<SettingsMode>("beginner");
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const session = useAdminSession();

  const load = useCallback(async (csrf: string) => {
    const [items, defaults, status, configuredSkills] = await Promise.all([
      api.adminProviders(csrf),
      api.agentDefaults(csrf),
      api.setupStatus(),
      api.adminSkills(csrf),
    ]);
    setProviders(items);
    setAgentDefaults(defaults);
    setSetupStatus(status);
    setSkills(Array.isArray(configuredSkills) ? configuredSkills : []);
  }, []);

  useEffect(() => {
    void api.setupStatus().then(setSetupStatus).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (session.csrf)
      void load(session.csrf).catch((cause) => setError(String(cause)));
  }, [session.csrf, load]);

  async function saveAgentDefaults(event: FormEvent) {
    event.preventDefault();
    if (!agentDefaults) return;
    setBusy(true);
    setError("");
    try {
      setAgentDefaults(await api.saveAgentDefaults(session.csrf, agentDefaults));
      setNotice("Agent 默认预算已保存");
      await onChanged();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function syncPublicState() {
    setSetupStatus(await api.setupStatus());
    await onChanged();
  }

  return (
    <div className="settings-backdrop" role="dialog" aria-modal="true" aria-label="设置中心">
      <section className="settings-panel">
        <header>
          <div>
            <span className="eyebrow">AGENT SETTINGS</span>
            <h2>设置中心</h2>
          </div>
          <div className="settings-header-actions"><button onClick={onClose}>关闭</button></div>
        </header>
        <div className="settings-scroll">
          <SetupProgress authenticated={session.authenticated} status={setupStatus} />
          {initialSetup && <p className="setup-hint">完成模型连接测试后即可提交第一个 Agent 任务。</p>}
          {!session.authenticated ? (
            <div className="admin-login" role={session.error ? "alert" : "status"}>
              {session.error ? `无法建立本地安全会话：${session.error}` : "正在建立本地安全会话..."}
            </div>
          ) : (
            <div className="settings-content">
              <div className="settings-mode-switch" role="group" aria-label="设置模式">
                <div>
                  <strong>{mode === "beginner" ? "新手模式" : "高级模式"}</strong>
                  <small>{mode === "beginner" ? "仅显示完成首个任务所需的设置" : "显示预算、上下文、工具与版本管理"}</small>
                </div>
                <div>
                  <button className={mode === "beginner" ? "active" : ""} aria-pressed={mode === "beginner"} onClick={() => setMode("beginner")}>新手模式</button>
                  <button className={mode === "advanced" ? "active" : ""} aria-pressed={mode === "advanced"} onClick={() => setMode("advanced")}>高级模式</button>
                </div>
              </div>
              <ProviderSettings csrf={session.csrf} providers={providers} onRefresh={() => load(session.csrf)} onChanged={onChanged} onNotice={setNotice} onError={setError} mode={mode} />
              <ToolExtensionsCenter csrf={session.csrf} mode={mode} onChanged={syncPublicState} onNotice={setNotice} onError={setError} />
              <AgentProfileCenter csrf={session.csrf} providers={providers} onChanged={syncPublicState} mode={mode} />
              {mode === "advanced" && <SkillSettings csrf={session.csrf} skills={skills} onRefresh={async () => { await load(session.csrf); await onChanged(); }} onNotice={setNotice} onError={setError} />}
              {mode === "advanced" && <EvaluationResults onError={setError} />}
              {mode === "advanced" && agentDefaults && (
                <section>
                  <div className="settings-title"><h3>Agent 默认预算与上下文</h3></div>
                  <form className="settings-form" onSubmit={saveAgentDefaults}>
                    <div className="form-grid">
                      {([[
                        "最大步骤", "max_steps"], ["模型调用", "max_model_calls"], ["工具调用", "max_tool_calls"], ["最大 Token", "max_tokens"], ["最大模型费用", "max_model_cost"], ["总时长（秒）", "max_duration_seconds"], ["单步超时（秒）", "step_timeout_seconds"],
                      ] as const).map(([label, key]) => (
                        <label key={key}>{label}<input type="number" value={agentDefaults.budget[key]} onChange={(event) => setAgentDefaults({ ...agentDefaults, budget: { ...agentDefaults.budget, [key]: Number(event.target.value) } })} /></label>
                      ))}
                      <label>Provider 重试预算<input type="number" value={agentDefaults.provider_retry_budget} onChange={(event) => setAgentDefaults({ ...agentDefaults, provider_retry_budget: Number(event.target.value) })} /></label>
                      <label>
                        上下文窗口
                        <select aria-label="上下文窗口预设" value={[32768, 65536, 131072, 262144].includes(agentDefaults.context_token_budget) ? agentDefaults.context_token_budget : "custom"} onChange={(event) => {
                          const value = event.target.value;
                          if (value !== "custom") setAgentDefaults({ ...agentDefaults, context_token_budget: Number(value) });
                        }}>
                          <option value={32768}>32K</option>
                          <option value={65536}>64K</option>
                          <option value={131072}>128K</option>
                          <option value={262144}>256K（默认）</option>
                          <option value="custom">自定义</option>
                        </select>
                      </label>
                      <label>自定义上下文 Token<input type="number" min={32768} value={agentDefaults.context_token_budget} onChange={(event) => setAgentDefaults({ ...agentDefaults, context_token_budget: Number(event.target.value) })} /></label>
                      <label>观察字符预算<input type="number" value={agentDefaults.observation_char_budget} onChange={(event) => setAgentDefaults({ ...agentDefaults, observation_char_budget: Number(event.target.value) })} /></label>
                    </div>
                    <button className="primary" disabled={busy}>保存 Agent 设置</button>
                  </form>
                </section>
              )}
            </div>
          )}
        </div>
        {(notice || error) && <div className="settings-feedback">{notice && <div className="settings-notice" aria-live="polite">{notice}</div>}{error && <div role="alert" className="settings-error">{error}</div>}</div>}
      </section>
    </div>
  );
}
