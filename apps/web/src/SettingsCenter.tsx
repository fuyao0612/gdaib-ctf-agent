/** 设置中心协调器：管理管理员会话，并组合 Provider、Agent 配置与默认预算。 */
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import AgentProfileCenter from "./components/AgentProfileCenter";
import ProviderSettings from "./components/ProviderSettings";
import { useAdminSession } from "./hooks/useAdminSession";
import type { AgentDefaults, ProviderConfig } from "./types";
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
  const [agentDefaults, setAgentDefaults] = useState<AgentDefaults | null>(
    null,
  );
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const session = useAdminSession();

  const load = useCallback(async (csrf: string) => {
    const [items, defaults] = await Promise.all([
      api.adminProviders(csrf),
      api.agentDefaults(csrf),
    ]);
    setProviders(items);
    setAgentDefaults(defaults);
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
          {initialSetup && (
            <div className="setup-progress">
              <strong>首次配置向导</strong>
              <span>
                1 管理员登录 → 2 配置 Provider → 3 连接测试 → 4 确认默认 Agent →
                5 开始对话
              </span>
              <small>管理员令牌只用于建立服务端会话，不会保存到浏览器。</small>
            </div>
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
              <ProviderSettings
                csrf={session.csrf}
                providers={providers}
                onRefresh={() => load(session.csrf)}
                onChanged={onChanged}
                onNotice={setNotice}
                onError={setError}
              />
              <AgentProfileCenter
                csrf={session.csrf}
                providers={providers}
                onChanged={onChanged}
              />
              {agentDefaults && (
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
