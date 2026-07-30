/** 默认对话区的五阶段进度与统一结果卡；技术细节继续留在运行审计。 */
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../api";
import type { Event, ExecutionStep, FlagCandidate, Message, Report, Run, RunAudit } from "../types";
import {
  elapsedSeconds,
  presentPhases,
  publicProgressSummary,
  tokenUsageLabel,
  candidateSourceLabel,
  flagFormatStatusLabel,
} from "./run-presentation";

interface Props {
  run: Run;
  events: Event[];
  audit: RunAudit | null;
  report: Report | null;
  messages: Message[];
}

const STATUS_COPY = {
  completed: { title: "任务已完成", next: "检查结论与验证状态；需要留档时下载完整报告。" },
  failed: { title: "任务失败", next: "根据失败原因调整配置或任务信息，然后点击重试。" },
  stopped: { title: "任务已停止", next: "确认任务范围后可安全重试，原审计记录会保留。" },
  waiting_input: { title: "等待用户补充", next: "在下方统一输入框补充缺少的信息，Agent 会从检查点继续。" },
  waiting_clarification: { title: "等待任务澄清", next: "在下方统一输入框回答任务说明中的澄清问题后继续。" },
  waiting_approval: { title: "等待计划确认", next: "检查计划范围、步骤和验证方式，再批准或提出修改。" },
  paused: { title: "任务已暂停", next: "检查已保存的计划、指引和预算，然后从安全检查点继续。" },
} as const;

function reportArray(report: Report | null, key: string): string[] {
  const value = report?.data[key];
  return Array.isArray(value) ? value.map(String) : [];
}

