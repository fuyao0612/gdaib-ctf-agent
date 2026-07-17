/** 对话输入区：附件、验证规则、模型选择以及停止/重试按钮。 */
import type {
  Artifact,
  Event,
  InteractionMode,
  ProviderConfig,
  Run,
} from "../types";

interface Props {
  activeRun: Run | null;
  interactionMode: InteractionMode;
  events: Event[];
  message: string;
  supplementalInput: string;
  pendingArtifacts: Artifact[];
  providers: ProviderConfig[];
  selectedProviderId: string;
  successPattern: string;
  needsEvidencePattern: boolean;
  advisoryMode: boolean;
  inputLocked: boolean;
  running: boolean;
  chatGenerating: boolean;
  chatCanRetry: boolean;
  busy: boolean;
  onMessageChange: (value: string) => void;
  onInteractionModeChange: (value: InteractionMode) => void;
  onSupplementChange: (value: string) => void;
  onProviderChange: (value: string) => void;
  onPatternChange: (value: string) => void;
  onUpload: (file?: File) => void;
  onSend: () => void;
  onStop: () => void;
  onRetry: () => void;
  onChatRetry: () => void;
  onSubmitSupplement: () => void;
}

export default function MessageComposer(props: Props) {
  if (
    props.interactionMode === "agent" &&
    props.activeRun?.status === "waiting_input"
  ) {
    const request = props.events
      .filter((event) => event.type === "run_waiting_input")
      .at(-1);
    return (
      <footer className="composer">
        <section className="input-request" data-testid="waiting-input">
          <strong>Agent 正在等待补充信息</strong>
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
    !props.message.trim() ||
    !props.selectedProviderId ||
    (props.interactionMode === "agent" &&
      props.needsEvidencePattern &&
      !props.successPattern.trim());

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
        <div className="lock-note">
          竞赛模式运行中：已锁定补充提示，仅可观察或停止。
        </div>
      )}
      <div className="interaction-switch" role="group" aria-label="回复模式">
        <button
          type="button"
          className={props.interactionMode === "chat" ? "active" : ""}
          aria-pressed={props.interactionMode === "chat"}
          disabled={props.running || props.chatGenerating}
          onClick={() => props.onInteractionModeChange("chat")}
        >
          对话
        </button>
        <button
          type="button"
          className={props.interactionMode === "agent" ? "active" : ""}
          aria-pressed={props.interactionMode === "agent"}
          disabled={props.running || props.chatGenerating}
          onClick={() => props.onInteractionModeChange("agent")}
        >
          Agent 任务
        </button>
        <span>
          {props.interactionMode === "chat"
            ? "直接自然语言回复"
            : "使用计划、工具、验证与审计"}
        </span>
      </div>
      {props.providers.length === 0 && (
        <div className="model-required">
          需要配置模型：请先进入设置中心添加、测试并启用 Provider。
        </div>
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
        disabled={props.inputLocked || props.busy || props.chatGenerating}
        placeholder={
          props.interactionMode === "chat"
            ? "给御网智元发送消息…"
            : "描述任务、授权范围、期望输出与必要上下文…"
        }
      />
      {props.interactionMode === "agent" && (
        <details className="agent-options">
          <summary>任务设置</summary>
          {props.needsEvidencePattern ? (
            <div className="verification-row">
              <label>
                成功答案正则
                <input
                  aria-label="成功答案正则"
                  value={props.successPattern}
                  onChange={(event) => props.onPatternChange(event.target.value)}
                  placeholder="例如 FLAG\{[A-Za-z0-9_-]+\}"
                  disabled={props.running}
                />
              </label>
              <small>候选必须绑定外部证据并通过此规则。</small>
            </div>
          ) : (
            <div className="trust-level">
              {props.advisoryMode
                ? "建议回答：模型生成，未经外部验证"
                : "结构化输出：按 Agent 配置验证"}
            </div>
          )}
        </details>
      )}
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
        <label className="provider-select">
          模型
          <select
            aria-label="运行模型"
            value={props.selectedProviderId}
            onChange={(event) => props.onProviderChange(event.target.value)}
            disabled={props.running || props.providers.length === 0}
          >
            <option value="">未配置</option>
            {props.providers.map((provider) => (
              <option key={provider.id} value={provider.id}>
                {provider.name} · {provider.model}
              </option>
            ))}
          </select>
        </label>
        <span className="authorization">Enter 发送 · Shift+Enter 换行</span>
        <div className="run-actions">
          {props.chatGenerating ? (
            <button className="danger" onClick={props.onStop}>
              停止生成
            </button>
          ) : props.running ? (
            <button className="danger" onClick={props.onStop}>
              停止
            </button>
          ) : props.chatCanRetry ? (
            <button onClick={props.onChatRetry}>重试回复</button>
          ) : props.activeRun &&
            ["failed", "stopped"].includes(props.activeRun.status) ? (
            <button onClick={props.onRetry}>重试</button>
          ) : null}
          <button
            className="primary"
            disabled={sendDisabled}
            onClick={props.onSend}
          >
            {props.busy || props.chatGenerating ? "正在发送…" : "发送"}
          </button>
        </div>
      </div>
    </footer>
  );
}
