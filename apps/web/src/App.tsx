/** 单页工作台协调器：只管理共享状态和网络动作，页面区域由小组件渲染。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import SettingsCenter from "./SettingsCenter";
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
} from "./types";
import "./styles.css";
import "./thread-management.css";

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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const inspectorUserRunRef = useRef<string | null>(null);
  const [sidebarExpanded, setSidebarExpanded] = useState(
    () => window.localStorage?.getItem("yuwang.sidebarExpanded") !== "false",
  );
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
      // Esc 也能关闭设置或新建对话弹层；只有审计抽屉本来打开时才把它
      // 记为用户选择，避免无关弹层影响新 Run 的审计默认展开状态。
      if (inspectorOpen && activeRunId) inspectorUserRunRef.current = activeRunId;
      setInspectorOpen(false);
      setCreateOpen(false);
      setSettingsOpen(false);
    };
    window.addEventListener("keydown", closeOverlay);
    return () => window.removeEventListener("keydown", closeOverlay);
  }, [activeRunId, inspectorOpen]);

  useEffect(() => {
    window.localStorage?.setItem(
      "yuwang.sidebarExpanded",
      String(sidebarExpanded),
    );
  }, [sidebarExpanded]);

  async function createThread() {
    // 新 Thread 不能继承旧会话尚未完成的请求、草稿或上传响应。
    taskActions.reset();
    setMessage("");
    setAuthorizedTarget("");
    setPendingArtifacts([]);
    setBusy(true);
    setError("");
    try {
      const value = await api.createThread(newTitle);
      await loadThreads();
      currentThreadIdRef.current = value.id;
      await selectThread(value.id);
      setCreateOpen(false);
    } catch (cause) {
      setError(String(cause));
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

  return (
    <div
      className={`shell ${sidebarExpanded ? "sidebar-expanded" : "sidebar-collapsed"}`}
    >
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">御</span>
          <div>
            <h1>御网智元</h1>
            <p>安全 Agent 工作台</p>
          </div>
          <button
            className="sidebar-close"
            aria-label="收起侧栏"
            onClick={() => setSidebarExpanded(false)}
          >
            ‹
          </button>
        </div>
        <button className="primary full" onClick={() => setCreateOpen(true)}>
          ＋ 新建任务
        </button>
        <button
          className="settings-button full"
          onClick={() => setSettingsOpen(true)}
        >
          ⚙ 设置中心
        </button>
        <div className="section-label">任务历史</div>
        <ThreadSidebar
          threads={threads}
          selectedId={detail?.id}
          onSelect={selectThreadFromSidebar}
          onRename={(thread) => void renameThread(thread)}
          onToggleArchive={(thread) => void toggleArchive(thread)}
          onDelete={(thread) => void removeThread(thread)}
        />
        <div className="security-note">
          <span>●</span>
          <div>
            <strong>安全边界已启用</strong>
            <p>公网默认拒绝 · 凭据自动脱敏</p>
          </div>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="topbar-heading">
            {!sidebarExpanded && (
              <button
                className="navigation-toggle"
                aria-label="展开侧栏"
                onClick={() => setSidebarExpanded(true)}
              >
                ☰
              </button>
            )}
            <div className="topbar-title" data-testid="thread-heading">
              <span className="eyebrow">TASK</span>
              <h2>{detail?.title ?? "选择或创建一个任务"}</h2>
              {detail && (
                <small>
                  {activeRun?.status === "completed" ? "任务已完成" : activeRun?.status === "failed" ? "任务失败" : activeRun?.status === "stopped" ? "任务已停止" : activeRun?.status === "waiting_input" ? "等待输入" : activeRun ? "执行中" : "提交任务后由 Agent 自主执行"}
                </small>
              )}
            </div>
          </div>
          <div className="topbar-actions">
            {detail && (
              <div className="top-meta" data-testid="thread-status">
                {activeRun && <StatusBadge status={activeRun.status} />}
              </div>
            )}
            {activeRun && (
              <button
              className="inspector-toggle"
              aria-expanded={inspectorOpen}
              aria-controls="run-inspector"
              onClick={() => {
                inspectorUserRunRef.current = activeRun.id;
                setInspectorOpen((value) => !value);
              }}
            >
              运行审计
              </button>
            )}
          </div>
        </header>
        {!detail ? (
          <section className="empty">
            <div className="radar">⌁</div>
            <h2>开始一个新任务</h2>
            <p>
              创建任务后，由 Agent 决定计划、工具和验证步骤。
            </p>
            <button className="primary" onClick={() => setCreateOpen(true)}>
              创建第一个任务
            </button>
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

      {activeRun && (
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

      {createOpen && (
        <CreateThreadDialog
          title={newTitle}
          busy={busy}
          onTitleChange={setNewTitle}
          onCancel={() => setCreateOpen(false)}
          onSubmit={() => void createThread()}
        />
      )}
      {settingsOpen && (
        <SettingsCenter
          initialSetup={initialSetup}
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
    </div>
  );
}
