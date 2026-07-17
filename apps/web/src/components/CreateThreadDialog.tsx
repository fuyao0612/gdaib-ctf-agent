/** 创建对话弹窗；表单内容较高时自身滚动，按钮始终可访问。 */
import type {
  AgentProfileSummary,
  InteractionMode,
  Mode,
  PlanMode,
} from "../types";

interface Props {
  title: string;
  mode: Mode;
  profileId: string;
  planMode: PlanMode;
  interactionMode: InteractionMode;
  profiles: AgentProfileSummary[];
  busy: boolean;
  onTitleChange: (value: string) => void;
  onModeChange: (value: Mode) => void;
  onProfileChange: (id: string, mode?: Mode) => void;
  onPlanModeChange: (value: PlanMode) => void;
  onInteractionModeChange: (value: InteractionMode) => void;
  onCancel: () => void;
  onSubmit: () => void;
}

export default function CreateThreadDialog(props: Props) {
  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="创建对话"
    >
      <form
        className="modal"
        onSubmit={(event) => {
          event.preventDefault();
          props.onSubmit();
        }}
      >
        <h2>创建对话</h2>
        <label>
          对话名称
          <input
            aria-label="对话名称"
            value={props.title}
            onChange={(event) => props.onTitleChange(event.target.value)}
          />
        </label>
        <label>
          默认回复方式
          <select
            aria-label="默认回复方式"
            value={props.interactionMode}
            onChange={(event) =>
              props.onInteractionModeChange(event.target.value as InteractionMode)
            }
          >
            <option value="chat">对话 · 直接自然语言回复</option>
            <option value="agent">Agent 任务 · 计划、工具与验证</option>
          </select>
        </label>
        {props.interactionMode === "agent" && (
          <>
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
                    {profile.name} · v{profile.version}
                  </option>
                ))}
              </select>
            </label>
            <label>
              执行限制
              <select
                aria-label="执行限制"
                value={props.mode}
                onChange={(event) => props.onModeChange(event.target.value as Mode)}
              >
                <option value="normal">普通执行 · 可继续交流</option>
                <option value="competition">竞赛限制 · 运行中锁定输入</option>
              </select>
            </label>
            <label>
              计划控制
              <select
                aria-label="计划控制"
                value={props.planMode}
                onChange={(event) =>
                  props.onPlanModeChange(event.target.value as PlanMode)
                }
              >
                <option value="approval">计划确认 · 推荐新手</option>
                <option value="auto">自动执行</option>
              </select>
            </label>
            <p>Agent 任务会绑定当前配置版本，历史运行不受后续编辑影响。</p>
          </>
        )}
        <div>
          <button type="button" onClick={props.onCancel}>
            取消
          </button>
          <button
            className="primary"
            type="submit"
            disabled={
              props.busy ||
              !props.title.trim() ||
              (props.interactionMode === "agent" && !props.profileId)
            }
          >
            创建
          </button>
        </div>
      </form>
    </div>
  );
}
