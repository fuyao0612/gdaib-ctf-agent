import { useEffect, useState } from "react";
import type { Event, Run, RunControl } from "../types";

interface Props {
  run: Run;
  control: RunControl;
  events: Event[];
  busy: boolean;
  onPause: () => Promise<boolean>;
  onResume: () => Promise<boolean>;
  onGuidance: (content: string) => Promise<boolean>;
}

/** 暂停、继续和追加指引的公开控制面板；停止仍由主输入区单独表达。 */
export default function RunControlPanel(props: Props) {
  const [guidance, setGuidance] = useState("");
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
          <small>暂停会等待安全检查点；停止会终止本次运行。</small>
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

      {controllable && (
        <div className="guidance-form">
          <textarea
            aria-label="追加指引"
            value={guidance}
            onChange={(event) => setGuidance(event.target.value)}
            placeholder="追加约束或纠偏信息；不会扩大原授权范围"
          />
          <button
            disabled={props.busy || !guidance.trim()}
            onClick={async () => {
              if (await props.onGuidance(guidance.trim())) setGuidance("");
            }}
          >
            排队追加指引
          </button>
        </div>
      )}

      {guidanceItems.length > 0 && (
        <ol className="guidance-list" aria-label="追加指引记录">
          {guidanceItems.map((item) => (
            <li key={item.id}>
              <span>#{item.sequence}</span>
              <p>{item.content}</p>
              <time dateTime={item.created_at}>
                {new Date(item.created_at).toLocaleString()}
              </time>
              <strong>{item.consumed_at ? "已应用并重规划" : "已排队"}</strong>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
