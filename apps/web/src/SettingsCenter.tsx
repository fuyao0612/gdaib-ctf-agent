import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  Blocks,
  Bot,
  Boxes,
  Cable,
  Rocket,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  X,
  type LucideIcon,
} from "lucide-react";
import { api } from "./api";
import AgentProfileCenter from "./components/AgentProfileCenter";
import CapabilityMarketplace, { type McpMarketplaceTemplate } from "./components/CapabilityMarketplace";
import ProviderSettings from "./components/ProviderSettings";
import SkillSettings from "./components/SkillSettings";
import SetupProgress from "./components/SetupProgress";
import ToolExtensionsCenter from "./components/ToolExtensionsCenter";
import KnowledgeBaseSettings from "./components/KnowledgeBaseSettings";
import IconButton from "./components/IconButton";
import { useAdminSession } from "./hooks/useAdminSession";
import type { AgentDefaults, ProviderConfig, SkillDefinition, SetupStatus } from "./types";
import "./settings.css";

interface Props { onClose: () => void; onChanged: () => Promise<void>; initialSetup?: boolean; initialCategory?: SettingsCategory; }
export type SettingsCategory = "quick" | "providers" | "marketplace" | "extensions" | "agents" | "runtime";
type InstalledTab = "knowledge" | "tools" | "skills";

const categories: { id: SettingsCategory; label: string; hint: string; icon: LucideIcon }[] = [
  { id: "quick", label: "开始使用", hint: "三步完成首次任务", icon: Rocket },
  { id: "providers", label: "模型与中转", hint: "连接、发现与切换模型", icon: Cable },
  { id: "marketplace", label: "能力广场", hint: "安装 Skills、接入 MCP", icon: Boxes },
  { id: "extensions", label: "已安装能力", hint: "知识库、工具、MCP、Skills", icon: Blocks },
  { id: "agents", label: "智能体策略", hint: "配置档案与继承关系", icon: Bot },
  { id: "runtime", label: "运行与安全", hint: "系统默认预算与边界", icon: ShieldCheck },
];

