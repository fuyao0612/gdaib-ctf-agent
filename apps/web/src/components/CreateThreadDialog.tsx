/** 创建对话弹窗；表单内容较高时自身滚动，按钮始终可访问。 */
import type { AgentProfileSummary, Mode } from "../types";

interface Props {
  title: string;
  mode: Mode;
  profileId: string;
  profiles: AgentProfileSummary[];
  busy: boolean;
  onTitleChange: (value: string) => void;
  onModeChange: (value: Mode) => void;
  onProfileChange: (id: string, mode?: Mode) => void;
  onCancel: () => void;
  onSubmit: () => void;
}

export default function CreateThreadDialog(props: Props) {
  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="创建 Agent 对话"
    >
      <form
        className="modal"
        onSubmit={(event) => {
          event.preventDefault();
          props.onSubmit();
        }}
      >
        <h2>创建 Agent 对话</h2>
        <label>
          任务名称
          <input
            aria-label="任务名称"
            value={props.title}
            onChange={(event) => props.onTitleChange(event.target.value)}
          />
        </label>
        <label>
          Agent 配置
          <select
            aria-label="Agent 配置"
            value={props.profileId}
            onChange={(event) => {
              const profile = props.profiles.find(
                (item) => item.profile_id === event.target.value,
              );
              props.onProfileChange(event.target.value, profile?.run_mode);
            }}
          >
            <option value="">请选择</option>
            {props.profiles.map((profile) => (
              <option key={profile.profile_id} value={profile.profile_id}>
                {profile.name} · v{profile.version} · {profile.completion_mode}
              </option>
            ))}
          </select>
        </label>
        <label>
          运行模式
          <select
            aria-label="运行模式"
            value={props.mode}
            onChange={(event) => props.onModeChange(event.target.value as Mode)}
          >
            <option value="normal">normal · 可继续交流</option>
            <option value="competition">competition · 运行中锁定输入</option>
          </select>
        </label>
        <p>Thread 将绑定当前 Agent 配置版本；后续编辑不会改变历史运行。</p>
        <div>
          <button type="button" onClick={props.onCancel}>
            取消
          </button>
          <button
            className="primary"
            type="submit"
            disabled={props.busy || !props.profileId}
          >
            创建
          </button>
        </div>
      </form>
    </div>
  );
}
