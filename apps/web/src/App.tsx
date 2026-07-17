/** 单页工作台协调器：只管理共享状态和网络动作，页面区域由小组件渲染。 */
import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import SettingsCenter from "./SettingsCenter";
import CreateThreadDialog from "./components/CreateThreadDialog";
import MessageComposer from "./components/MessageComposer";
import {
  ConversationView,
  InspectorPanel,
  StatusBadge,
} from "./components/RunViews";
import ThreadSidebar from "./components/ThreadSidebar";
import { useChatActions } from "./hooks/useChatActions";
import { useWorkbenchData } from "./hooks/useWorkbenchData";
import { useRunControlActions } from "./hooks/useRunControlActions";
import type {
  AgentPlan,
  Artifact,
  InteractionMode,
  Mode,
  PlanMode,
  Thread,
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
    providers,
    agentProfiles,
    selectedProfileId,
    selectedProviderId,
    chatDefaults,
    setDetail,
    setEvents,
    setActiveRun,
    setReport,
    setControl,
    setMemories,
    setSelectedProfileId,
    setSelectedProviderId,
    loadThreads,
    refreshSettings,
    loadControl,
    selectThread,
    connect,
    bootstrap,
  } = workspace;
  const [message, setMessage] = useState("");
  const [successPattern, setSuccessPattern] = useState("");
  const [pendingArtifacts, setPendingArtifacts] = useState<Artifact[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("新对话");
  const [newMode, setNewMode] = useState<Mode>("normal");
  const [newPlanMode, setNewPlanMode] = useState<PlanMode>("approval");
  const [newInteractionMode, setNewInteractionMode] =
    useState<InteractionMode>("chat");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [sidebarExpanded, setSidebarExpanded] = useState(
    () => window.localStorage?.getItem("yuwang.sidebarExpanded") !== "false",
  );
  const [initialSetup, setInitialSetup] = useState(false);
  const [supplementalInput, setSupplementalInput] = useState("");
  const runControls = useRunControlActions({
    run: activeRun,
    setRun: setActiveRun,
    setBusy,
    setError,
    loadControl,
    connect,
  });
  const chat = useChatActions({ detail, setDetail, loadThreads, setError });

  useEffect(() => {
    void bootstrap()
      .then((result) => {
        setInitialSetup(result.initialSetup);
        setSettingsOpen(!result.authenticated);
      })
      .catch(() => setError("无法连接后端服务，请检查部署状态。"));
  }, [bootstrap]);

  // 弹层均支持 Esc 退出，避免键盘用户被困在设置、创建任务或审计抽屉中。
  useEffect(() => {
    const closeOverlay = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setInspectorOpen(false);
      setCreateOpen(false);
      setSettingsOpen(false);
    };
    window.addEventListener("keydown", closeOverlay);
    return () => window.removeEventListener("keydown", closeOverlay);
  }, []);

  useEffect(() => {
    window.localStorage?.setItem(
      "yuwang.sidebarExpanded",
      String(sidebarExpanded),
    );
  }, [sidebarExpanded]);

  useEffect(() => {
    if (!chatDefaults) return;
    setNewInteractionMode(chatDefaults.default_mode);
    setSidebarExpanded(chatDefaults.sidebar_expanded);
    if (detail?.interaction_mode === "agent")
      setInspectorOpen(chatDefaults.audit_expanded);
  }, [chatDefaults, detail?.interaction_mode]);

  async function createThread() {
    setBusy(true);
    setError("");
    try {
      const value = await api.createThread(
        newTitle,
        newMode,
        selectedProfileId,
        newPlanMode,
        newInteractionMode,
      );
      await loadThreads();
      await selectThread(value.id);
      setPendingArtifacts([]);
      setCreateOpen(false);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function upload(file?: File) {
    if (!detail || !file) return;
    setBusy(true);
    try {
      const artifact = await api.upload(detail.id, file);
      setPendingArtifacts((items) => [...items, artifact]);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function sendAndRun() {
    // turn 接口在一个后端用例中保存用户消息并创建 Run；拿到 202 后再订阅 SSE，
    // 避免“消息已显示但运行未创建”这类前后端中间状态。
    if (
      !detail ||
      !message.trim() ||
      !selectedProviderId ||
      (needsEvidencePattern && !successPattern.trim())
    )
      return;
    setBusy(true);
    setError("");
    setEvents([]);
    setReport(null);
    setControl(null);
    try {
      const run = await api.turn(
        detail.id,
        message,
        pendingArtifacts.map((item) => item.id),
        selectedProviderId,
        successPattern,
      );
      setActiveRun(run);
      setMessage("");
      setPendingArtifacts([]);
      connect(run);
      setDetail(await api.detail(detail.id));
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    if (!detail || !message.trim() || !selectedProviderId) return;
    if (detail.interaction_mode === "agent") {
      await sendAndRun();
      return;
    }
    const content = message.trim();
    const artifacts = pendingArtifacts;
    setMessage("");
    setPendingArtifacts([]);
    await chat.send(content, artifacts, selectedProviderId);
  }

  async function switchInteractionMode(value: InteractionMode) {
    if (!detail || detail.interaction_mode === value) return;
    setDetail({ ...detail, interaction_mode: value });
    try {
      await api.updateThread(detail.id, { interaction_mode: value });
      await loadThreads();
    } catch (cause) {
      setDetail(detail);
      setError(String(cause));
    }
  }

  async function stop() {
    if (!activeRun) return;
    await api.stop(activeRun.id);
    setActiveRun({ ...activeRun, stop_requested: true });
  }

  async function retry() {
    if (!activeRun) return;
    const run = await api.retry(activeRun.id);
    setEvents([]);
    setReport(null);
    setActiveRun(run);
    connect(run);
  }

  async function submitSupplement() {
    if (!activeRun || !supplementalInput.trim()) return;
    const run = await api.submitInput(activeRun.id, supplementalInput);
    setSupplementalInput("");
    setActiveRun(run);
    connect(run);
  }

  async function submitClarification(content: string, briefVersion: number) {
    if (!activeRun) return;
    setBusy(true);
    setError("");
    try {
      const run = await api.submitClarification(
        activeRun.id,
        content,
        briefVersion,
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
    const title = window.prompt("输入新的对话名称", thread.title)?.trim();
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

  const running = Boolean(
    activeRun &&
      [
        "queued",
        "running",
        "waiting_input",
        "waiting_clarification",
        "waiting_approval",
        "paused",
      ].includes(activeRun.status),
  );
  const inputLocked = detail?.mode === "competition" && running;
  const interactionMode = detail?.interaction_mode ?? "chat";
  const currentProfile = agentProfiles.find(
    (value) => value.profile_id === detail?.agent_profile_id,
  );
  const needsEvidencePattern = currentProfile?.completion_mode === "evidence";
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
          ＋ 新建对话
        </button>
        <button
          className="settings-button full"
          onClick={() => setSettingsOpen(true)}
        >
          ⚙ 设置中心
        </button>
        <div className="section-label">历史对话</div>
        <ThreadSidebar
          threads={threads}
          selectedId={detail?.id}
          onSelect={(id) => {
            setError("");
            chat.reset();
            setPendingArtifacts([]);
            void selectThread(id);
          }}
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
              <span className="eyebrow">THREAD</span>
              <h2>{detail?.title ?? "选择或创建一个对话"}</h2>
              {detail && (
                <small>
                  {interactionMode === "chat"
                    ? "自然语言对话"
                    : `${currentProfile?.name ?? "Agent 配置"} · v${detail.agent_profile_version ?? "?"}`}
                </small>
              )}
            </div>
          </div>
          <div className="topbar-actions">
            {detail && (
              <div className="top-meta" data-testid="thread-status">
                <span className="mode">
                  {interactionMode === "chat" ? "对话" : "Agent 任务"}
                </span>
                {interactionMode === "agent" && (
                  <span className="mode">
                    {detail.mode === "competition" ? "竞赛限制" : "普通执行"}
                  </span>
                )}
                {interactionMode === "agent" && activeRun && (
                  <>
                    <span className="mode">{activeRun.provider}</span>
                    <span className="mode">{activeRun.evidence_level}</span>
                    <StatusBadge status={activeRun.status} />
                  </>
                )}
              </div>
            )}
            {interactionMode === "agent" && activeRun && (
              <button
              className="inspector-toggle"
              aria-expanded={inspectorOpen}
              aria-controls="run-inspector"
              onClick={() => setInspectorOpen((value) => !value)}
            >
              运行审计
              </button>
            )}
          </div>
        </header>
        {!detail ? (
          <section className="empty">
            <div className="radar">⌁</div>
            <h2>开始一段新对话</h2>
            <p>
              日常问题直接对话；需要计划、工具和验证时，再切换到 Agent 任务。
            </p>
            <button className="primary" onClick={() => setCreateOpen(true)}>
              创建第一个对话
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
              chatDraft={chat.draft}
              chatFailure={chat.failure}
              onClarify={(content, version) =>
                void submitClarification(content, version)
              }
              onEditPlan={(plan, version, reason) =>
                void editPlan(plan, version, reason)
              }
              onDecidePlan={(decision, version, reason) =>
                void decidePlan(decision, version, reason)
              }
              onPause={runControls.pause}
              onResume={runControls.resume}
              onGuidance={runControls.queueGuidance}
            />
            <MessageComposer
              activeRun={activeRun}
              interactionMode={interactionMode}
              events={events}
              message={message}
              supplementalInput={supplementalInput}
              pendingArtifacts={pendingArtifacts}
              providers={providers}
              selectedProviderId={selectedProviderId}
              successPattern={successPattern}
              needsEvidencePattern={needsEvidencePattern}
              advisoryMode={currentProfile?.completion_mode === "advisory"}
              inputLocked={inputLocked}
              running={running}
              chatGenerating={chat.generating}
              chatCanRetry={Boolean(chat.failure?.retryable)}
              busy={busy}
              onMessageChange={setMessage}
              onInteractionModeChange={(value) => void switchInteractionMode(value)}
              onSupplementChange={setSupplementalInput}
              onProviderChange={setSelectedProviderId}
              onPatternChange={setSuccessPattern}
              onUpload={(file) => void upload(file)}
              onSend={() => void send()}
              onStop={() =>
                chat.generating ? chat.stop() : void stop()
              }
              onRetry={() => void retry()}
              onChatRetry={() => void chat.retry()}
              onSubmitSupplement={() => void submitSupplement()}
            />
          </>
        )}
        {error && (
          <div role="alert" className="toast">
            {error}
          </div>
        )}
      </main>

      {interactionMode === "agent" && activeRun && (
        <InspectorPanel
        open={inspectorOpen}
        metrics={metrics}
        audit={audit}
        events={events}
        detail={detail}
        memories={memories}
        onClose={() => setInspectorOpen(false)}
        onToggleMemory={(value) => void toggleMemory(value)}
        onDeleteMemory={(id) => void removeMemory(id)}
        onClearMemories={() => void clearMemory()}
        />
      )}

      {createOpen && (
        <CreateThreadDialog
          title={newTitle}
          mode={newMode}
          planMode={newPlanMode}
          interactionMode={newInteractionMode}
          profileId={selectedProfileId}
          profiles={agentProfiles}
          busy={busy}
          onTitleChange={setNewTitle}
          onModeChange={setNewMode}
          onPlanModeChange={setNewPlanMode}
          onInteractionModeChange={setNewInteractionMode}
          onProfileChange={(id, mode) => {
            setSelectedProfileId(id);
            if (mode) setNewMode(mode);
          }}
          onCancel={() => setCreateOpen(false)}
          onSubmit={() => void createThread()}
        />
      )}
      {settingsOpen && (
        <SettingsCenter
          initialSetup={initialSetup}
          onClose={() => setSettingsOpen(false)}
          onChanged={async () => {
            await refreshSettings();
            const status = await api.setupStatus();
            setInitialSetup(!status.configured);
          }}
        />
      )}
    </div>
  );
}