export default function SettingsCenter({ onClose, onChanged, initialSetup = false, initialCategory = "quick" }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [category, setCategory] = useState<SettingsCategory>(initialCategory);
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [agentDefaults, setAgentDefaults] = useState<AgentDefaults | null>(null);
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [installedTab, setInstalledTab] = useState<InstalledTab>("knowledge");
  const [mcpTemplate, setMcpTemplate] = useState<McpMarketplaceTemplate | null>(null);
  const session = useAdminSession();

  const load = useCallback(async (csrf: string) => {
    // Provider 列表驱动设置页的主要交互；先提交它，避免其他设置接口拖延列表刷新。
    const providersRequest = api.adminProviders(csrf);
    const restRequest = Promise.all([
      api.agentDefaults(csrf), api.setupStatus(), api.adminSkills(csrf),
    ]);
    const items = await providersRequest;
    setProviders(items);
    const [defaults, status, configuredSkills] = await restRequest;
    setAgentDefaults(defaults); setSetupStatus(status);
    setSkills(Array.isArray(configuredSkills) ? configuredSkills : []);
  }, []);

  useEffect(() => { void api.setupStatus().then(setSetupStatus).catch(() => undefined); }, []);
  useEffect(() => { if (session.csrf) void load(session.csrf).catch((cause) => setError(String(cause))); }, [session.csrf, load]);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    const previousFocus = document.activeElement as HTMLElement | null;
    const siblings = Array.from(dialog.parentElement?.children ?? [])
      .filter((item): item is HTMLElement => item instanceof HTMLElement && item !== dialog)
      .map((item) => ({ item, inert: item.inert, ariaHidden: item.getAttribute("aria-hidden") }));
    siblings.forEach(({ item }) => {
      item.inert = true;
      item.setAttribute("aria-hidden", "true");
    });
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    )).filter((item) => !item.hasAttribute("hidden"));
    requestAnimationFrame(() => focusable()[0]?.focus());
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    dialog.addEventListener("keydown", trapFocus);
    return () => {
      dialog.removeEventListener("keydown", trapFocus);
      siblings.forEach(({ item, inert, ariaHidden }) => {
        item.inert = inert;
        if (ariaHidden === null) item.removeAttribute("aria-hidden");
        else item.setAttribute("aria-hidden", ariaHidden);
      });
      previousFocus?.focus();
    };
  }, []);

  async function saveAgentDefaults(event: FormEvent) {
    event.preventDefault(); if (!agentDefaults) return;
    setBusy(true); setError("");
    try { setAgentDefaults(await api.saveAgentDefaults(session.csrf, agentDefaults)); setNotice("系统默认值已保存"); await onChanged(); }
    catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  }
  async function syncPublicState() { setSetupStatus(await api.setupStatus()); await onChanged(); }

  const current = categories.find((item) => item.id === category) ?? categories[0];
  return (
    <div ref={dialogRef} className="settings-backdrop" role="dialog" aria-modal="true" aria-label="设置中心">
      <section className="settings-panel">
        <header className="settings-panel-header">
          <div className="settings-heading"><span className="settings-heading-icon"><Settings size={20} aria-hidden="true" /></span><div><span className="eyebrow">工作台偏好与能力</span><h2>设置中心</h2></div></div>
          <IconButton icon={X} label="关闭设置中心" onClick={onClose} />
        </header>
        <div className="settings-layout">
          <nav className="settings-nav" aria-label="设置分类">
            {categories.map((item) => {
              const CategoryIcon = item.icon;
              return (
              <button key={item.id} type="button" className={item.id === category ? "active" : ""}
                aria-current={item.id === category ? "page" : undefined}
                onClick={() => { setCategory(item.id); setShowAdvanced(false); setNotice(""); setError(""); }}>
                <CategoryIcon size={18} aria-hidden="true" />
                <span><strong>{item.label}</strong><small>{item.hint}</small></span>
              </button>
            )})}
          </nav>
          <div className="settings-scroll">
            <div className="settings-content">
              <div className="settings-breadcrumb"><div><h3>{current.label}</h3><p>{current.hint}</p></div>
                {(["providers", "agents", "extensions"] as SettingsCategory[]).includes(category) && <button type="button" className={showAdvanced ? "active" : ""} aria-pressed={showAdvanced} onClick={() => setShowAdvanced((value) => !value)}><SlidersHorizontal size={16} aria-hidden="true" />高级选项</button>}</div>
              {(category === "quick" || category === "providers") && <SetupProgress authenticated={session.authenticated} status={setupStatus} />}
              {initialSetup && category === "quick" && <p className="setup-hint">先完成 Provider 连接测试，再选择默认 Agent，即可开始第一个任务。</p>}
              {!session.authenticated ? <div className="admin-login" role={session.error ? "alert" : "status"}>{session.error ? `无法建立本地安全会话：${session.error}` : "正在建立本地安全会话..."}</div> : (
                <>
                  {category === "quick" && <section className="quick-config onboarding-guide">
                    <div className="onboarding-intro"><div><span className="eyebrow">首次配置</span><h3>第一次使用只需要三步</h3><p>先连接中转模型，再安装需要的能力，最后关闭设置并创建安全任务。</p></div><span className={setupStatus?.configured ? "onboarding-ready" : "onboarding-pending"}>{setupStatus?.configured ? "已可开始" : "待完成"}</span></div>
                    <div className="quick-grid">
                      <article><span className="quick-step">步骤 1</span><strong>连接模型与中转</strong><small>{providers.filter((item) => item.connection_status === "ok").length ? `${providers.filter((item) => item.connection_status === "ok").length} 个连接已通过测试` : "填写地址和密钥，然后发现可用模型"}</small><button type="button" className="primary" onClick={() => setCategory("providers")}>{providers.length ? "管理模型" : "连接第一个模型"}</button></article>
                      <article><span className="quick-step">步骤 2</span><strong>安装任务能力</strong><small>{skills.length ? `已安装 ${skills.length} 个 Skill` : "从 CTF、应急、漏洞、逆向模板中选择"}</small><button type="button" onClick={() => setCategory("marketplace")}>打开能力广场</button></article>
                      <article><span className="quick-step">步骤 3</span><strong>创建安全任务</strong><small>选择场景、模型和授权目标，Agent 才会开始执行。</small><button type="button" disabled={!setupStatus?.configured} onClick={onClose}>关闭设置并开始</button></article>
                    </div>
                    <div className="onboarding-help"><strong>不知道输入什么？</strong><span>CTF：上传题目附件并要求“先检查文件类型，再给出证据化解题计划”。</span><span>应急：上传日志并要求“统一时区、建立时间线、提取 IOC”。</span></div>
                  </section>}
                  {category === "providers" && <ProviderSettings csrf={session.csrf} providers={providers} onRefresh={() => load(session.csrf)} onChanged={onChanged} onNotice={setNotice} onError={setError} mode={showAdvanced ? "advanced" : "beginner"} />}
                  {category === "marketplace" && <CapabilityMarketplace csrf={session.csrf} skills={skills} onSkillsChanged={async () => { await load(session.csrf); await onChanged(); }} onConfigureMcp={(template) => { setMcpTemplate(template); setInstalledTab("tools"); setCategory("extensions"); setShowAdvanced(true); }} onNotice={setNotice} onError={setError} />}
                  {category === "agents" && <AgentProfileCenter csrf={session.csrf} providers={providers} onChanged={syncPublicState} mode={showAdvanced ? "advanced" : "beginner"} />}
                  {category === "extensions" && <section className="installed-capabilities">
                    <div className="installed-tabs" role="tablist" aria-label="已安装能力类型">
                      <button role="tab" aria-selected={installedTab === "knowledge"} className={installedTab === "knowledge" ? "active" : ""} onClick={() => setInstalledTab("knowledge")}>安全知识库</button>
                      <button role="tab" aria-selected={installedTab === "tools"} className={installedTab === "tools" ? "active" : ""} onClick={() => setInstalledTab("tools")}>工具与 MCP</button>
                      <button role="tab" aria-selected={installedTab === "skills"} className={installedTab === "skills" ? "active" : ""} onClick={() => setInstalledTab("skills")}>Skills</button>
                    </div>
                    {installedTab === "knowledge" && <KnowledgeBaseSettings csrf={session.csrf} onNotice={setNotice} onError={setError} />}
                    {installedTab === "tools" && <ToolExtensionsCenter csrf={session.csrf} mode={showAdvanced ? "advanced" : "beginner"} onChanged={syncPublicState} onNotice={setNotice} onError={setError} initialMcpTemplate={mcpTemplate?.input ?? null} onTemplateConsumed={() => setMcpTemplate(null)} />}
                    {installedTab === "skills" && <SkillSettings csrf={session.csrf} skills={skills} onRefresh={async () => { await load(session.csrf); await onChanged(); }} onNotice={setNotice} onError={setError} />}
                  </section>}
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
