/** 统一输入区：所有消息走同一入口，复杂执行由服务端自行判断。 */
import type { Artifact, Event, Run } from "../types";

interface Props {
  activeRun: Run | null;
  events: Event[];
  message: string;
  supplementalInput: string;
  pendingArtifacts: Artifact[];
  inputLocked: boolean;
  running: boolean;
  chatGenerating: boolean;
  chatCanRetry: boolean;
  busy: boolean;
  onMessageChange: (value: string) => void;
  onSupplementChange: (value: string) => void;
  onUpload: (file?: File) => void;
  onSend: () => void;
  onStop: () => void;
  onRetry: () => void;
  onChatRetry: () => void;
  onSubmitSupplement: () => void;
}

export default function MessageComposer(props: Props) {
  if (props.activeRun?.status === "waiting_input") {
    const request = props.events
      .filter((event) => event.type === "run_waiting_input")
      .at(-1);
    return (
      <footer className="composer">
        <section className="input-request" data-testid="waiting-input">
          <strong>正在等待补充信息</strong>
          <p>{request?.summary}</p>
          <textarea
            aria-label="补充信息"
            value={props.supplementalInput}
            onChange={(event) => props.onSupplementChange(event.target.value)}
            placeholder="补充必要事实；内容仍按不可信输入处理"
          />
          <button
            className="primary"
            disabled={!props.supplementalInput.trim()}
            onClick={props.onSubmitSupplement}
          >
            提交并继续
          </button>
        </section>
      </footer>
    );
  }

  const sendDisabled =
    props.busy ||
    props.inputLocked ||
    props.running ||
    props.chatGenerating ||
    !props.message.trim();

  return (
    <footer className="composer">
      <div className="attachments">
        {props.pendingArtifacts.map((file) => (
          <span key={file.id}>
            📎 {file.filename} · {file.size} B
          </span>
        ))}
      </div>
      {props.inputLocked && (
        <div className="lock-note">当前任务正在执行中，请先停止后再发送新消息。</div>
      )}
      <textarea
        aria-label="消息"
        value={props.message}
        onChange={(event) => props.onMessageChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            props.onSend();
          }
        }}
        disabled={props.inputLocked || props.busy || props.chatGenerating || props.running}
        placeholder="给御网智元发送消息…"
      />
      <div className="composer-actions">
        <label className="file-button">
          ＋ 附件
          <input
            aria-label="上传附件"
            type="file"
            accept=".txt,.json,.md,.log,.bin"
            onChange={(event) => props.onUpload(event.target.files?.[0])}
          />
        </label>
        <span className="authorization">Enter 发送 · Shift+Enter 换行</span>
        <div className="run-actions">
          {props.chatGenerating || props.running ? (
            <button className="danger" onClick={props.onStop}>
              {props.chatGenerating ? "停止生成" : "停止"}
            </button>
          ) : props.chatCanRetry ? (
            <button onClick={props.onChatRetry}>重试回复</button>
          ) : props.activeRun && ["failed", "stopped"].includes(props.activeRun.status) ? (
            <button onClick={props.onRetry}>重试</button>
          ) : null}
          <button className="primary" disabled={sendDisabled} onClick={props.onSend}>
            {props.busy || props.chatGenerating ? "正在发送…" : "发送"}
          </button>
        </div>
      </div>
    </footer>
  );
}
