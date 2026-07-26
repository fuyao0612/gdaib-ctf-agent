/** 创建任务弹窗；表单内容较高时自身滚动，按钮始终可访问。 */
interface Props {
  title: string;
  busy: boolean;
  onTitleChange: (value: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}

export default function CreateThreadDialog(props: Props) {
  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="创建任务"
    >
      <form
        className="modal"
        onSubmit={(event) => {
          event.preventDefault();
          props.onSubmit();
        }}
      >
        <h2>创建任务</h2>
        <label>
          任务名称
          <input
            aria-label="任务名称"
            value={props.title}
            onChange={(event) => props.onTitleChange(event.target.value)}
          />
        </label>
        <p>直接发送消息即可；系统会在需要时自动执行受控任务。</p>
        <div>
          <button type="button" onClick={props.onCancel}>
            取消
          </button>
          <button
            className="primary"
            type="submit"
            disabled={props.busy || !props.title.trim()}
          >
            创建
          </button>
        </div>
      </form>
    </div>
  );
}
