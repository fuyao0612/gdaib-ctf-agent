/** 单页工作台协调器：只管理共享状态和网络动作，页面区域由小组件渲染。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Binary,
  Boxes,
  Bug,
  ClipboardCheck,
  FilePlus2,
  Flag,
  FolderOpen,
  Menu,
  PanelLeftClose,
  Settings,
  ShieldCheck,
  Siren,
  X,
} from "lucide-react";
import { api } from "./api";
import SettingsCenter, { type SettingsCategory } from "./SettingsCenter";
import EvaluationResults from "./components/EvaluationResults";
import CreateThreadDialog from "./components/CreateThreadDialog";
import MessageComposer from "./components/MessageComposer";
import SkillSelector from "./components/SkillSelector";
import ToolSelector from "./components/ToolSelector";
import {
  ConversationView,
  InspectorPanel,
  StatusBadge,
} from "./components/RunViews";
import ThreadSidebar from "./components/ThreadSidebar";
import IconButton from "./components/IconButton";
import { useTaskActions } from "./hooks/useTaskActions";
import { useWorkbenchData } from "./hooks/useWorkbenchData";
import { useRunControlActions } from "./hooks/useRunControlActions";
import type {
  AgentPlan,
  Artifact,
  ProviderConfig,
  SkillDefinition,
  Thread,
  ToolSpec,
  SecurityScenario,
} from "./types";
import "./styles.css";
import "./thread-management.css";

const taskStarterPrompts: Record<SecurityScenario, string> = {
  general: "请分析我接下来提供的安全材料，先说明判断依据，再给出可复现的验证步骤和修复建议。",
  ctf: "请分析这道 CTF 题目。先识别题型和已有线索，再给出逐步解题计划；对 Flag 做格式与证据验证。",
  incident_response: "请分析这批应急响应材料，整理事件时间线、提取 IOC、判断影响范围，并给出处置与复盘建议。",
  vulnerability_analysis: "请在已授权范围内分析该漏洞，给出可复现证据、影响判断、CWE 映射，以及可验证的修复方案。",
  reverse_static: "请仅做静态分析，不执行样本。检查文件结构、导入、字符串和可疑行为，并给出证据与后续隔离建议。",
};

function initialSidebarExpanded(): boolean {
  if (isCompactViewport()) return false;
  return window.localStorage?.getItem("yuwang.sidebarExpanded") !== "false";
}

function isCompactViewport(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(max-width: 700px)").matches
  );
}

export default function App() {
  const workspace = useWorkbenchData();
  const {
    threads,
    detail,
    events,
    activeRun,
    report,
    audit,
    control,
    memories,
    setDetail,
    setEvents,
    setActiveRun,
    setReport,
    setControl,
    setMemories,
    loadThreads,
    loadControl,
    selectThread,
    connect,
    bootstrap,
  } = workspace;
  const [message, setMessage] = useState("");
  const [authorizedTarget, setAuthorizedTarget] = useState("");
  const [pendingArtifacts, setPendingArtifacts] = useState<Artifact[]>([]);
  // 附件在服务端确认接收前不应被当作已随消息发送；单独记录上传中状态，
  // 不用全局 busy 锁住正在运行任务的主输入框。
  const [uploadingCount, setUploadingCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [tools, setTools] = useState<ToolSpec[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("新任务");
  const [newScenario, setNewScenario] = useState<SecurityScenario>("general");
  const [newPrompt, setNewPrompt] = useState(taskStarterPrompts.general);
  const [newProviderId, setNewProviderId] = useState("");
  const [newAuthorizedTarget, setNewAuthorizedTarget] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsCategory, setSettingsCategory] = useState<SettingsCategory>("quick");
  const [evaluationOpen, setEvaluationOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const inspectorUserRunRef = useRef<string | null>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const compactSidebarReturnFocusRef = useRef<HTMLElement | null>(null);
  const [sidebarExpanded, setSidebarExpanded] = useState(initialSidebarExpanded);
  const [compactLayout, setCompactLayout] = useState(isCompactViewport);
  const [initialSetup, setInitialSetup] = useState(false);
  const [bootstrapReady, setBootstrapReady] = useState(false);
  // 上传是异步的。切换会话时先更新此 ref，旧会话的迟到响应不能混入新会话的
  // 待发送附件清单。
  const currentThreadIdRef = useRef<string | null>(null);
  const acknowledgedProviderNoticeRef = useRef<string | null>(null);
  const runControls = useRunControlActions({
    run: activeRun,
    setRun: setActiveRun,
    setBusy,
    setError,
    loadControl,
    connect,
  });
  const taskActions = useTaskActions({
    detail,
    providerConfigId: detail?.provider_config_id ?? null,
    setDetail,
    loadThreads,
    setError,
    onExecutionStarted: (run) => {
      setEvents([]);
      setReport(null);
      setControl(null);
      setActiveRun(run);
      connect(run);
    },
    onExecutionStopped: (run) => {
      if (!run) return;
      setActiveRun(run);
      void loadControl(run.id);
      // 运行中的停止请求先持久化为 running + stop_requested，真正的终态
      // 会随后通过 Run SSE 到达；不能把这类响应误当成已经停止。
      if (
        run.stop_requested &&
        !["completed", "failed", "stopped"].includes(run.status)
      )
        connect(run);
    },
    onRunInteraction: (run) => {
      setActiveRun(run);
      void loadControl(run.id);
      // submit_input / submit_clarification 的同步响应可能仍保留旧的等待状态，
      // 但恢复任务已经在后端排队。此时也必须尽早恢复 SSE，才能收到后续状态。
      if (!["completed", "failed", "stopped"].includes(run.status)) connect(run);
    },
  });
  const activeRunId = activeRun?.id;
  const uploading = uploadingCount > 0;
  const taskCanStop = Boolean(activeRun && [
    "queued",
    "running",
    "waiting_input",
    "waiting_clarification",
    "waiting_approval",
    "paused",
  ].includes(activeRun.status) && !activeRun.stop_requested);

  useEffect(() => {
    currentThreadIdRef.current = detail?.id ?? null;
  }, [detail?.id]);

  const refreshProviders = useCallback(async () => {
    const result = await api.listProviders();
    if (!Array.isArray(result)) {
      throw new Error("模型配置列表返回格式无效");
    }
    // 输入区只能选择已启用的配置；停用项仍可在设置中管理。
    setProviders(result.filter((provider) => provider.enabled));
  }, []);

  useEffect(() => {
    void bootstrap()
      .then((result) => {
        setInitialSetup(result.initialSetup);
        // 本机会话可在首次启动前就建立，但 Provider 等产品配置仍未完成时，
        // 必须自动打开设置中心，不能把“已认证”误判为“已配置”。
        setSettingsOpen(result.initialSetup || !result.authenticated);
        setBootstrapReady(result.authenticated);
      })
      .catch(() => {
        setBootstrapReady(false);
        setError("无法连接后端服务，请检查部署状态。");
      });
  }, [bootstrap]);

  useEffect(() => {
    if (!bootstrapReady) return;
    void refreshProviders().catch(() => setProviders([]));
  }, [bootstrapReady, refreshProviders]);

  useEffect(() => {
    if (providers.length === 0) {
      setNewProviderId("");
      return;
    }
    if (providers.some((provider) => provider.id === newProviderId)) return;
    setNewProviderId(
      providers.find((provider) => provider.is_default)?.id ?? providers[0].id,
    );
  }, [newProviderId, providers]);

  const refreshSkills = useCallback(async () => {
    const result = await api.listSkills();
    setSkills(Array.isArray(result) ? result.filter((skill) => skill.enabled) : []);
  }, []);

  useEffect(() => {
    if (!bootstrapReady) return;
    void refreshSkills().catch(() => setSkills([]));
  }, [bootstrapReady, refreshSkills]);

  const refreshTools = useCallback(async () => {
    const result = await api.tools();
    setTools(Array.isArray(result) ? result : []);
  }, []);

  useEffect(() => {
    if (!bootstrapReady) return;
    void refreshTools().catch(() => setTools([]));
  }, [bootstrapReady, refreshTools]);

  useEffect(() => {
    const notice = detail?.provider_fallback_notice;
    if (!detail || !notice) return;
    const key = `${detail.id}:${notice}`;
    if (acknowledgedProviderNoticeRef.current === key) return;
    acknowledgedProviderNoticeRef.current = key;
    setError(notice);
    void api
      .updateThread(detail.id, { acknowledge_provider_fallback: true })
      .then((updated) =>
        setDetail((current) =>
          current?.id === updated.id ? { ...current, ...updated } : current,
        ),
      )
      .catch(() => undefined);
  }, [detail, setDetail]);

  // 弹层均支持 Esc 退出，避免键盘用户被困在设置、创建任务或审计抽屉中。
  useEffect(() => {
    const closeOverlay = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // 只关闭最上层界面，避免从新建任务打开设置后按一次 Esc，连底层草稿
      // 一起丢失。
      if (settingsOpen) {
        setSettingsOpen(false);
        return;
      }
      if (evaluationOpen) {
        setEvaluationOpen(false);
        return;
      }
      if (inspectorOpen) {
        if (activeRunId) inspectorUserRunRef.current = activeRunId;
        setInspectorOpen(false);
        return;
      }
      if (compactLayout && sidebarExpanded) {
        setSidebarExpanded(false);
        return;
      }
      if (createOpen) setCreateOpen(false);
    };
    window.addEventListener("keydown", closeOverlay);
    return () => window.removeEventListener("keydown", closeOverlay);
  }, [activeRunId, compactLayout, createOpen, evaluationOpen, inspectorOpen, settingsOpen, sidebarExpanded]);

  useEffect(() => {
    if (!compactLayout || !sidebarExpanded) return undefined;
    compactSidebarReturnFocusRef.current = document.activeElement as HTMLElement | null;
    const sidebar = sidebarRef.current;
    const focusable = () => Array.from(
      sidebar?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    ).filter((item) => !item.hasAttribute("hidden"));
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
    sidebar?.addEventListener("keydown", trapFocus);
    return () => {
      sidebar?.removeEventListener("keydown", trapFocus);
      compactSidebarReturnFocusRef.current?.focus();
      compactSidebarReturnFocusRef.current = null;
    };
  }, [compactLayout, sidebarExpanded]);

  useEffect(() => {
    if (!compactLayout)
      window.localStorage?.setItem(
        "yuwang.sidebarExpanded",
        String(sidebarExpanded),
      );
  }, [compactLayout, sidebarExpanded]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia("(max-width: 700px)");
    const syncLayout = (matches: boolean) => {
      setCompactLayout(matches);
      setSidebarExpanded(
        matches
          ? false
          : window.localStorage?.getItem("yuwang.sidebarExpanded") !== "false",
      );
    };
    const onChange = (event: MediaQueryListEvent) => syncLayout(event.matches);
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, []);

  async function createThread() {
    // 新 Thread 不能继承旧会话尚未完成的请求、草稿或上传响应。
    taskActions.reset();
    setMessage("");
    setAuthorizedTarget("");
    setPendingArtifacts([]);
    setBusy(true);
    setError("");
    let createdThreadId: string | null = null;
    try {
      const value = await api.createThread(newTitle, [], newScenario);
      createdThreadId = value.id;
      if (newProviderId) {
        await api.updateThread(value.id, { provider_config_id: newProviderId });
      }
      await loadThreads();
      currentThreadIdRef.current = value.id;
      await selectThread(value.id);
      const authorizedTargets = newAuthorizedTarget
        .split(/[\n,]/)
        .map((target) => target.trim())
        .filter(Boolean);
      await api.message(
        value.id,
        {
          request_id: crypto.randomUUID(),
          content: newPrompt.trim(),
          artifact_ids: [],
          provider_config_id: newProviderId || null,
          authorized_targets: authorizedTargets,
        },
        new AbortController().signal,
        (event) => {
          if (event.type === "execution_started") {
            setEvents([]);
            setReport(null);
            setControl(null);
            setActiveRun(event.data.run);
            setCreateOpen(false);
            connect(event.data.run);
          }
          if (event.type === "execution_stopped") {
            if (event.data.run) setActiveRun(event.data.run);
            setCreateOpen(false);
          }
          if (
            event.type === "guidance_queued" ||
            event.type === "input_received" ||
            event.type === "clarification_received"
          ) {
            setActiveRun(event.data.run);
            setCreateOpen(false);
            connect(event.data.run);
          }
        },
      );
      setDetail(await api.detail(value.id));
      setCreateOpen(false);
      setNewAuthorizedTarget("");
    } catch (cause) {
      if (createdThreadId) {
        setCreateOpen(false);
        await selectThread(createdThreadId).catch(() => undefined);
        setError(`任务已创建，但启动失败：${String(cause)}`);
      } else {
        setError(String(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  async function upload(file?: File) {
    if (!detail || !file) return;
    const threadId = detail.id;
    setUploadingCount((count) => count + 1);
    setError("");
    try {
      const artifact = await api.upload(threadId, file);
      if (currentThreadIdRef.current === threadId) {
        setPendingArtifacts((items) => [...items, artifact]);
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setUploadingCount((count) => Math.max(0, count - 1));
    }
  }

  async function send() {
    if (!detail || !message.trim()) return;
    const content = message.trim();
    const artifacts = pendingArtifacts;
    const authorizedTargets = authorizedTarget
      .split(/[\n,]/)
      .map((target) => target.trim())
      .filter(Boolean);
    // 网络失败时保留文字和待发送附件，让用户能确认并重试；只有统一消息
    // 接口确认受理后才清空草稿，避免形成“附件好像上传/发送了”的错觉。
    if (await taskActions.send(content, artifacts, authorizedTargets)) {
      setMessage("");
      setPendingArtifacts([]);
      setAuthorizedTarget("");
    }
  }

  async function selectProvider(providerId: string) {
    if (!detail || !providerId || providerId === detail.provider_config_id) return;
    setError("");
    try {
      const updated = await api.updateThread(detail.id, { provider_config_id: providerId });
      setDetail((current) =>
        current?.id === updated.id ? { ...current, ...updated } : current,
      );
      await loadThreads();
    } catch (cause) {
      setError(String(cause));
    }
  }

  function selectThreadFromSidebar(id: string) {
    setError("");
    taskActions.reset();
    currentThreadIdRef.current = id;
    setMessage("");
    setPendingArtifacts([]);
    if (typeof window.matchMedia === "function" && window.matchMedia("(max-width: 700px)").matches)
      setSidebarExpanded(false);
    void selectThread(id);
  }

  async function selectSkills(skillIds: string[]) {
    if (!detail) return;
    const current = detail.skill_ids ?? [];
    if (current.length === skillIds.length && current.every((id) => skillIds.includes(id))) return;
    setError("");
    try {
      const updated = await api.updateThread(detail.id, { skill_ids: skillIds });
      setDetail((currentDetail) =>
        currentDetail?.id === updated.id ? { ...currentDetail, ...updated } : currentDetail,
      );
      await loadThreads();
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function selectTools(mode: "inherit" | "selected", toolIds: string[]) {
    if (!detail) return;
    const unchanged =
      detail.tool_selection_mode === mode &&
      detail.tool_ids.length === toolIds.length &&
      detail.tool_ids.every((id) => toolIds.includes(id));
    if (unchanged) return;
    setError("");
    try {
      const updated = await api.updateThread(detail.id, {
        tool_selection_mode: mode,
        tool_ids: toolIds,
      });
      setDetail((current) =>
        current?.id === updated.id ? { ...current, ...updated } : current,
      );
      await loadThreads();
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function retry() {
    if (!activeRun) return;
    const run = await api.retry(activeRun.id);
    setEvents([]);
    setReport(null);
    setActiveRun(run);
    connect(run);
  }

  async function retryMessage() {
    // 首次发送失败时草稿会保留；同一 request_id 重试成功后必须同步清空，
    // 否则用户可能以为尚未发出而再次创建一条新请求。
    if (await taskActions.retry()) {
      setMessage("");
      setPendingArtifacts([]);
    }
  }

  async function editPlan(plan: AgentPlan, version: number, reason: string) {
    if (!activeRun) return;
    setBusy(true);
    setError("");
    try {
      await api.editPlan(
        activeRun.id,
        plan,
        version,
        reason,
        crypto.randomUUID(),
      );
      await loadControl(activeRun.id);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function decidePlan(
    decision: "approve" | "reject",
    version: number,
    reason: string,
  ) {
    if (!activeRun) return;
    setBusy(true);
    setError("");
    try {
      const run = await api.decidePlan(
        activeRun.id,
        decision,
        version,
        reason,
        crypto.randomUUID(),
      );
      setActiveRun(run);
      await loadControl(run.id);
      connect(run);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function toggleMemory(enabled: boolean) {
    if (!detail) return;
    await api.toggleMemories(detail.id, enabled);
    setMemories(await api.memories(detail.id));
  }

  async function removeMemory(id: string) {
    if (!detail) return;
    await api.deleteMemory(detail.id, id);
    setMemories(await api.memories(detail.id));
  }

  async function clearMemory() {
    if (!detail) return;
    await api.clearMemories(detail.id);
    setMemories([]);
  }

  async function renameThread(thread: Thread) {
    const title = window.prompt("输入新的任务名称", thread.title)?.trim();
    if (!title || title === thread.title) return;
    await api.updateThread(thread.id, { title });
    await loadThreads();
    if (detail?.id === thread.id) setDetail({ ...detail, title });
  }

  async function toggleArchive(thread: Thread) {
    await api.updateThread(thread.id, { archived: !thread.archived });
    await loadThreads();
    if (detail?.id === thread.id) setDetail(null);
  }

  async function removeThread(thread: Thread) {
    if (
      !window.confirm(
        `永久删除“${thread.title}”及其消息、运行和审计记录？此操作无法撤销。`,
      )
    )
      return;
    await api.deleteThread(thread.id);
    await loadThreads();
    if (detail?.id === thread.id) {
      window.localStorage?.removeItem("yuwang.currentThreadId");
      setDetail(null);
    }
  }

  const metrics = useMemo(
    () => ({
      tools: events.filter((item) => item.type === "tool_finished").length,
      replans: events.filter((item) => item.type === "replanned").length,
      events: events.length,
    }),
    [events],
  );

  function openStarter(title: string, scenario: SecurityScenario) {
    setNewTitle(title);
    setNewScenario(scenario);
    setNewPrompt(taskStarterPrompts[scenario]);
    setNewProviderId(
      providers.find((provider) => provider.is_default)?.id ?? providers[0]?.id ?? "",
    );
    setNewAuthorizedTarget("");
    setInspectorOpen(false);
    if (typeof window.matchMedia === "function" && window.matchMedia("(max-width: 700px)").matches)
      setSidebarExpanded(false);
    setCreateOpen(true);
  }

  function openSettings(category: SettingsCategory = "quick") {
    setSettingsCategory(category);
    setSettingsOpen(true);
  }

  return (
    <div
      className={`shell ${sidebarExpanded ? "sidebar-expanded" : "sidebar-collapsed"}`}
    >
      <aside ref={sidebarRef} id="app-sidebar" className="sidebar" aria-label="主导航">
        <div className="brand">
          <span className="brand-mark">御</span>
          <div>
            <h1>御网智元</h1>
            <p>安全智能体工作台</p>
          </div>
          <IconButton
            className="sidebar-close"
            icon={PanelLeftClose}
            label="收起侧栏"
            aria-controls="app-sidebar"
            aria-expanded={sidebarExpanded}
            onClick={() => setSidebarExpanded(false)}
          />
        </div>

        <button className="primary sidebar-action" onClick={() => openStarter("新任务", "general")}>
          <FilePlus2 size={18} aria-hidden="true" />
          <span>新建任务</span>
        </button>

        <nav className="sidebar-global-nav" aria-label="工作台入口">
          <button type="button" onClick={() => openSettings("marketplace")}>
            <Boxes size={18} aria-hidden="true" />
            <span>能力中心</span>
          </button>
          <button type="button" onClick={() => setEvaluationOpen(true)}>
            <ClipboardCheck size={18} aria-hidden="true" />
            <span>评测中心</span>
          </button>
        </nav>

        <section className="project-summary" aria-label="当前项目">
          <div className="section-label">当前项目</div>
          <div className="project-card">
            <span className="project-icon"><FolderOpen size={18} aria-hidden="true" /></span>
            <span>
              <strong>安全分析工作区</strong>
              <small>{threads.filter((thread) => !thread.archived).length} 个任务</small>
            </span>
          </div>
        </section>

        <ThreadSidebar
          threads={threads}
          selectedId={detail?.id}
          onSelect={selectThreadFromSidebar}
          onRename={(thread) => void renameThread(thread)}
          onToggleArchive={(thread) => void toggleArchive(thread)}
          onDelete={(thread) => void removeThread(thread)}
        />

        <div className="sidebar-footer">
          <button type="button" className="sidebar-settings" onClick={() => openSettings("quick")}>
            <Settings size={18} aria-hidden="true" />
            <span>设置中心</span>
          </button>
          <div className="security-note">
            <ShieldCheck size={18} aria-hidden="true" />
            <div>
              <strong>安全边界已启用</strong>
              <p>公网默认拒绝 · 凭据自动脱敏</p>
            </div>
          </div>
        </div>
      </aside>
      {compactLayout && sidebarExpanded && (
        <button
          type="button"
          className="sidebar-scrim"
          aria-label="关闭导航"
          onClick={() => setSidebarExpanded(false)}
        />
      )}

      <main
        className="workspace"
        inert={compactLayout && sidebarExpanded}
        aria-hidden={compactLayout && sidebarExpanded ? "true" : undefined}
      >
        <header className="topbar">
          <div className="topbar-heading">
            {!sidebarExpanded && (
              <IconButton
                className="navigation-toggle"
                icon={Menu}
                label="展开侧栏"
                aria-controls="app-sidebar"
                aria-expanded={sidebarExpanded}
                onClick={() => setSidebarExpanded(true)}
              />
            )}
            <div className="topbar-title" data-testid="thread-heading">
              <span className="eyebrow">任务</span>
              <h2>{createOpen ? "新建安全任务" : detail?.title ?? "选择或创建一个任务"}</h2>
              {createOpen ? (
                <small>确认场景、目标与运行配置后立即开始</small>
              ) : detail && (
                <small>
                  {activeRun?.status === "completed" ? "任务已完成" : activeRun?.status === "failed" ? "任务失败" : activeRun?.status === "stopped" ? "任务已停止" : activeRun?.status === "waiting_input" ? "等待输入" : activeRun ? "执行中" : "提交任务后由 Agent 自主执行"}
                </small>
              )}
            </div>
          </div>
          <div className="topbar-actions">
            {!createOpen && detail && (
              <div className="top-meta" data-testid="thread-status">
                {activeRun && <StatusBadge status={activeRun.status} />}
              </div>
            )}
            {!createOpen && activeRun && (
              <button
              className="inspector-toggle"
              aria-expanded={inspectorOpen}
              aria-controls="run-inspector"
              onClick={() => {
                inspectorUserRunRef.current = activeRun.id;
                setInspectorOpen((value) => !value);
              }}
            >
              <Activity size={17} aria-hidden="true" />
              运行审计
              </button>
            )}
          </div>
        </header>
        {createOpen ? (
          <CreateThreadDialog
            title={newTitle}
            prompt={newPrompt}
            scenario={newScenario}
            providerConfigId={newProviderId}
            authorizedTarget={newAuthorizedTarget}
            providers={providers}
            busy={busy}
            onTitleChange={setNewTitle}
            onPromptChange={setNewPrompt}
            onScenarioChange={(scenario) => {
              setNewScenario(scenario);
              setNewPrompt(taskStarterPrompts[scenario]);
            }}
            onProviderChange={setNewProviderId}
            onAuthorizedTargetChange={setNewAuthorizedTarget}
            onOpenSettings={(category) => openSettings(category)}
            onCancel={() => setCreateOpen(false)}
            onSubmit={() => void createThread()}
          />
        ) : !detail ? (
          <section className="empty empty-onboarding">
            <span className="eyebrow">安全工作台</span>
            <h2>从一个明确的安全场景开始</h2>
            <p>选择场景后，再上传材料、确认模型和授权目标。Agent 会先给出计划，再调用受控工具。</p>
            <div className="starter-grid">
              <button type="button" onClick={() => openStarter("CTF 题目分析", "ctf")}><Flag size={20} aria-hidden="true" /><span><strong>CTF 题目</strong><small>附件识别、解码、取证与 Flag 验证</small></span></button>
              <button type="button" onClick={() => openStarter("应急响应日志分析", "incident_response")}><Siren size={20} aria-hidden="true" /><span><strong>应急响应</strong><small>日志归一化、时间线与 IOC 提取</small></span></button>
              <button type="button" onClick={() => openStarter("Web 漏洞研判", "vulnerability_analysis")}><Bug size={20} aria-hidden="true" /><span><strong>漏洞分析</strong><small>证据、CWE 映射与修复验证</small></span></button>
              <button type="button" onClick={() => openStarter("可疑样本静态分析", "reverse_static")}><Binary size={20} aria-hidden="true" /><span><strong>静态逆向</strong><small>不执行样本，分析结构、导入和字符串</small></span></button>
            </div>
            <div className="starter-actions">
              <button className="primary" onClick={() => openStarter("新任务", "general")}>创建通用任务</button>
              <button onClick={() => openSettings("providers")}><Settings size={17} aria-hidden="true" />先配置模型与能力</button>
            </div>
          </section>
        ) : (
          <>
            <ConversationView
              detail={detail}
              events={events}
              report={report}
              run={activeRun}
              audit={audit}
              control={control}
              busy={busy}
              taskFailure={taskActions.failure}
              onEditPlan={(plan, version, reason) =>
                void editPlan(plan, version, reason)
              }
              onDecidePlan={(decision, version, reason) =>
                void decidePlan(decision, version, reason)
              }
              onPause={runControls.pause}
              onResume={runControls.resume}
            />
            <MessageComposer
              activeRun={activeRun}
              message={message}
              authorizedTarget={authorizedTarget}
              pendingArtifacts={pendingArtifacts}
              providers={providers}
              providerConfigId={detail.provider_config_id}
              uploading={uploading}
              taskSubmitting={taskActions.submitting}
              taskCanRetry={Boolean(taskActions.failure?.retryable)}
              onMessageChange={setMessage}
              onAuthorizedTargetChange={setAuthorizedTarget}
              onProviderChange={(providerId) => void selectProvider(providerId)}
              onUpload={(file) => void upload(file)}
              onSend={() => void send()}
              onStop={() =>
                taskCanStop ? void taskActions.stopRun() : taskActions.cancelResponse()
              }
              onRetry={() => void retry()}
              onTaskRetry={() => void retryMessage()}
            >
              <SkillSelector
                skills={skills}
                value={detail.skill_ids ?? []}
                disabled={uploading || taskActions.submitting}
                onChange={(skillIds) => void selectSkills(skillIds)}
              />
              <ToolSelector
                tools={tools}
                mode={detail.tool_selection_mode ?? "inherit"}
                value={detail.tool_ids ?? []}
                disabled={uploading || taskActions.submitting}
                onChange={(mode, toolIds) => void selectTools(mode, toolIds)}
              />
            </MessageComposer>
          </>
        )}
        {error && (
          <div role="alert" className="toast">
            {error}
          </div>
        )}
      </main>

      {!createOpen && activeRun && (
        <InspectorPanel
        open={inspectorOpen}
        metrics={metrics}
        audit={audit}
        events={events}
        detail={detail}
        memories={memories}
        onClose={() => {
          inspectorUserRunRef.current = activeRun.id;
          setInspectorOpen(false);
        }}
        onToggleMemory={(value) => void toggleMemory(value)}
        onDeleteMemory={(id) => void removeMemory(id)}
        onClearMemories={() => void clearMemory()}
        />
      )}

      {settingsOpen && (
        <SettingsCenter
          initialSetup={initialSetup}
          initialCategory={settingsCategory}
          onClose={() => setSettingsOpen(false)}
          onChanged={async () => {
            await refreshProviders();
            await refreshSkills();
            await refreshTools();
            if (detail) await selectThread(detail.id);
            const status = await api.setupStatus();
            setInitialSetup(!status.configured);
          }}
        />
      )}
      {evaluationOpen && (
        <div className="settings-backdrop" role="dialog" aria-modal="true" aria-label="评测中心">
          <section className="evaluation-panel"><header><div><span className="eyebrow">质量验证</span><h2>评测中心</h2></div><IconButton icon={X} label="关闭评测中心" onClick={() => setEvaluationOpen(false)} /></header><div className="settings-scroll"><EvaluationResults onError={setError} /></div></section>
        </div>
      )}
    </div>
  );
}
