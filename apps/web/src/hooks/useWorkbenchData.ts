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
  // 会话详情、审计和 SSE 都是异步的。切换选择时递增代次，迟到的旧响应
  // 只能自行结束，不能覆盖当前 Thread 的数据区。
  const selectedThreadIdRef = useRef<string | null>(null);
  const selectionVersionRef = useRef(0);
  const controlRequestVersionRef = useRef(0);

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
    const selectionVersion = selectionVersionRef.current;
    const requestVersion = ++controlRequestVersionRef.current;
    const value = await api.control(runId);
    if (
      selectionVersion === selectionVersionRef.current &&
      requestVersion === controlRequestVersionRef.current
    )
      setControl(value);
    return value;
  }, []);

  const connect = useCallback(
    (run: Run) => {
      if (selectedThreadIdRef.current !== run.thread_id) return;
      // SSE 只传递追加事件，刷新后的权威状态仍从持久化详情和审计接口恢复。
      sourceRef.current?.close();
      sourceRef.current = null;
      if (streamRunRef.current !== run.id) {
        streamRunRef.current = run.id;
        latestSequenceRef.current = 0;
      }
      const source = new EventSource(
        `/api/v1/runs/${run.id}/events/stream?after=${latestSequenceRef.current}`,
      );
      sourceRef.current = source;
      const isCurrentRun = () =>
        selectedThreadIdRef.current === run.thread_id &&
        streamRunRef.current === run.id;
      const isCurrentStream = () =>
        isCurrentRun() && sourceRef.current === source;
      const closeStream = () => {
        source.close();
        if (sourceRef.current === source) sourceRef.current = null;
      };
      source.onmessage = (messageEvent) => {
        if (!isCurrentStream()) return;
        const event = JSON.parse(messageEvent.data) as Event;
        latestSequenceRef.current = Math.max(
          latestSequenceRef.current,
          event.sequence,
        );
        setEvents((previous) =>
          !isCurrentStream() || previous.some((item) => item.sequence === event.sequence)
            ? previous
            : [...previous, event],
        );
        if (event.type === "run_started")
          setActiveRun((current) =>
            // 停止请求的 HTTP 响应会先把界面更新为终态。旧 SSE 中迟到的
            // run_started 事件不能把已停止任务“复活”。
            isCurrentStream() &&
            current?.id === run.id && !terminalStatuses.has(current.status)
              ? {
                  ...current,
                  status: "running",
                  started_at: current.started_at ?? event.timestamp,
                }
              : current,
          );
        void api.audit(run.id).then((value) => {
          if (isCurrentRun()) setAudit(value);
        });
        void loadControl(run.id);
        if (waitingEvents.has(event.type)) {
          closeStream();
          void api.detail(run.thread_id).then((value) => {
            if (!isCurrentRun()) return;
            setDetail(value);
            setActiveRun(value.runs.find((item) => item.id === run.id) ?? run);
          });
        }
        if (terminalStatuses.has(event.type.replace("run_", ""))) {
          closeStream();
          void api.detail(run.thread_id).then((value) => {
            if (!isCurrentRun()) return;
            const latest = value.runs.find((item) => item.id === run.id) ?? run;
            setDetail(value);
            setActiveRun(latest);
            void loadThreads();
            void api.memories(run.thread_id).then((memories) => {
              if (isCurrentRun()) setMemories(memories);
            });
            if (["run_completed", "run_failed"].includes(event.type))
              void api.report(run.id)
                .then((value) => {
                  if (isCurrentRun()) setReport(value);
                })
                .catch(() => {
                  if (isCurrentRun()) setReport(null);
                });
          });
        }
      };
      source.onerror = () => {
        if (source.readyState === EventSource.CLOSED) closeStream();
      };
    },
    [loadControl, loadThreads],
  );

  const selectThread = useCallback(
    async (id: string) => {
      const selectionVersion = ++selectionVersionRef.current;
      const isCurrentSelection = () =>
        selectionVersion === selectionVersionRef.current &&
        selectedThreadIdRef.current === id;
      selectedThreadIdRef.current = id;
      sourceRef.current?.close();
      sourceRef.current = null;
      streamRunRef.current = "";
      latestSequenceRef.current = 0;
      controlRequestVersionRef.current += 1;
      // 切换过程中不保留旧会话的输入、Run 或控制面，避免用户在新旧详情交替时
      // 误把旧草稿或旧状态当作新会话内容。
      setDetail(null);
      setEvents([]);
      setActiveRun(null);
      setReport(null);
      setControl(null);
      setAudit(null);
      setMemories([]);
      window.localStorage?.setItem("yuwang.currentThreadId", id);
      const [value, threadMemories] = await Promise.all([
        api.detail(id),
        api.memories(id),
      ]);
      if (!isCurrentSelection()) return;
      setDetail(value);
      setMemories(threadMemories);
      const run = value.runs.at(-1) ?? null;
      setActiveRun(run);
      if (!run) {
        return;
      }
      streamRunRef.current = run.id;
      const [runEvents, runAudit] = await Promise.all([
        api.events(run.id),
        api.audit(run.id),
        loadControl(run.id),
      ]);
      if (!isCurrentSelection() || streamRunRef.current !== run.id) return;
      latestSequenceRef.current = runEvents.at(-1)?.sequence ?? 0;
      setEvents(runEvents);
      setAudit(runAudit);
      if (["completed", "failed"].includes(run.status)) {
        const latestReport = await api.report(run.id).catch(() => null);
        if (isCurrentSelection() && streamRunRef.current === run.id)
          setReport(latestReport);
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
