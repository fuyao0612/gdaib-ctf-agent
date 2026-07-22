/** 统一输入区：文本和附件始终走同一消息入口，状态只改变服务端解释方式。 */
import type { Artifact, Run } from "../types";

interface Props {
  activeRun: Run | null;
  message: string;
  pendingArtifacts: Artifact[];
  uploading: boolean;
  chatGenerating: boolean;
  chatCanRetry: boolean;
  onMessageChange: (value: string) => void;
  onUpload: (file?: File) => void;
  onSend: () => void;
  onStop: () => void;
  onRetry: () => void;
  onChatRetry: () => void;
}

function inputCopy(run: Run | null) {
  switch (run?.status) {
    case "queued":
    case "running":
      return {
        note: "任务仍在运行：这条消息会作为追加指引按顺序应用。",
        placeholder: "补充约束、纠偏信息或当前对话的附件；不会扩大原授权范围",
        send: "追加指引",
      };
    case "waiting_input":
      return {
        note: "任务正在等待补充：发送后会从检查点继续。",
        placeholder: "补充必要事实或附件；内容仍按不可信输入处理",
        send: "补充并继续",
      };
    case "waiting_clarification":
      return {
        note: "任务说明正在等待澄清：发送后会更新说明并继续。",
        placeholder: "回答上方的澄清问题；原始要求和历史版本会保留",
        send: "提交澄清",
      };
    case "waiting_approval":
      return {
        note: "计划等待确认：这条消息会排队为反馈，批准或编辑计划仍在任务详情中完成。",
        placeholder: "补充计划反馈或附件；不会自动批准计划",
        send: "追加计划反馈",
      };
    case "paused":
      return {
        note: "任务已暂停：指引会保存，恢复后在安全检查点应用。",
        placeholder: "补充恢复后的约束、纠偏信息或附件",
        send: "保存指引",
      };
    default:
      return {
        note: "发送消息，系统会自动选择直接回复或受控执行。",
        placeholder: "给御网智元发送消息…",
        send: "发送",
      };
  }
}

export default function MessageComposer(props: Props) {
  const copy = inputCopy(props.activeRun);
  // 运行控制、计划编辑等操作不应禁用这里的主输入框。只有当前消息请求或附件
  // 尚未上传完成时才暂时不能提交，用户仍可继续编辑下一条内容。
  const sendDisabled =
    props.uploading || props.chatGenerating || !props.message.trim();
  const taskIsActive = Boolean(props.activeRun && [
    "queued",
    "running",
    "waiting_input",
    "waiting_clarification",
    "waiting_approval",
    "paused",
  ].includes(props.activeRun.status));
  const stopPending = Boolean(taskIsActive && props.activeRun?.stop_requested);
  const taskCanStop = taskIsActive && !stopPending;
  const canStop = props.chatGenerating || taskIsActive;

  return (
    <footer className="composer">
      <p className="composer-note" aria-live="polite">{copy.note}</p>
      {stopPending && (
        <p className="composer-note" role="status">
          停止请求处理中，仍在接收任务状态更新。
        </p>
      )}
      {props.uploading && (
        <p className="upload-note" role="status">
          附件正在上传，完成后会出现在下方列表并随下一条消息发送。
        </p>
      )}
      <div className="attachments">
        {props.pendingArtifacts.map((file) => (
          <span key={file.id}>
            📎 {file.filename} · {file.size} B
          </span>
        ))}
      </div>
      <textarea
        aria-label="消息"
        value={props.message}
        onChange={(event) => props.onMessageChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            // 键盘提交必须遵守和按钮完全相同的附件/请求保护，不能在上传中
            // 绕过禁用状态把一条尚未关联附件的消息提前发出。
            if (!sendDisabled) props.onSend();
          }
        }}
        disabled={props.chatGenerating}
        placeholder={copy.placeholder}
      />
      <div className="composer-actions">
        <label className="file-button">
          ＋ 附件
          <input
            aria-label="上传附件"
            type="file"
            accept=".txt,.json,.md,.log,.bin"
            disabled={props.uploading || props.chatGenerating}
            onChange={(event) => {
              props.onUpload(event.target.files?.[0]);
              // 允许用户在上传失败后重新选择同一个文件；待发送清单只在上传成功后更新。
              event.currentTarget.value = "";
            }}
          />
        </label>
        <span className="authorization">Enter 发送 · Shift+Enter 换行</span>
        <div className="run-actions">
          {props.chatCanRetry && (
            <button onClick={props.onChatRetry}>重试回复</button>
          )}
          {!props.chatCanRetry && props.activeRun && ["failed", "stopped"].includes(props.activeRun.status) && (
            <button onClick={props.onRetry}>重试</button>
          )}
          {canStop && (
            <button
              className="danger"
              disabled={stopPending}
              onClick={props.onStop}
            >
              {stopPending
                ? "停止请求处理中"
                : taskCanStop
                  ? "停止任务"
                  : "停止生成"}
            </button>
          )}
          <button className="primary" disabled={sendDisabled} onClick={props.onSend}>
            {props.uploading || props.chatGenerating ? "正在发送…" : copy.send}
          </button>
        </div>
      </div>
    </footer>
  );
}
