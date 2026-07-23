/** 设置中心协调器：管理管理员会话，并组合 Provider、Agent 配置与默认预算。 */
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import AgentProfileCenter from "./components/AgentProfileCenter";
import ProviderSettings from "./components/ProviderSettings";
import SkillSettings from "./components/SkillSettings";
import SetupProgress from "./components/SetupProgress";
import { useAdminSession } from "./hooks/useAdminSession";
import type {
  AgentDefaults,
  ChatDefaults,
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
  const [adminToken, setAdminToken] = useState("");
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [agentDefaults, setAgentDefaults] = useState<AgentDefaults | null>(
    null,
  );
  const [chatDefaults, setChatDefaults] = useState<ChatDefaults | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<SettingsMode>("beginner");
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const session = useAdminSession();

  const load = useCallback(async (csrf: string) => {
    const [items, defaults, chat, status, configuredSkills] = await Promise.all([
      api.adminProviders(csrf),
      api.agentDefaults(csrf),
      api.chatDefaults(csrf),
      api.setupStatus(),
      api.adminSkills(csrf),
    ]);
    setProviders(items);
    setAgentDefaults(defaults);
    setChatDefaults(chat);
    setSetupStatus(status);
    setSkills(Array.isArray(configuredSkills) ? configuredSkills : []);
  }, []);

  useEffect(() => {
    void api.setupStatus().then(setSetupStatus).catch(() => undefined);
  }, []);

  // 已存在 HttpOnly 会话时，CSRF 恢复完成后立即载入设置，而不是要求再次登录。
  useEffect(() => {
    if (session.csrf)
      void load(session.csrf).catch((cause) => setError(String(cause)));
  }, [session.csrf, load]);

  async function authenticate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const csrf = await session.login(adminToken);
      setAdminToken("");
      await load(csrf);
      setNotice("已建立安全管理员会话");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(
        message.includes("管理员鉴权失败")
          ? "管理员令牌不正确。请使用项目根目录 .env 中 YUWANG_ADMIN_TOKEN 的值；如果刚修改过该值，请重启服务后再试。"
          : message,
      );
    } finally {
      setBusy(false);
    }
  }

  async function saveAgentDefaults(event: FormEvent) {
    event.preventDefault();
    if (!agentDefaults) return;
    setBusy(true);
    setError("");
    try {
      setAgentDefaults(
        await api.saveAgentDefaults(session.csrf, agentDefaults),
      );
      setNotice("Agent 默认预算已保存");
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function saveChatDefaults(event: FormEvent) {
    event.preventDefault();
    if (!chatDefaults) return;
    setBusy(true);
    setError("");
    try {
      setChatDefaults(await api.saveChatDefaults(session.csrf, chatDefaults));
      setNotice("聊天与界面偏好已保存");
      await onChanged();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function syncPublicState() {
    const status = await api.setupStatus();
    setSetupStatus(status);
    await onChanged();
  }

  return (
    <div
      className="settings-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="设置中心"
    >
      <section className="settings-panel">
        <header>
          <div>
            <span className="eyebrow">ADMIN SETTINGS</span>
            <h2>设置中心</h2>
          </div>
          <div className="settings-header-actions">
            <button onClick={onClose}>关闭</button>
            <button onClick={() => void session.logout().finally(onClose)}>
              退出登录
            </button>
          </div>
        </header>
        <div className="settings-scroll">
          <SetupProgress
            authenticated={session.authenticated}
            status={setupStatus}
          />
          {initialSetup && (
            <p className="setup-hint">
              首次配置只需要管理员登录、填写模型并完成一次真实连接测试。
            </p>
          )}
          {!session.authenticated ? (
            <form className="admin-login" onSubmit={authenticate}>
              <h3>管理员验证</h3>
              <p>令牌仅保存在当前页面内存中，关闭或刷新后即清除。</p>
              <p>
                请粘贴项目根目录 <code>.env</code> 中{" "}
                <code>YUWANG_ADMIN_TOKEN</code> 等号后的完整内容。
              </p>
              <label>
                管理员令牌
                <input
                  type="password"
                  aria-label="管理员令牌"
                  autoComplete="off"
                  value={adminToken}
                  onChange={(event) => setAdminToken(event.target.value)}
                />
              </label>
              <button
                className="primary"
                disabled={busy || session.busy || !adminToken}
              >
                进入设置
              </button>
            </form>
          ) : (
            <div className="settings-content">
              <div className="settings-mode-switch" role="group" aria-label="设置模式">
                <div>
                  <strong>{mode === "beginner" ? "新手模式" : "高级模式"}</strong>
                  <small>
                    {mode === "beginner"
                      ? "仅显示首次运行需要的配置"
                      : "显示预算、上下文、记忆、规划、验证与版本管理"}
                  </small>
                </div>
                <div>
                  <button
                    className={mode === "beginner" ? "active" : ""}
                    aria-pressed={mode === "beginner"}
                    onClick={() => setMode("beginner")}
                  >
                    新手模式
                  </button>
                  <button
                    className={mode === "advanced" ? "active" : ""}
                    aria-pressed={mode === "advanced"}
                    onClick={() => setMode("advanced")}
                  >
                    高级模式
                  </button>
                </div>
              </div>
              <ProviderSettings
                csrf={session.csrf}
                providers={providers}
                onRefresh={() => load(session.csrf)}
                onChanged={onChanged}
                onNotice={setNotice}
                onError={setError}
                mode={mode}
              />
              {chatDefaults && (
                <section>
                  <div className="settings-title">
                    <div>
                      <h3>聊天与界面</h3>
                      <small>所有消息从同一输入框发送；系统会按需进入受控执行。</small>
                    </div>
                  </div>
                  <form className="settings-form" onSubmit={saveChatDefaults}>
                    <div className="form-grid">
                      <label>
                        默认聊天模型
                        <select
                          aria-label="默认聊天模型"
                          value={chatDefaults.default_provider_id ?? ""}
                          onChange={(event) =>
                            setChatDefaults({
                              ...chatDefaults,
                              default_provider_id: event.target.value || null,
                            })
                          }
                        >
                          <option value="">使用默认 Provider</option>
                          {providers.filter((item) => item.enabled).map((provider) => (
                            <option key={provider.id} value={provider.id}>
                              {provider.name} · {provider.model}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        外观
                        <select aria-label="外观" value="light" disabled>
                          <option value="light">浅色</option>
                        </select>
                      </label>
                    </div>
                    <div className="check-row">
                      <label>
                        <input
                          type="checkbox"
                          checked={chatDefaults.stream_enabled}
                          onChange={(event) =>
                            setChatDefaults({
                              ...chatDefaults,
                              stream_enabled: event.target.checked,
                            })
                          }
                        />
                        流式输出
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={chatDefaults.sidebar_expanded}
                          onChange={(event) =>
                            setChatDefaults({
                              ...chatDefaults,
                              sidebar_expanded: event.target.checked,
                            })
                          }
                        />
                        默认展开侧栏
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={chatDefaults.audit_expanded}
                          onChange={(event) =>
                            setChatDefaults({
                              ...chatDefaults,
                              audit_expanded: event.target.checked,
                            })
                          }
                        />
                        Agent 默认展开审计
                      </label>
                    </div>
                    <details className="advanced-settings">
                      <summary>高级聊天设置</summary>
                      <div className="form-grid">
                        <label className="wide">
                          普通聊天系统提示词
                          <textarea
                            aria-label="普通聊天系统提示词"
                            value={chatDefaults.system_prompt}
                            onChange={(event) =>
                              setChatDefaults({
                                ...chatDefaults,
                                system_prompt: event.target.value,
                              })
                            }
                          />
                        </label>
                        <label>
                          上下文消息数
                          <input
                            type="number"
                            value={chatDefaults.recent_message_limit}
                            onChange={(event) =>
                              setChatDefaults({
                                ...chatDefaults,
                                recent_message_limit: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          上下文 Token 限制
                          <input
                            type="number"
                            value={chatDefaults.context_token_limit}
                            onChange={(event) =>
                              setChatDefaults({
                                ...chatDefaults,
                                context_token_limit: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          附件字符限制
                          <input
                            type="number"
                            value={chatDefaults.attachment_char_limit}
                            onChange={(event) =>
                              setChatDefaults({
                                ...chatDefaults,
                                attachment_char_limit: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                      </div>
                    </details>
                    <button className="primary" disabled={busy}>
                      保存聊天设置
                    </button>
                  </form>
                </section>
              )}
              <AgentProfileCenter
                csrf={session.csrf}
                providers={providers}
                onChanged={syncPublicState}
                mode={mode}
              />
              {mode === "advanced" && (
                <SkillSettings
                  csrf={session.csrf}
                  skills={skills}
                  onRefresh={async () => {
                    await load(session.csrf);
                    await onChanged();
                  }}
                  onNotice={setNotice}
                  onError={setError}
                />
              )}
              {mode === "advanced" && agentDefaults && (
                <section>
                  <div className="settings-title">
                    <h3>平台默认预算</h3>
                  </div>
                  <form className="settings-form" onSubmit={saveAgentDefaults}>
                    <div className="form-grid">
                      {(
                        [
                          ["最大步骤", "max_steps"],
                          ["模型调用", "max_model_calls"],
                          ["工具调用", "max_tool_calls"],
                          ["最大 Token", "max_tokens"],
                          ["最大模型费用", "max_model_cost"],
                          ["总时长（秒）", "max_duration_seconds"],
                          ["单步超时（秒）", "step_timeout_seconds"],
                        ] as const
                      ).map(([label, key]) => (
                        <label key={key}>
                          {label}
                          <input
                            type="number"
                            value={agentDefaults.budget[key]}
                            onChange={(event) =>
                              setAgentDefaults({
                                ...agentDefaults,
                                budget: {
                                  ...agentDefaults.budget,
                                  [key]: Number(event.target.value),
                                },
                              })
                            }
                          />
                        </label>
                      ))}
                      <label>
                        Provider 重试预算
                        <input
                          type="number"
                          value={agentDefaults.provider_retry_budget}
                          onChange={(event) =>
                            setAgentDefaults({
                              ...agentDefaults,
                              provider_retry_budget: Number(event.target.value),
                            })
                          }
                        />
                      </label>
                      <label>
                        上下文 Token 预算
                        <input
                          type="number"
                          value={agentDefaults.context_token_budget}
                          onChange={(event) =>
                            setAgentDefaults({
                              ...agentDefaults,
                              context_token_budget: Number(event.target.value),
                            })
                          }
                        />
                      </label>
                      <label>
                        观察字符预算
                        <input
                          type="number"
                          value={agentDefaults.observation_char_budget}
                          onChange={(event) =>
                            setAgentDefaults({
                              ...agentDefaults,
                              observation_char_budget: Number(
                                event.target.value,
                              ),
                            })
                          }
                        />
                      </label>
                    </div>
                    <button className="primary" disabled={busy}>
                      保存 Agent 设置
                    </button>
                  </form>
                </section>
              )}
            </div>
          )}
        </div>
        {(notice || error) && (
          <div className="settings-feedback">
            {notice && (
              <div className="settings-notice" aria-live="polite">
                {notice}
              </div>
            )}
            {error && (
              <div role="alert" className="settings-error">
                {error}
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
