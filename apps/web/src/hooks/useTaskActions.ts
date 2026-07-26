/** 统一消息入口的 fetch-SSE 生命周期；Run 的实时事件仍由 EventSource 接收。 */
import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "../api";
import type { Artifact, Message, Run, ThreadDetail } from "../types";

export interface TaskRequestFailure {
  message: string;
  retryable: boolean;
  requestId: string;
  threadId: string;
  content: string;
  artifactIds: string[];
  authorizedTargets: string[];
}

interface Options {
  detail: ThreadDetail | null;
  providerConfigId: string | null;
  setDetail: Dispatch<SetStateAction<ThreadDetail | null>>;
  loadThreads: () => Promise<unknown>;
  setError: Dispatch<SetStateAction<string>>;
  onExecutionStarted: (run: Run) => void;
  onExecutionStopped: (run: Run | undefined) => void;
  onRunInteraction: (run: Run) => void;
}

export function useTaskActions(options: Options) {
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<TaskRequestFailure | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  // fetch-SSE 的回调在 abort 后仍可能迟到。用会话和请求代次共同判定归属，
  // 让旧会话永远不能写入刚切换到的新会话。
  const currentThreadIdRef = useRef<string | null>(options.detail?.id ?? null);
  const requestVersionRef = useRef(0);
  const detailThreadId = options.detail?.id ?? null;
  useEffect(() => {
    if (currentThreadIdRef.current === detailThreadId) return;
    currentThreadIdRef.current = detailThreadId;
    requestVersionRef.current += 1;
  }, [detailThreadId]);

  const appendMessage = (
    message: Message,
    threadId: string,
    requestVersion: number,
  ) => {
    options.setDetail((current) => {
      if (
        requestVersionRef.current !== requestVersion ||
        currentThreadIdRef.current !== threadId ||
        !current ||
        current.id !== threadId ||
        current.messages.some((item) => item.id === message.id)
      )
        return current;
      return { ...current, messages: [...current.messages, message] };
    });
  };

  const execute = async (value: TaskRequestFailure): Promise<boolean> => {
    // 同一输入框的每次提交都有独立 request_id；在上一次 SSE 尚未结束时拒绝
    // 本地的重复点击，避免把同一条指引并发发送两次。
    if (!options.detail || controllerRef.current) return false;
    const threadId = options.detail.id;
    const requestVersion = ++requestVersionRef.current;
    const isCurrentRequest = () =>
      requestVersionRef.current === requestVersion &&
      currentThreadIdRef.current === threadId;
    const controller = new AbortController();
    controllerRef.current = controller;
    setSubmitting(true);
    setFailure(null);
    options.setError("");
    try {
      await api.message(
        threadId,
        {
          request_id: value.requestId,
          content: value.content,
          artifact_ids: value.artifactIds,
          provider_config_id: options.providerConfigId,
          authorized_targets: value.authorizedTargets,
        },
        controller.signal,
        (event) => {
          if (!isCurrentRequest()) return;
          if (event.type === "execution_started") {
            appendMessage(event.data.user_message, threadId, requestVersion);
            options.onExecutionStarted(event.data.run);
          }
          if (event.type === "execution_stopped") {
            if (event.data.user_message)
              appendMessage(event.data.user_message, threadId, requestVersion);
            options.onExecutionStopped(event.data.run);
          }
          if (
            event.type === "guidance_queued" ||
            event.type === "input_received" ||
            event.type === "clarification_received"
          ) {
            if (event.data.user_message)
              appendMessage(event.data.user_message, threadId, requestVersion);
            options.onRunInteraction(event.data.run);
          }
        },
      );
      const latest = await api.detail(threadId);
      if (!isCurrentRequest()) return false;
      options.setDetail((current) =>
        current?.id === threadId ? latest : current,
      );
      await options.loadThreads();
      return true;
    } catch (cause) {
      if (!isCurrentRequest()) return false;
      const stopped = cause instanceof DOMException && cause.name === "AbortError";
      setFailure({
        ...value,
        threadId,
        message: stopped ? "任务请求已取消，可以重试。" : String(cause),
        retryable: true,
      });
      if (!stopped) options.setError(String(cause));
      return false;
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (isCurrentRequest()) setSubmitting(false);
    }
  };

  const send = (content: string, artifacts: Artifact[], authorizedTargets: string[]) =>
    execute(
      {
        requestId: crypto.randomUUID(),
        threadId: options.detail?.id ?? "",
        content,
        artifactIds: artifacts.map((item) => item.id),
        authorizedTargets,
        message: "",
        retryable: true,
      },
    );

  const retry = () =>
    failure && failure.threadId === currentThreadIdRef.current
      ? execute(failure)
      : Promise.resolve(false);
  const cancelResponse = () => controllerRef.current?.abort();
  const stopRun = () => {
    // 运行中的追加消息可能仍在读取 SSE；停止任务优先于该次展示请求。
    // 先使旧回调失效，再用独立 request_id 发出可持久化、可重放的停止命令。
    const activeController = controllerRef.current;
    if (activeController) {
      requestVersionRef.current += 1;
      controllerRef.current = null;
      activeController.abort();
    }
    return execute(
      {
        requestId: crypto.randomUUID(),
        threadId: options.detail?.id ?? "",
        // 停止按钮和用户直接输入“停止”共用唯一消息入口，后端负责按当前
        // Run 状态识别该短语并返回持久化后的终态。
        content: "停止任务",
        artifactIds: [],
        authorizedTargets: [],
        message: "",
        retryable: false,
      },
    );
  };
  const reset = () => {
    requestVersionRef.current += 1;
    cancelResponse();
    controllerRef.current = null;
    setSubmitting(false);
    setFailure(null);
  };

  return {
    submitting,
    failure,
    send,
    retry,
    cancelResponse,
    stopRun,
    reset,
  };
}
