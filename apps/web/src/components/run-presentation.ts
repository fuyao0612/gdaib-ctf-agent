/** 把持久化 Run/Event/Audit 归纳为用户视图；这里不创造新的运行状态。 */
import type { Event, Run, RunAudit } from "../types";

export const RUN_PHASES = [
  "理解任务",
  "制定计划",
  "执行动作",
  "验证结果",
  "生成汇报",
] as const;

export type PhaseState =
  | "pending"
  | "active"
  | "completed"
  | "waiting"
  | "interrupted";

export interface PresentedPhase {
  label: (typeof RUN_PHASES)[number];
  state: PhaseState;
}

const ACTION_NODES = new Set([
  "select_action",
  "policy_check",
  "execute_tool",
  "observe",
  "replan",
  "request_input",
]);

function reachedPhase(events: Event[], audit: RunAudit | null): number {
  const nodes = new Set((audit?.checkpoints ?? []).map((item) => item.node));
  const types = new Set(events.map((event) => event.type));
  if (nodes.has("generate_report") || types.has("run_completed")) return 4;
  if (nodes.has("verify") || nodes.has("complete")) return 3;
  if (
    [...nodes].some((node) => ACTION_NODES.has(node)) ||
    [...types].some((type) =>
      ["policy_checked", "tool_started", "tool_finished", "replanned"].includes(type),
    ) ||
    events.some((event) => typeof event.payload.action === "string")
  )
    return 2;
  if (nodes.has("plan") || types.has("plan_updated")) return 1;
  return 0;
}

export function presentPhases(
  run: Run,
  events: Event[],
  audit: RunAudit | null,
): PresentedPhase[] {
  const current = reachedPhase(events, audit);
  const completed = run.status === "completed";
  const interrupted = run.status === "failed" || run.status === "stopped";
  return RUN_PHASES.map((label, index) => {
    let state: PhaseState = "pending";
    if (completed || index < current) state = "completed";
    else if (
      index === current &&
      ["waiting_input", "waiting_clarification", "waiting_approval", "paused"].includes(
        run.status,
      )
    )
      state = "waiting";
    else if (index === current && interrupted) state = "interrupted";
    else if (index === current && ["queued", "running"].includes(run.status))
      state = "active";
    return { label, state };
  });
}

export function publicProgressSummary(events: Event[]): string {
  const latest = [...events]
    .reverse()
    .find((event) => !event.type.startsWith("run_") && event.summary.trim());
  return latest?.summary ?? "正在准备运行环境与任务快照。";
}

export function elapsedSeconds(
  run: Run,
  events: Event[],
  audit: RunAudit | null,
  now = Date.now(),
): number {
  const auditSeconds = audit?.usage.elapsed_seconds ?? 0;
  const started = run.started_at ?? events[0]?.timestamp ?? run.created_at;
  if (!started) return Math.round(auditSeconds);
  const startedMs = Date.parse(started);
  if (!Number.isFinite(startedMs)) return Math.round(auditSeconds);
  const terminalEvent = [...events]
    .reverse()
    .find((event) => ["run_completed", "run_failed", "run_stopped"].includes(event.type));
  const finishedMs = run.finished_at
    ? Date.parse(run.finished_at)
    : terminalEvent
      ? Date.parse(terminalEvent.timestamp)
      : now;
  const wallSeconds = Math.max(0, Math.round((finishedMs - startedMs) / 1000));
  return Math.max(Math.round(auditSeconds), wallSeconds);
}

export function tokenUsageLabel(audit: RunAudit | null): string {
  if (!audit) return "等待统计";
  const calls = audit.model_calls ?? [];
  const reported = calls.filter((call) => call.metadata.usage_reported === true).length;
  const tokens = audit.usage.tokens ?? 0;
  if (calls.length && reported === calls.length) return `${tokens}`;
  if (reported) return `${tokens}（部分调用为本地估算）`;
  return `厂商未提供（本地预算估算 ${tokens}）`;
}
