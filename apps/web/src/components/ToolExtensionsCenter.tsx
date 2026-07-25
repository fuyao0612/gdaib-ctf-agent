/** 统一工具与 MCP 扩展的管理界面；不接受插件代码或任意 Shell。 */
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import type {
  McpDeletionImpact,
  McpServerInput,
  McpServerView,
  SettingsMode,
  ToolSpec,
} from "../types";

interface Props {
  csrf: string;
  mode: SettingsMode;
  onChanged: () => Promise<void>;
  onNotice: (value: string) => void;
  onError: (value: string) => void;
}

const sourceLabels = {
  builtin: "内置",
  python_plugin: "Python 插件",
  mcp: "MCP",
} as const;

const riskLabels = { low: "低风险", medium: "中风险", high: "高风险" } as const;

function splitNames(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function blankServer(): McpServerInput {
  return {
    name: "",
    transport: "streamable_http",
    command: null,
    args: [],
    url: "",
    enabled: true,
    connect_timeout_seconds: 10,
    call_timeout_seconds: 30,
    allowed_tools: [],
    blocked_tools: [],
  };
}

function serverInput(value: McpServerView): McpServerInput {
  return {
    name: value.name,
    transport: value.transport,
    command: value.command,
    args: value.args,
    url: value.url,
    enabled: value.enabled,
    connect_timeout_seconds: value.connect_timeout_seconds,
    call_timeout_seconds: value.call_timeout_seconds,
    allowed_tools: value.allowed_tools,
    blocked_tools: value.blocked_tools,
  };
}

function sourceServerId(tool: ToolSpec): string | null {
  return tool.source.startsWith("mcp:") ? tool.source.slice(4) : null;
}

export default function ToolExtensionsCenter({
  csrf,
  mode,
  onChanged,
  onNotice,
  onError,
}: Props) {
  const [tools, setTools] = useState<ToolSpec[]>([]);
  const [servers, setServers] = useState<McpServerView[]>([]);
  const [allowedCommands, setAllowedCommands] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<McpServerInput>(blankServer);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [pendingEnable, setPendingEnable] = useState<McpServerView | null>(null);
  const [deletionImpact, setDeletionImpact] = useState<McpDeletionImpact | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [toolItems, serverItems, commands] = await Promise.all([
        api.tools(),
        api.mcpServers(csrf),
        api.mcpStdioCommands(csrf),
      ]);
      // 设置中心可独立升级；旧 API 测试替身或暂未部署的新端点不应让整个面板崩溃。
      setTools(Array.isArray(toolItems) ? toolItems : []);
      setServers(Array.isArray(serverItems) ? serverItems : []);
      setAllowedCommands(Array.isArray(commands.commands) ? commands.commands : []);
    } catch (cause) {
      onError(String(cause));
    } finally {
      setLoading(false);
    }
  }, [csrf, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const serverById = useMemo(
    () => new Map(servers.map((item) => [item.id, item])),
    [servers],
  );
  const orderedTools = useMemo(
    () => [...tools].sort((left, right) => left.display_name.localeCompare(right.display_name)),
    [tools],
  );

  function resetForm() {
    setEditingId(null);
    setForm(blankServer());
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const normalized: McpServerInput = {
        ...form,
        command: form.transport === "stdio" ? form.command : null,
        url: form.transport === "streamable_http" ? form.url : null,
      };
      const server = editingId
        ? await api.updateMcpServer(csrf, editingId, normalized)
        : await api.createMcpServer(csrf, normalized);
      resetForm();
      await load();
      await onChanged();
      onNotice(`MCP 服务“${server.name}”已保存，可使用“检查并刷新”发现工具`);
    } catch (cause) {
      onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function refresh(server: McpServerView) {
    setBusy(true);
    try {
      const result = await api.refreshMcpServer(csrf, server.id);
      await load();
      await onChanged();
      onNotice(`已检查“${server.name}”，发现 ${result.tools.length} 个可用工具`);
    } catch (cause) {
      onError(String(cause));
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function setEnabled(server: McpServerView, enabled: boolean) {
    setBusy(true);
    try {
      await api.updateMcpServer(csrf, server.id, {
        ...serverInput(server),
        enabled,
      });
      if (!enabled) await api.refreshMcpServer(csrf, server.id);
      await load();
      await onChanged();
      onNotice(`MCP 服务“${server.name}”已${enabled ? "启用" : "停用"}`);
    } catch (cause) {
      onError(String(cause));
    } finally {
      setBusy(false);
      setPendingEnable(null);
    }
  }

  async function requestDelete(server: McpServerView) {
    try {
      setDeletionImpact(await api.mcpDeletionImpact(csrf, server.id));
    } catch (cause) {
      onError(String(cause));
    }
  }

  async function removeServer() {
    if (!deletionImpact || deletionImpact.blocking_reasons.length) return;
    setBusy(true);
    try {
      await api.deleteMcpServer(csrf, deletionImpact.id);
      setDeletionImpact(null);
      resetForm();
      await load();
      await onChanged();
      onNotice(`MCP 服务“${deletionImpact.name}”已删除；历史 Run 快照保持不变`);
    } catch (cause) {
      onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="tool-extensions" data-testid="tool-extensions-center">
      <div className="settings-title">
        <div>
          <h3>工具与扩展</h3>
          <small>工具只会在下一次 Run 生效；运行中的快照不会被设置变更改写。</small>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading || busy}>
          刷新列表
        </button>
      </div>

      {loading ? (
        <p className="settings-notice">正在读取工具与扩展状态…</p>
      ) : (
        <div className="tool-list" aria-label="工具列表">
          {orderedTools.length ? (
            orderedTools.map((tool) => {
              const server = sourceServerId(tool)
                ? serverById.get(sourceServerId(tool) ?? "")
                : undefined;
              const enabled = tool.enabled && (server?.enabled ?? true);
              return (
                <article className="tool-row" key={tool.id}>
                  <div className="tool-main">
                    <strong title={tool.display_name}>{tool.display_name}</strong>
                    <small title={tool.id}>{tool.id} · v{tool.version}</small>
                    {mode === "advanced" && <p>{tool.description}</p>}
                  </div>
                  <div className="tool-flags" aria-label={`${tool.display_name} 状态`}>
                    <span>{sourceLabels[tool.source_type]}</span>
                    <span className={`risk-${tool.risk}`}>{riskLabels[tool.risk]}</span>
                    <span className={enabled ? "health-healthy" : "health-disabled"}>
                      {enabled ? tool.health.status : "disabled"}
                    </span>
                  </div>
                  {mode === "advanced" && (
                    <details className="tool-details">
                      <summary>权限与协议</summary>
                      <dl>
                        <div><dt>能力</dt><dd>{tool.capabilities.join("、") || "未声明"}</dd></div>
                        <div><dt>场景</dt><dd>{tool.scenarios.join("、") || "未声明"}</dd></div>
                        <div><dt>权限</dt><dd>{tool.permissions.join("、") || "无额外权限"}</dd></div>
                        <div><dt>网络</dt><dd>{tool.requires_network ? "需要" : "不需要"}</dd></div>
                        <div><dt>超时</dt><dd>{tool.timeout_seconds} 秒</dd></div>
                      </dl>
                      {tool.health.last_error && <p className="tool-error">最近错误：{tool.health.last_error}</p>}
                      <details>
                        <summary>输入 Schema</summary>
                        <pre>{JSON.stringify(tool.input_schema, null, 2)}</pre>
                      </details>
                    </details>
                  )}
                </article>
              );
            })
          ) : (
            <p className="settings-notice">暂无已发现的工具。内置工具会在服务启动时注册，MCP 工具需先检查并刷新。</p>
          )}
        </div>
      )}

      <div className="settings-title tool-server-title">
        <div>
          <h4>MCP 服务</h4>
          <small>仅可选择部署管理员已允许的 Stdio 程序；不支持上传插件代码或输入 Shell 命令。</small>
        </div>
        {mode === "advanced" && <button type="button" onClick={resetForm}>添加服务</button>}
      </div>
      <div className="mcp-server-list" aria-label="MCP 服务列表">
        {servers.length ? servers.map((server) => (
          <article className="mcp-server-row" key={server.id}>
            <div>
              <strong>{server.name}</strong>
              <small>{server.transport === "stdio" ? "Stdio" : "Streamable HTTP"} · {server.health_status}</small>
              {server.last_error && <p className="tool-error">最近错误：{server.last_error}</p>}
            </div>
            <div className="mcp-server-actions">
              <button type="button" onClick={() => void refresh(server)} disabled={busy || !server.enabled}>检查并刷新</button>
              <button type="button" onClick={() => server.enabled ? void setEnabled(server, false) : setPendingEnable(server)} disabled={busy}>
                {server.enabled ? "停用" : "启用"}
              </button>
              {mode === "advanced" && <>
                <button type="button" onClick={() => { setEditingId(server.id); setForm(serverInput(server)); }}>编辑</button>
                <button type="button" className="danger" onClick={() => void requestDelete(server)}>删除</button>
              </>}
            </div>
            {pendingEnable?.id === server.id && (
              <div className="mcp-confirmation" role="alert">
                <p>MCP 工具默认按中风险处理。启用后，调用仍需经过策略检查与用户确认。</p>
                <button type="button" className="primary" onClick={() => void setEnabled(server, true)} disabled={busy}>确认启用</button>
                <button type="button" onClick={() => setPendingEnable(null)}>取消</button>
              </div>
            )}
          </article>
        )) : <p className="settings-notice">尚未配置 MCP 服务。</p>}
      </div>

      {mode === "advanced" && (
        <form className="settings-form mcp-form" onSubmit={(event) => void submit(event)}>
          <h4>{editingId ? "编辑 MCP 服务" : "添加 MCP 服务"}</h4>
          <div className="form-grid">
            <label>
              服务名称
              <input aria-label="MCP 服务名称" value={form.name} required maxLength={120} onChange={(event) => setForm({ ...form, name: event.target.value })} />
            </label>
            <label>
              传输方式
              <select aria-label="MCP 传输方式" value={form.transport} onChange={(event) => setForm({ ...form, transport: event.target.value as McpServerInput["transport"], command: event.target.value === "stdio" ? allowedCommands[0] ?? null : null, url: event.target.value === "streamable_http" ? form.url ?? "" : null })}>
                <option value="streamable_http">Streamable HTTP</option>
                <option value="stdio">Stdio</option>
              </select>
            </label>
            {form.transport === "streamable_http" ? (
              <label className="wide">
                HTTPS 地址
                <input aria-label="MCP HTTPS 地址" type="url" placeholder="https://mcp.example.com/mcp" value={form.url ?? ""} required onChange={(event) => setForm({ ...form, url: event.target.value })} />
              </label>
            ) : (
              <>
                <label>
                  Stdio 程序
                  <select aria-label="Stdio 程序" value={form.command ?? ""} required onChange={(event) => setForm({ ...form, command: event.target.value || null })}>
                    <option value="">选择部署允许的程序</option>
                    {allowedCommands.map((command) => <option value={command} key={command}>{command}</option>)}
                  </select>
                </label>
                <label>
                  参数（每行一个）
                  <textarea aria-label="MCP 参数" value={form.args.join("\n")} onChange={(event) => setForm({ ...form, args: splitNames(event.target.value) })} />
                </label>
              </>
            )}
            <label>
              认证令牌（可选）
              <input aria-label="MCP 认证令牌" type="password" autoComplete="new-password" placeholder={editingId ? "留空以保留现有令牌" : "仅发送一次，不会回显"} onChange={(event) => setForm({ ...form, auth_token: event.target.value || undefined })} />
            </label>
            <label>
              连接超时（秒）
              <input aria-label="MCP 连接超时" type="number" min="1" max="120" value={form.connect_timeout_seconds} onChange={(event) => setForm({ ...form, connect_timeout_seconds: Number(event.target.value) })} />
            </label>
            <label>
              调用超时（秒）
              <input aria-label="MCP 调用超时" type="number" min="1" max="300" value={form.call_timeout_seconds} onChange={(event) => setForm({ ...form, call_timeout_seconds: Number(event.target.value) })} />
            </label>
            <label>
              允许工具（逗号或换行分隔）
              <textarea aria-label="允许 MCP 工具" value={form.allowed_tools.join("\n")} onChange={(event) => setForm({ ...form, allowed_tools: splitNames(event.target.value) })} />
            </label>
            <label>
              阻止工具（逗号或换行分隔）
              <textarea aria-label="阻止 MCP 工具" value={form.blocked_tools.join("\n")} onChange={(event) => setForm({ ...form, blocked_tools: splitNames(event.target.value) })} />
            </label>
          </div>
          <div className="check-row"><label><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />保存后启用此服务</label></div>
          <button className="primary" disabled={busy}>{editingId ? "保存 MCP 服务" : "创建 MCP 服务"}</button>
        </form>
      )}

      {deletionImpact && (
        <div className="mcp-confirmation" role="alert">
          <h4>删除“{deletionImpact.name}”前检查</h4>
          <p>活动 Run：{deletionImpact.active_run_count}；历史工具快照：{deletionImpact.historical_snapshot_count}（将被保留）。</p>
          {deletionImpact.blocking_reasons.map((reason) => <p className="tool-error" key={reason}>{reason}</p>)}
          <button type="button" className="danger" disabled={busy || deletionImpact.blocking_reasons.length > 0} onClick={() => void removeServer()}>确认删除</button>
          <button type="button" onClick={() => setDeletionImpact(null)}>取消</button>
        </div>
      )}
    </section>
  );
}
