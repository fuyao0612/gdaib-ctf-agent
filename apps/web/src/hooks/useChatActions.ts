/** 普通聊天的 fetch-SSE 生命周期；与 Agent Run/EventSource 完全独立。 */
import { useRef, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "../api";
import type { Artifact, Message, ThreadDetail } from "../types";

export interface ChatFailure {
  message: string;
  retryable: boolean;
  requestId: string;
  content: string;
  artifactIds: string[];
  providerId: string;
}

interface Options {
  detail: ThreadDetail | null;
  setDetail: Dispatch<SetStateAction<ThreadDetail | null>>;
  loadThreads: () => Promise<unknown>;
  setError: Dispatch<SetStateAction<string>>;
}

export function useChatActions(options: Options) {
  const [generating, setGenerating] = useState(false);
  const [draft, setDraft] = useState("");
  const [failure, setFailure] = useState<ChatFailure | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const appendMessage = (message: Message) => {
    options.setDetail((current) => {
      if (!current || current.messages.some((item) => item.id === message.id)) return current;
      return { ...current, messages: [...current.messages, message] };
    });
  };

  const execute = async (value: ChatFailure, retry: boolean): Promise<boolean> => {
    if (!options.detail) return false;
    const threadId = options.detail.id;
    const controller = new AbortController();
    controllerRef.current = controller;
    setGenerating(true);
    setDraft("");
    setFailure(null);
    options.setError("");
    try {
      await api.chat(
        threadId,
        {
          request_id: value.requestId,
          content: value.content,
          artifact_ids: value.artifactIds,
          provider_config_id: value.providerId || null,
          retry,
        },
        controller.signal,
        (event) => {
          if (event.type === "reply_start") appendMessage(event.data.user_message);
          if (event.type === "text_delta")
            setDraft((current) => current + event.data.text);
          if (event.type === "reply_complete") {
            appendMessage(event.data.message);
            setDraft("");
          }
          if (event.type === "reply_failed") {
            setDraft("");
            setFailure({ ...value, ...event.data });
          }
        },
      );
      options.setDetail(await api.detail(threadId));
      await options.loadThreads();
      return true;
    } catch (cause) {
      const stopped = cause instanceof DOMException && cause.name === "AbortError";
      setDraft("");
      setFailure({
        ...value,
        message: stopped ? "已停止生成，可以重试这条消息。" : String(cause),
        retryable: true,
      });
      if (!stopped) options.setError(String(cause));
      return false;
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      setGenerating(false);
    }
  };

  const send = (content: string, artifacts: Artifact[], providerId: string) =>
    execute(
      {
        requestId: crypto.randomUUID(),
        content,
        artifactIds: artifacts.map((item) => item.id),
        providerId,
        message: "",
        retryable: true,
      },
      false,
    );

  const retry = () => (failure ? execute(failure, true) : Promise.resolve(false));
  const stop = () => controllerRef.current?.abort();
  const reset = () => {
    controllerRef.current?.abort();
    setGenerating(false);
    setDraft("");
    setFailure(null);
  };

  return { generating, draft, failure, send, retry, stop, reset };
}
