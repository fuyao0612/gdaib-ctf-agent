/** 默认对话区的五阶段进度与统一结果卡；技术细节继续留在运行审计。 */
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../api";
import type { Event, Message, Report, Run, RunAudit } from "../types";
import {
  elapsedSeconds,
  presentPhases,
  publicProgressSummary,
  tokenUsageLabel,
} from "./run-presentation";

interface Props {
  run: Run;
  events: Event[];
  audit: RunAudit | null;
  report: Report | null;
  messages: Message[];
}

const STATUS_COPY = {
  completed: { title: "任务成功", next: "检查结果与证据；需要留档时下载完整报告。" },
  failed: { title: "任务失败", next: "根据失败原因调整配置或任务信息，然后点击重试。" },
  stopped: { title: "任务已停止", next: "确认任务范围后可安全重试，原审计记录会保留。" },
  waiting_input: { title: "等待用户补充", next: "在下方补充缺少的信息，Agent 会从检查点继续。" },
  waiting_clarification: { title: "等待任务澄清", next: "回答 Task Brief 中的澄清问题后继续。" },
  waiting_approval: { title: "等待计划确认", next: "检查计划范围、步骤和验证方式，再批准或提出修改。" },
  paused: { title: "任务已暂停", next: "检查已保存的计划、指引和预算，然后从安全检查点继续。" },
} as const;

function reportArray(report: Report | null, key: string): string[] {
  const value = report?.data[key];
  return Array.isArray(value) ? value.map(String) : [];
}

function finalAnswer(run: Run, report: Report | null, messages: Message[]): string {
  const explicit = report?.data.final_answer;
  if (typeof explicit === "string" && explicit.trim()) return explicit;
  const structured = report?.data.structured_output;
  if (structured && typeof structured === "object")
    return JSON.stringify(structured, null, 2);
  if (run.status !== "completed") return "未生成最终答案";
  return (
    [...messages]
      .reverse()
      .find((message) => ["assistant", "agent"].includes(message.role))?.content ??
    "未生成最终答案"
  );
}

function verifiedLabel(run: Run): string {
  if (run.validation_status === "validated") return "已通过配置的验证";
  if (run.validation_status === "unverified") return "模型生成，未经外部验证";
  if (run.validation_status === "failed") return "验证失败";
  return "尚未完成验证";
}

export function RunProgress({ run, events, audit }: Omit<Props, "report" | "messages">) {
  const [now, setNow] = useState(0);
  const active = [
    "queued",
    "running",
    "waiting_input",
    "waiting_clarification",
    "waiting_approval",
    "paused",
  ].includes(run.status);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const phases = useMemo(() => presentPhases(run, events, audit), [run, events, audit]);
  const current = phases.find((phase) =>
    ["active", "waiting", "interrupted"].includes(phase.state),
  );
  const model = audit?.model_calls?.at(-1)?.model ?? "等待首次模型调用";
  const latestKnownTime = Date.parse(
    events.at(-1)?.timestamp ?? run.started_at ?? run.created_at ?? "",
  );

  return (
    <section className="run-progress" data-testid="run-progress">
      <header>
        <div>
          <span className="pulse" />
          <strong>{current?.label ?? "五阶段已完成"}</strong>
        </div>
        <time>
          {elapsedSeconds(
            run,
            events,
            audit,
            now || (Number.isFinite(latestKnownTime) ? latestKnownTime : 0),
          )}{" "}
          秒
        </time>
      </header>
      <ol>
        {phases.map((phase, index) => (
          <li className={phase.state} key={phase.label}>
            <span>{phase.state === "completed" ? "✓" : index + 1}</span>
            {phase.label}
          </li>
        ))}
      </ol>
      <p>{publicProgressSummary(events)}</p>
      <dl className="run-resource-grid">
        <div><dt>Provider / 模型</dt><dd>{audit?.run.provider ?? run.provider} / {model}</dd></div>
        <div><dt>Agent 配置</dt><dd>{audit?.profile ? `${audit.profile.name} · v${audit.profile.version}` : `v${run.agent_profile_version ?? "?"}`}</dd></div>
        <div><dt>模型调用</dt><dd>{audit?.usage.model_calls ?? 0} / {audit?.limits.max_model_calls ?? "-"}</dd></div>
        <div><dt>工具调用</dt><dd>{audit?.usage.tool_calls ?? 0} / {audit?.limits.max_tool_calls ?? "-"}</dd></div>
        <div><dt>Token</dt><dd>{tokenUsageLabel(audit)}</dd></div>
        <div><dt>步骤预算</dt><dd>{audit?.usage.steps ?? 0} / {audit?.limits.max_steps ?? "-"}</dd></div>
        <div><dt>费用预算</dt><dd>{audit?.usage.model_cost ?? 0} / {audit?.limits.max_model_cost ?? "-"}</dd></div>
        <div><dt>时间预算</dt><dd>{elapsedSeconds(run, events, audit, now || (Number.isFinite(latestKnownTime) ? latestKnownTime : 0))} / {audit?.limits.max_duration_seconds ?? "-"} 秒</dd></div>
      </dl>
    </section>
  );
}

export function ResultCard({ run, events, audit, report, messages }: Props) {
  if (!(run.status in STATUS_COPY)) return null;
  const copy = STATUS_COPY[run.status as keyof typeof STATUS_COPY];
  const evidence = reportArray(report, "evidence");
  const auditEvidence = (audit?.evidence ?? []).map((item) => item.verification_summary);
  const evidenceSummary = [...evidence, ...auditEvidence].slice(0, 3);
  const reason =
    run.error ??
    [...events].reverse().find((event) => event.type === `run_${run.status}`)?.summary ??
    (run.status === "completed" ? "已完成全部阶段" : "等待继续运行");

  return (
    <section className={`result-card result-${run.status}`} data-testid={`result-${run.status}`}>
      <header>
        <div><span aria-hidden="true">{run.status === "completed" ? "✓" : run.status.startsWith("waiting_") || run.status === "paused" ? "…" : "!"}</span><h3>{copy.title}</h3></div>
        <small>{verifiedLabel(run)}</small>
      </header>
      <div className="result-answer">
        <strong>最终答案</strong>
        <pre>{finalAnswer(run, report, messages)}</pre>
      </div>
      <dl className="result-details">
        <div><dt>证据摘要</dt><dd>{evidenceSummary.length ? evidenceSummary.join("；") : "暂无可展示证据"}</dd></div>
        <div><dt>消耗</dt><dd>模型 {audit?.usage.model_calls ?? 0} 次 · 工具 {audit?.usage.tool_calls ?? 0} 次 · Token {tokenUsageLabel(audit)} · 费用 {audit?.usage.model_cost ?? 0} · {elapsedSeconds(run, events, audit)} 秒</dd></div>
        <div><dt>{run.status === "completed" ? "完成说明" : "原因"}</dt><dd>{reason}</dd></div>
        <div><dt>建议下一步</dt><dd>{copy.next}</dd></div>
      </dl>
      {report && (
        <details className="full-report">
          <summary>查看完整报告</summary>
          <div className="report-downloads">
            <a href={api.reportUrl(run.id, "md")}>下载 Markdown</a>
            <a href={api.reportUrl(run.id, "json")}>下载 JSON</a>
          </div>
          <ReactMarkdown>{report.markdown}</ReactMarkdown>
        </details>
      )}
    </section>
  );
}
