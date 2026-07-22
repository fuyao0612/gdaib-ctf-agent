/** 工作台数据与 Run/SSE 生命周期；页面组件只协调用户动作和弹层。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type {
  ChatDefaults,
  Event,
  MemoryRecord,
  Report,
  Run,
  RunAudit,
  RunControl,
  Thread,
  ThreadDetail,
} from "../types";

const terminalStatuses = new Set(["completed", "failed", "stopped"]);
const waitingEvents = new Set([
  "run_waiting_input",
  "clarification_requested",
  "plan_approval_requested",
  "run_paused",
]);

export function useWorkbenchData() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [audit, setAudit] = useState<RunAudit | null>(null);
  const [control, setControl] = useState<RunControl | null>(null);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [chatDefaults, setChatDefaults] = useState<ChatDefaults | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const streamRunRef = useRef("");
  const latestSequenceRef = useRef(0);

  const loadThreads = useCallback(async () => {
    const values = await api.listThreads();
    setThreads(values);
    return values;
  }, []);

  const refreshSettings = useCallback(async () => {
    const preferences = await api.chatPreferences();
    setChatDefaults(preferences);
  }, []);

  const loadControl = useCallback(async (runId: string) => {
    const value = await api.control(runId);
    setControl(value);
    return value;
  }, []);

  const connect = useCallback(
    (run: Run) => {
      // SSE 只传递追加事件，刷新后的权威状态仍从持久化详情和审计接口恢复。
      sourceRef.current?.close();
      if (streamRunRef.current !== run.id) {
        streamRunRef.current = run.id;
        latestSequenceRef.current = 0;
      }
      const source = new EventSource(
        `/api/v1/runs/${run.id}/events/stream?after=${latestSequenceRef.current}`,
      );
      sourceRef.current = source;
      source.onmessage = (messageEvent) => {
        const event = JSON.parse(messageEvent.data) as Event;
        latestSequenceRef.current = Math.max(
          latestSequenceRef.current,
          event.sequence,
        );
        setEvents((previous) =>
          previous.some((item) => item.sequence === event.sequence)
            ? previous
            : [...previous, event],
        );
        if (event.type === "run_started")
          setActiveRun((current) =>
            current?.id === run.id
              ? {
                  ...current,
                  status: "running",
                  started_at: current.started_at ?? event.timestamp,
                }
              : current,
          );
        void api.audit(run.id).then(setAudit);
        void loadControl(run.id);
        if (waitingEvents.has(event.type)) {
          source.close();
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
    [loadControl, loadThreads],
  );

  const selectThread = useCallback(
    async (id: string) => {
      sourceRef.current?.close();
      setReport(null);
      setControl(null);
      window.localStorage?.setItem("yuwang.currentThreadId", id);
      const value = await api.detail(id);
      setDetail(value);
      setMemories(await api.memories(id));
      const run = value.runs.at(-1) ?? null;
      setActiveRun(run);
      if (!run) {
        streamRunRef.current = "";
        latestSequenceRef.current = 0;
        setEvents([]);
        setAudit(null);
        return;
      }
      const [runEvents, runAudit] = await Promise.all([
        api.events(run.id),
        api.audit(run.id),
        loadControl(run.id),
      ]);
      streamRunRef.current = run.id;
      latestSequenceRef.current = runEvents.at(-1)?.sequence ?? 0;
      setEvents(runEvents);
      setAudit(runAudit);
      if (["completed", "failed"].includes(run.status)) {
        setReport(await api.report(run.id).catch(() => null));
      } else if (["queued", "running"].includes(run.status)) {
        // 刷新或重新选择会话后必须恢复实时订阅，否则页面会停在旧检查点。
        connect(run);
      }
    },
    [connect, loadControl],
  );

  const bootstrap = useCallback(async () => {
    const status = await api.setupStatus();
    try {
      await api.adminSession();
      const [values, preferences] = await Promise.all([
        loadThreads(),
        api.chatPreferences(),
      ]);
      setChatDefaults(preferences);
      const remembered = window.localStorage?.getItem("yuwang.currentThreadId");
      if (remembered && values.some((item) => item.id === remembered))
        await selectThread(remembered);
      return { initialSetup: !status.configured, authenticated: true };
    } catch {
      return { initialSetup: !status.configured, authenticated: false };
    }
  }, [loadThreads, selectThread]);

  useEffect(() => () => sourceRef.current?.close(), []);

  return {
    threads,
    detail,
    events,
    activeRun,
    report,
    audit,
    control,
    memories,
    chatDefaults,
    setDetail,
    setEvents,
    setActiveRun,
    setReport,
    setControl,
    setMemories,
    loadThreads,
    refreshSettings,
    loadControl,
    selectThread,
    connect,
    bootstrap,
  };
}
