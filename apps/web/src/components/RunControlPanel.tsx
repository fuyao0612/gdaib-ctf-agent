import { useEffect, useState } from "react";
import type { Event, Run, RunControl, RunGuidance } from "../types";

interface Props {
  run: Run;
  control: RunControl;
  events: Event[];
  busy: boolean;
  onPause: () => Promise<boolean>;
  onResume: () => Promise<boolean>;
}

function guidanceState(item: RunGuidance, events: Event[]): string {
  if (item.discarded_at) return "任务已结束，未应用";
  if (!item.consumed_at) return "已排队";
  // 后端在 `replanned` 事件中写入真正触发该次重规划的 guidance_sequences。
  // 只接受这个显式关联，不能从“已消费”或时间先后推断因果。
  const replannedForGuidance = events.some((event) => {
    if (event.type !== "replanned") return false;
    const sequences = event.payload.guidance_sequences;
    return Array.isArray(sequences) && sequences.includes(item.sequence);
  });
  return replannedForGuidance
    ? "已在检查点应用，因本指引重规划"
    : "已在检查点应用";
}

/** 暂停、继续和已保存指引的高级查看入口；停止由主输入区统一处理。 */
export default function RunControlPanel(props: Props) {
  const [pauseSubmitted, setPauseSubmitted] = useState(false);
  const guidanceItems = props.control.guidance ?? [];
  const lastPause = [...props.events].reverse().find((event) =>
    ["pause_requested", "run_paused", "run_resumed"].includes(event.type),
  );
  const pauseQueued =
    props.run.status === "running" &&
    (pauseSubmitted || lastPause?.type === "pause_requested");
  const controllable = ["running", "paused"].includes(props.run.status);

  useEffect(() => {
    if (props.run.status !== "running" || lastPause?.type === "run_resumed")
      setPauseSubmitted(false);
  }, [lastPause?.type, props.run.status]);

  if (!controllable && guidanceItems.length === 0) return null;

  return (
    <section className="run-control-panel" data-testid="run-control-panel">
      <div className="run-control-heading">
        <div>
          <strong>运行控制</strong>
          <small>暂停会等待安全检查点；停止请直接在下方输入框发送。</small>
        </div>
        {props.run.status === "running" ? (
          <button
            disabled={props.busy || pauseQueued}
            onClick={async () => {
              setPauseSubmitted(true);
              if (!(await props.onPause())) setPauseSubmitted(false);
            }}
          >
            {pauseQueued ? "暂停已排队" : "安全暂停"}
          </button>
        ) : props.run.status === "paused" ? (
          <button className="primary" disabled={props.busy} onClick={props.onResume}>
            从检查点继续
          </button>
        ) : null}
      </div>

      {guidanceItems.length > 0 && (
        <ol className="guidance-list" aria-label="追加指引记录">
          {guidanceItems.map((item) => (
            <li key={item.id}>
              <span>#{item.sequence}</span>
              <p>{item.content}</p>
              <time dateTime={item.created_at}>
                {new Date(item.created_at).toLocaleString()}
              </time>
              <strong>{guidanceState(item, props.events)}</strong>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
