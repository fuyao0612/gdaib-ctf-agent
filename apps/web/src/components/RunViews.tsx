/** 运行状态、对话时间线与审计抽屉等只读视图。 */
import { api } from "../api";
import type {
  Event,
  MemoryRecord,
  Report,
  Run,
  RunAudit,
  RunControl,
  ThreadDetail,
} from "../types";
import { ResultCard, RunProgress } from "./RunSummary";
import TaskPlanControl from "./TaskPlanControl";

export function StatusBadge({ status }: { status: string }) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "运行中",
    waiting_input: "等待补充",
    waiting_clarification: "等待澄清",
    waiting_approval: "等待计划确认",
    paused: "已暂停",
    completed: "已完成",
    failed: "失败",
    stopped: "已停止",
  };
  return <span className={`badge badge-${status}`}>{labels[status] ?? status}</span>;
}

export function EventCard({ event }: { event: Event }) {
  const isToolEvent = event.type.startsWith("tool_");
  return (
    <article
      className={`event-card ${isToolEvent ? "tool-event" : ""}`}
      data-testid={`event-${event.type}`}
    >
      <div className="event-head">
        <span className="event-sequence">#{event.sequence}</span>
        <strong>{event.type.replaceAll("_", " ")}</strong>
        <time>{new Date(event.timestamp).toLocaleTimeString()}</time>
      </div>
      <p>{event.summary}</p>
      {isToolEvent && (
        <details>
          <summary>工具审计详情</summary>
          <pre>{JSON.stringify(event.payload, null, 2)}</pre>
        </details>
      )}
    </article>
  );
}

interface ConversationProps {
  detail: ThreadDetail;
  events: Event[];
  report: Report | null;
  run: Run | null;
  audit: RunAudit | null;
  control: RunControl | null;
  busy: boolean;
  onClarify: (content: string, briefVersion: number) => void;
  onEditPlan: (plan: import("../types").AgentPlan, version: number, reason: string) => void;
  onDecidePlan: (
    decision: "approve" | "reject",
    version: number,
    reason: string,
  ) => void;
}

export function ConversationView({
  detail,
  events,
  report,
  run,
  audit,
  control,
  busy,
  onClarify,
  onEditPlan,
  onDecidePlan,
}: ConversationProps) {
  return (
    <section className="conversation" aria-label="对话时间线">
      {detail.messages.map((message) => (
        <div key={message.id} className={`message ${message.role}`}>
          <span className="avatar">
            {message.role === "user" ? "你" : "智"}
          </span>
          <div>
            <div className="message-meta">
              {message.role === "user" ? "用户任务" : "Agent"} ·{" "}
              {new Date(message.created_at).toLocaleTimeString()}
            </div>
            <p>{message.content}</p>
          </div>
        </div>
      ))}
      {run && <RunProgress run={run} events={events} audit={audit} />}
      {run && control && (
        <TaskPlanControl
          run={run}
          control={control}
          busy={busy}
          onClarify={onClarify}
          onEdit={onEditPlan}
          onDecide={onDecidePlan}
        />
      )}
      {run && (
        <ResultCard
          run={run}
          events={events}
          audit={audit}
          report={report}
          messages={detail.messages}
        />
      )}
    </section>
  );
}

interface InspectorProps {
  open: boolean;
  metrics: { events: number; tools: number; replans: number };
  audit: RunAudit | null;
  events: Event[];
  detail: ThreadDetail | null;
  memories: MemoryRecord[];
  onClose: () => void;
  onToggleMemory: (enabled: boolean) => void;
  onDeleteMemory: (id: string) => void;
  onClearMemories: () => void;
}