function nonEmpty(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function failureAnalysisSummary(report: Report | null, events: Event[]): string | null {
  const reportAnalysis = report?.data.failure_analysis;
  if (reportAnalysis && typeof reportAnalysis === "object") {
    const summary = nonEmpty((reportAnalysis as Record<string, unknown>).summary);
    if (summary) return summary;
  }
  const failedEvent = [...events].reverse().find((event) => event.type === "run_failed");
  const eventAnalysis = failedEvent?.payload.failure_analysis;
  if (eventAnalysis && typeof eventAnalysis === "object")
    return nonEmpty((eventAnalysis as Record<string, unknown>).summary);
  return null;
}

function retrospective(report: Report | null): NonNullable<Report["data"]["retrospective"]> | null {
  const value = report?.data.retrospective;
  return value && typeof value === "object" ? value : null;
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

function verifiedLabel(run: Run, candidates: FlagCandidate[]): string {
  if (run.validation_status === "validated") return "已通过配置的验证";
  if (run.validation_status === "partial") return "已完成结构化校验，未完成外部验证";
  if (run.validation_status === "unverified")
    return candidates.length ? "已发现候选，尚未完成外部验证" : "结果未经外部验证";
  if (run.validation_status === "failed") return "验证失败";
  return "尚未完成验证";
}

function completedTitle(run: Run): string {
  if (run.validation_status === "validated") return "任务已验证成功";
  if (run.validation_status === "partial") return "回答已完成（部分验证）";
  if (run.validation_status === "unverified") return "回答已完成（未外部验证）";
  if (run.validation_status === "failed") return "回答完成，但验证失败";
  return "任务已完成，验证状态待确认";
}

function conciseAnswer(value: string): string {
  const limit = 420;
  if (value.length <= limit) return value;
  return `${value.slice(0, limit).trimEnd()}…`;
}

function flagCandidates(report: Report | null): FlagCandidate[] {
  const values = report?.data.flag_candidates;
  return Array.isArray(values) ? values.filter((value): value is FlagCandidate =>
    Boolean(
      value &&
      typeof value.candidate === "string" &&
      (typeof value.format_status === "string" || typeof value.validation_status === "string"),
    ),
  ) : [];
}

const STEP_STATUS_LABEL: Record<ExecutionStep["observation_status"], string> = {
  running: "执行中", success: "成功", error: "失败", timeout: "超时", blocked: "已阻止", stopped: "已停止",
};

export function ExecutionTimeline({ steps }: { steps: ExecutionStep[] }) {
  if (!steps.length) return <p className="muted">暂无已持久化的工具执行步骤。</p>;
  return (
    <ol className="execution-timeline" aria-label="行动、观察与决策时间线">
      {steps.map((step) => (
        <li className={`execution-step ${step.observation_status}`} key={`${step.sequence}-${step.call_id ?? "manual"}`}>
          <header><strong>步骤 {step.sequence}</strong><span>{STEP_STATUS_LABEL[step.observation_status]}</span><time>{step.duration_ms == null ? "进行中" : `${step.duration_ms} ms`}</time></header>
          <p><b>目标：</b>{step.goal}</p>
          <p><b>理由：</b>{step.action_reason ?? "历史步骤未记录公开理由"}</p>
          <p><b>行动：</b>{step.tool_name ?? step.action_kind}，{step.action_summary}</p>
          <p><b>关键观察：</b>{step.observation_summary ?? "正在等待工具返回"}</p>
          <p><b>下一步：</b>{(step.decision ?? "等待下一次公开决策").replace(/^(下一步：\s*)+/, "").replace(/^(结束：\s*)+/, "")}</p>
          <div className="step-links">证据 {step.evidence_ids.length} · Artifact {step.artifact_ids.length}</div>
          <details>
            <summary>查看参数与结果预览</summary>
            <pre>{JSON.stringify(step.arguments, null, 2)}</pre>
            {step.preview && <pre>{step.preview}</pre>}
            {step.error && <p className="step-error">{step.error}</p>}
          </details>
        </li>
      ))}
    </ol>
  );
}

export function RunProgress({ run, events, audit, report = null }: Omit<Props, "messages" | "report"> & { report?: Report | null }) {
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
  const model = audit?.model_calls?.at(-1)?.model ?? run.model ?? "等待首次模型调用";
  const reportSteps = report?.data.timeline;
  const executionSteps = ["completed", "failed", "stopped"].includes(run.status) && Array.isArray(reportSteps)
    ? reportSteps as ExecutionStep[]
    : audit?.steps ?? [];
  const latestKnownTime = Date.parse(
    events.at(-1)?.timestamp ?? run.started_at ?? run.created_at ?? "",
  );

  return (
    <section className="run-progress" data-testid="run-progress">
      <header>
        <div>
          <span className="pulse" />
          <strong>{current?.label ?? "五阶段已完成"}</strong>
          <small data-testid="run-model">{model}</small>
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
      <p>{publicProgressSummary(events)}</p>
      <details className="run-details">
        <summary>查看执行阶段与资源</summary>
        <ol>
          {phases.map((phase, index) => (
            <li className={phase.state} key={phase.label}>
              <span>{phase.state === "completed" ? "✓" : index + 1}</span>
              {phase.label}
            </li>
          ))}
        </ol>
        <dl className="run-resource-grid">
          <div><dt>Provider / 模型</dt><dd>{audit?.run.provider ?? run.provider} / {model}</dd></div>
          <div><dt>Agent 配置</dt><dd>{audit?.profile ? `${audit.profile.name} · v${audit.profile.version}` : `v${run.agent_profile_version ?? "?"}`}</dd></div>
          <div><dt>模型调用</dt><dd>{audit?.usage.model_calls ?? 0} / {audit?.limits.max_model_calls ?? "-"}</dd></div>
          <div><dt>工具调用</dt><dd>{audit?.usage.tool_calls ?? 0} / {audit?.limits.max_tool_calls ?? "-"}</dd></div>
          <div><dt>Token</dt><dd>{tokenUsageLabel(audit)}</dd></div>
          <div><dt>步骤预算</dt><dd>{audit?.usage.steps ?? 0} / {audit?.limits.max_steps ?? "-"}</dd></div>
        </dl>
      </details>
      {executionSteps.length > 0 && <section className="execution-trace" data-testid="execution-timeline">
        <h3>执行时间线</h3>
        <ExecutionTimeline steps={executionSteps} />
      </section>}
    </section>
  );
}

export function ResultCard({ run, events, audit, report, messages }: Props) {
  if (!(run.status in STATUS_COPY)) return null;
  const baseCopy = STATUS_COPY[run.status as keyof typeof STATUS_COPY];
  const copy = run.status === "completed"
    ? { ...baseCopy, title: completedTitle(run) }
    : baseCopy;
  const evidence = reportArray(report, "evidence");
  const auditEvidence = (audit?.evidence ?? []).map((item) => item.verification_summary);
  const evidenceSummary = [...evidence, ...auditEvidence].slice(0, 3);
  const answer = finalAnswer(run, report, messages);
  const failureSummary = failureAnalysisSummary(report, events);
  const candidates = flagCandidates(report);
  const review = retrospective(report);
  const reason =
    (run.status === "failed" ? failureSummary : null) ??
    nonEmpty(run.error) ??
    [...events].reverse().find((event) => event.type === `run_${run.status}`)?.summary ??
    (run.status === "completed" ? "已完成全部阶段" : "等待继续运行");

  return (
    <section className={`result-card result-${run.status}`} data-testid={`result-${run.status}`}>
      <header>
        <div><span aria-hidden="true">{run.status === "completed" ? "✓" : run.status.startsWith("waiting_") || run.status === "paused" ? "…" : "!"}</span><h3>{copy.title}</h3></div>
        <small>{verifiedLabel(run, candidates)}</small>
      </header>
      <p className="result-next">{copy.next}</p>
      {run.status === "completed" && (
        <section className="result-conclusion" data-testid="result-conclusion">
          <strong>结论</strong>
          <p>{conciseAnswer(answer)}</p>
        </section>
      )}
      {candidates.length > 0 && (
        <section className="result-conclusion" data-testid="flag-candidates">
          <strong>Flag 候选与验证状态</strong>
          {candidates.map((candidate) => (
            <p key={`${candidate.candidate}-${candidate.source_call_id ?? ""}`}>
              {candidate.candidate}：格式校验 {flagFormatStatusLabel(candidate.format_status ?? candidate.validation_status)}；
              来源 {candidateSourceLabel(candidate.discovery_source ?? candidate.source_kind)}；
              {candidate.platform_verified ? "赛题平台验证通过" : "尚未经过赛题平台验证（未执行）"}
            </p>
          ))}
        </section>
      )}
      {run.status === "completed" && review?.summary && (
        <section className="result-conclusion" data-testid="retrospective-summary">
          <strong>{review.source === "model" ? "模型复盘" : "确定性摘要"}</strong>
          <p>{conciseAnswer(review.summary)}</p>
        </section>
      )}
      {run.status === "failed" && (
        <section className="result-conclusion" data-testid="failure-analysis">
          <strong>失败复盘</strong>
          <p>{conciseAnswer(reason)}</p>
        </section>
      )}
      <details className="full-report">
        <summary>查看完整报告、证据与复现步骤</summary>
        <div className="result-answer">
          <strong>{run.status === "failed" ? "失败复盘" : "结论"}</strong>
          <pre>{run.status === "failed" ? reason : answer}</pre>
        </div>
        <dl className="result-details">
          <div><dt>证据摘要</dt><dd>{evidenceSummary.length ? evidenceSummary.join("；") : candidates.length ? "已发现 Flag 候选及其工具来源，但尚无确定性验证记录" : "暂无已持久化证据"}</dd></div>
          <div><dt>消耗</dt><dd>模型 {audit?.usage.model_calls ?? 0} 次 · 工具 {audit?.usage.tool_calls ?? 0} 次 · Token {tokenUsageLabel(audit)} · {elapsedSeconds(run, events, audit)} 秒</dd></div>
          <div><dt>{run.status === "completed" ? "完成说明" : "原因"}</dt><dd>{reason}</dd></div>
        </dl>
        {report && (
          <>
          <div className="report-downloads">
            <a href={api.reportUrl(run.id, "md")}>下载 Markdown</a>
            <a href={api.reportUrl(run.id, "json")}>下载 JSON</a>
            <a href={api.trajectoryUrl(run.id)}>下载轨迹</a>
          </div>
          <ReactMarkdown>{report.markdown}</ReactMarkdown>
          </>
        )}
      </details>
    </section>
  );
}
