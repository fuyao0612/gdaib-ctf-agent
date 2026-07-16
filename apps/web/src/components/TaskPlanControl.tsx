/** 公开 Task Brief、计划版本与人工确认；不展示模型隐藏思维链。 */
import { useEffect, useState } from "react";
import type { AgentPlan, Run, RunControl } from "../types";

interface Props {
  run: Run;
  control: RunControl;
  busy: boolean;
  onClarify: (content: string, briefVersion: number) => void;
  onEdit: (plan: AgentPlan, version: number, reason: string) => void;
  onDecide: (decision: "approve" | "reject", version: number, reason: string) => void;
}

const joinLines = (values: string[]) => values.join("\n");
const splitLines = (value: string) =>
  value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);

function BriefList({ label, values }: { label: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <div>
      <dt>{label}</dt>
      <dd>{values.join("；")}</dd>
    </div>
  );
}

export default function TaskPlanControl(props: Props) {
  const brief = props.control.task_briefs?.at(-1);
  const revision = props.control.plans?.at(-1);
  const [clarification, setClarification] = useState("");
  const [reason, setReason] = useState("");
  const [summary, setSummary] = useState("");
  const [steps, setSteps] = useState("");
  const [successApproach, setSuccessApproach] = useState("");
  const [expectedResults, setExpectedResults] = useState("");
  const [verificationMethods, setVerificationMethods] = useState("");
  const [risks, setRisks] = useState("");
  const [dependencies, setDependencies] = useState("");

  useEffect(() => {
    if (!revision) return;
    setSummary(revision.plan.summary);
    setSteps(joinLines(revision.plan.steps));
    setSuccessApproach(revision.plan.success_approach);
    setExpectedResults(joinLines(revision.plan.expected_results));
    setVerificationMethods(joinLines(revision.plan.verification_methods));
    setRisks(joinLines(revision.plan.risks));
    setDependencies(joinLines(revision.plan.dependencies));
  }, [revision]);

  const editedPlan = (): AgentPlan => ({
    summary,
    steps: splitLines(steps),
    success_approach: successApproach,
    expected_results: splitLines(expectedResults),
    verification_methods: splitLines(verificationMethods),
    risks: splitLines(risks),
    dependencies: splitLines(dependencies),
  });

  return (
    <section className="task-plan-control" data-testid="task-plan-control">
      {brief && (
        <details open={props.run.status === "waiting_clarification"}>
          <summary>Task Brief · v{brief.version}</summary>
          <p className="task-goal">{brief.goal}</p>
          <dl className="task-brief-grid">
            <BriefList label="授权范围" values={brief.authorized_scope} />
            <BriefList label="限制条件" values={brief.constraints} />
            <BriefList label="成功标准" values={brief.success_criteria} />
            <BriefList label="已知信息" values={brief.known_information} />
            <BriefList label="假设" values={brief.assumptions} />
            <BriefList label="风险" values={brief.risks} />
            <div><dt>预期输出</dt><dd>{brief.expected_output || "未单独指定"}</dd></div>
          </dl>
          {props.run.status === "waiting_clarification" && (
            <div className="control-action" data-testid="clarification-control">
              <strong>需要补充</strong>
              <ul>{brief.clarification_questions.map((item) => <li key={item}>{item}</li>)}</ul>
              <textarea
                aria-label="任务澄清"
                value={clarification}
                onChange={(event) => setClarification(event.target.value)}
                placeholder="回答上面的问题；原始要求和历史版本会保留"
              />
              <button
                className="primary"
                disabled={props.busy || !clarification.trim()}
                onClick={() => props.onClarify(clarification, brief.version)}
              >
                提交澄清并继续
              </button>
            </div>
          )}
        </details>
      )}

      {revision && (
        <details open={props.run.status === "waiting_approval"}>
          <summary>执行计划 · v{revision.version} · {revision.source}</summary>
          {props.run.status === "waiting_approval" ? (
            <div className="plan-editor" data-testid="plan-approval-control">
              <label>计划摘要<input aria-label="计划摘要" value={summary} onChange={(event) => setSummary(event.target.value)} /></label>
              <label>步骤（每行一项）<textarea aria-label="计划步骤" value={steps} onChange={(event) => setSteps(event.target.value)} /></label>
              <label>预期结果（每行一项）<textarea aria-label="计划预期结果" value={expectedResults} onChange={(event) => setExpectedResults(event.target.value)} /></label>
              <label>验证方式（每行一项）<textarea aria-label="计划验证方式" value={verificationMethods} onChange={(event) => setVerificationMethods(event.target.value)} /></label>
              <label>风险与依赖<textarea aria-label="计划风险" value={risks} onChange={(event) => setRisks(event.target.value)} /></label>
              <label>依赖（每行一项）<textarea aria-label="计划依赖" value={dependencies} onChange={(event) => setDependencies(event.target.value)} /></label>
              <label>成功路径<input aria-label="计划成功路径" value={successApproach} onChange={(event) => setSuccessApproach(event.target.value)} /></label>
              <label>修改或拒绝原因<textarea aria-label="计划意见" value={reason} onChange={(event) => setReason(event.target.value)} /></label>
              <div className="plan-actions">
                <button disabled={props.busy || !summary.trim() || !splitLines(steps).length} onClick={() => props.onEdit(editedPlan(), revision.version, reason)}>保存新版本</button>
                <button className="danger" disabled={props.busy || !reason.trim()} onClick={() => props.onDecide("reject", revision.version, reason)}>拒绝并重新规划</button>
                <button className="primary" disabled={props.busy} onClick={() => props.onDecide("approve", revision.version, reason)}>批准并执行</button>
              </div>
            </div>
          ) : (
            <ol>{revision.plan.steps.map((step) => <li key={step}>{step}</li>)}</ol>
          )}
        </details>
      )}
    </section>
  );
}