export function InspectorPanel(props: InspectorProps) {
  const toolEvents = props.events.filter((event) =>
    event.type.startsWith("tool_"),
  );
  const progressEvents = props.events.filter(
    (event) => !event.type.startsWith("tool_"),
  );
  return (
    <aside
      id="run-inspector"
      className={`inspector ${props.open ? "open" : ""}`}
    >
      <div className="inspector-head">
        <span>运行审计</span>
        <div>
          <small>LIVE</small>
          <button
            className="inspector-close"
            aria-label="关闭运行审计"
            onClick={props.onClose}
          >
            关闭
          </button>
        </div>
      </div>
      <div className="metrics">
        <div>
          <strong>{props.metrics.events}</strong>
          <span>事件</span>
        </div>
        <div>
          <strong>{props.metrics.tools}</strong>
          <span>工具调用</span>
        </div>
        <div>
          <strong>{props.metrics.replans}</strong>
          <span>重规划</span>
        </div>
      </div>
      {props.audit && (
        <section className="budget-audit" data-testid="budget-audit">
          <div className="section-label">配置与剩余预算</div>
          <p>
            {props.audit.profile?.name} · v{props.audit.profile?.version} ·{" "}
            {props.audit.profile?.completion_mode}
          </p>
          <p>
            策略：{props.audit.profile?.planning_strategy} · 工作流：
            {props.audit.profile?.workflow_preset}
          </p>
          <p>Provider：{props.audit.run.provider}</p>
          <p>
            验证：{props.audit.run.validation_status} · 证据：
            {props.audit.run.evidence_level}
          </p>
          <dl>
            <dt>步骤</dt>
            <dd>
              {props.audit.usage.steps ?? 0} /{" "}
              {props.audit.limits.max_steps ?? "-"}
            </dd>
            <dt>模型调用</dt>
            <dd>
              {props.audit.usage.model_calls ?? 0} /{" "}
              {props.audit.limits.max_model_calls ?? "-"}
            </dd>
            <dt>Token</dt>
            <dd>
              {props.audit.usage.tokens ?? 0} /{" "}
              {props.audit.limits.max_tokens ?? "-"}
            </dd>
            <dt>上下文</dt>
            <dd>{props.audit.usage.context_tokens ?? 0} Token</dd>
            <dt>观察</dt>
            <dd>{props.audit.usage.observation_chars ?? 0} 字符</dd>
            <dt>裁剪</dt>
            <dd>{props.audit.usage.context_truncations ?? 0} 次</dd>
          </dl>
        </section>
      )}
      <div className="section-label">完整公开事件</div>
      <div className="tool-list">
        {progressEvents.map((event) => (
          <EventCard key={event.event_id} event={event} />
        ))}
        {progressEvents.length === 0 && (
          <p className="muted">运行后将在此展示计划、状态、验证和结果事件。</p>
        )}
      </div>
      <div className="section-label">工具与证据</div>
      <div className="tool-list">
        {toolEvents.map((event) => (
          <EventCard key={event.event_id} event={event} />
        ))}
        {toolEvents.length === 0 && (
          <p className="muted">运行后将在此展示折叠的工具审计卡片。</p>
        )}
      </div>
      <div className="section-label">附件</div>
      {props.detail?.artifacts.map((artifact) => (
        <a
          className="artifact"
          key={artifact.id}
          href={api.artifactUrl(artifact.id)}
        >
          <span>TXT</span>
          <div>
            <strong>{artifact.filename}</strong>
            <small>
              {artifact.sha256.slice(0, 12)}… · {artifact.size} B
            </small>
          </div>
        </a>
      ))}
      {(!props.detail || props.detail.artifacts.length === 0) && (
        <p className="muted">暂无附件。</p>
      )}
      {props.detail && (
        <section className="memory-panel">
          <div className="section-label">对话记忆</div>
          <div className="memory-controls">
            <button onClick={() => props.onToggleMemory(true)}>启用</button>
            <button onClick={() => props.onToggleMemory(false)}>停用</button>
            <button className="danger" onClick={props.onClearMemories}>
              全部清除
            </button>
          </div>
          <div className="memory-list">
            {props.memories.map((memory) => (
              <article
                key={memory.id}
                className={memory.enabled ? "" : "disabled"}
              >
                <strong>{memory.kind}</strong>
                <p>{memory.content}</p>
                <button
                  aria-label={`删除记忆 ${memory.content.slice(0, 20)}`}
                  onClick={() => props.onDeleteMemory(memory.id)}
                >
                  删除
                </button>
              </article>
            ))}
            {props.memories.length === 0 && (
              <p className="muted">暂无已保存记忆。</p>
            )}
          </div>
        </section>
      )}
    </aside>
  );
}
