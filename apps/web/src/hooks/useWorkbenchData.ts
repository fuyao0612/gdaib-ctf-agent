/** 工作台数据与 Run/SSE 生命周期；页面组件只协调用户动作和弹层。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type {
  AgentProfileSummary,
  Event,
  MemoryRecord,
  ProviderConfig,
  Report,
  Run,
  RunAudit,
  Thread,
  ThreadDetail,
} from "../types";

const terminalStatuses = new Set(["completed", "failed", "stopped"]);

export function useWorkbenchData() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [audit, setAudit] = useState<RunAudit | null>(null);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [agentProfiles, setAgentProfiles] = useState<AgentProfileSummary[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [selectedProviderId, setSelectedProviderId] = useState("");
  const sourceRef = useRef<EventSource | null>(null);

  const loadThreads = useCallback(async () => {
    const values = await api.listThreads();
    setThreads(values);
    return values;
  }, []);

  const loadProviders = useCallback(async () => {
    const values = await api.listProviders();
    setProviders(values);
    setSelectedProviderId((current) =>
      current && values.some((value) => value.id === current)
        ? current
        : (values.find((value) => value.is_default)?.id ?? values[0]?.id ?? ""),
    );
  }, []);

  const loadProfiles = useCallback(async () => {
    const values = await api.listAgentProfiles();
    setAgentProfiles(values);
    setSelectedProfileId((current) =>
      current && values.some((value) => value.profile_id === current)
        ? current
        : (values.find((value) => value.is_default)?.profile_id ??
          values[0]?.profile_id ??
          ""),
    );
  }, []);

  const refreshSettings = useCallback(async () => {
    await Promise.all([loadProviders(), loadProfiles()]);
  }, [loadProviders, loadProfiles]);

  const selectThread = useCallback(async (id: string) => {
    sourceRef.current?.close();
    setReport(null);
    window.localStorage?.setItem("yuwang.currentThreadId", id);
    const value = await api.detail(id);
    setDetail(value);
    setMemories(await api.memories(id));
    const run = value.runs.at(-1) ?? null;
    setActiveRun(run);
    if (!run) {
      setEvents([]);
      setAudit(null);
      return;
    }
    setEvents(await api.events(run.id));
    setAudit(await api.audit(run.id));
    if (["completed", "failed"].includes(run.status))
      setReport(await api.report(run.id).catch(() => null));
  }, []);

  const connect = useCallback(
    (run: Run) => {
      // SSE 只传递追加事件，刷新后的权威状态仍从持久化详情和审计接口恢复。
      sourceRef.current?.close();
      const source = new EventSource(`/api/v1/runs/${run.id}/events/stream`);
      sourceRef.current = source;
      source.onmessage = (messageEvent) => {
        const event = JSON.parse(messageEvent.data) as Event;
        setEvents((previous) =>
          previous.some((item) => item.sequence === event.sequence)
            ? previous
            : [...previous, event],
        );
        void api.audit(run.id).then(setAudit);
        if (event.type === "run_waiting_input") {
          void api.detail(run.thread_id).then((value) => {
            setDetail(value);
            setActiveRun(value.runs.find((item) => item.id === run.id) ?? run);
          });
        }
        if (terminalStatuses.has(event.type.replace("run_", ""))) {
          source.close();
          void api.detail(run.thread_id).then((value) => {
            const latest = value.runs.find((item) => item.id === run.id) ?? run;
            setDetail(value);
            setActiveRun(latest);
            void loadThreads();
            void api.memories(run.thread_id).then(setMemories);
            if (["run_completed", "run_failed"].includes(event.type))
              void api.report(run.id).then(setReport).catch(() => setReport(null));
          });
        }
      };
      source.onerror = () => {
        if (source.readyState === EventSource.CLOSED) source.close();
      };
    },
    [loadThreads],
  );

  const bootstrap = useCallback(async () => {
    const status = await api.setupStatus();
    try {
      await api.adminSession();
      const [values] = await Promise.all([
        loadThreads(),
        loadProviders(),
        loadProfiles(),
      ]);
      const remembered = window.localStorage?.getItem("yuwang.currentThreadId");
      if (remembered && values.some((item) => item.id === remembered))
        await selectThread(remembered);
      return { initialSetup: !status.configured, authenticated: true };
    } catch {
      return { initialSetup: !status.configured, authenticated: false };
    }
  }, [loadThreads, loadProviders, loadProfiles, selectThread]);

  useEffect(() => () => sourceRef.current?.close(), []);

  return {
    threads,
    detail,
    events,
    activeRun,
    report,
    audit,
    memories,
    providers,
    agentProfiles,
    selectedProfileId,
    selectedProviderId,
    setDetail,
    setEvents,
    setActiveRun,
    setReport,
    setMemories,
    setSelectedProfileId,
    setSelectedProviderId,
    loadThreads,
    refreshSettings,
    selectThread,
    connect,
    bootstrap,
  };
}
