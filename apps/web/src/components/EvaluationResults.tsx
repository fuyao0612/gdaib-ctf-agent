/** 已持久化评测的只读查询面；浏览器不触发模型调用。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { EvaluationRecord, EvaluationStatistics, EvaluationStatus } from "../types";

const labels: Record<EvaluationStatus, string> = {
  passed: "通过",
  failed: "失败",
  skipped: "跳过",
};

function milliseconds(value: number) {
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} 秒`;
}

interface Props {
  onError: (message: string) => void;
}

export default function EvaluationResults({ onError }: Props) {
  const [records, setRecords] = useState<EvaluationRecord[]>([]);
  const [statistics, setStatistics] = useState<EvaluationStatistics | null>(null);
  const [status, setStatus] = useState<"" | EvaluationStatus>("");
  const [category, setCategory] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [loading, setLoading] = useState(false);
  const [comparisonCase, setComparisonCase] = useState("");

  const categories = useMemo(
    () => [...new Set(records.map((item) => item.category))].sort(),
    [records],
  );
  const difficulties = useMemo(
    () => [...new Set(records.map((item) => item.difficulty))].sort(),
    [records],
  );
  const query = useMemo(
    () => ({ status, category, difficulty }),
    [category, difficulty, status],
  );
  const comparableCases = useMemo(
    () => [...new Set(records.map((item) => item.case_id))]
      .filter((caseId) => records.filter((item) => item.case_id === caseId).length > 1)
      .sort(),
    [records],
  );
  const comparison = useMemo(
    () => records.filter((item) => item.case_id === comparisonCase)
      .sort((left, right) => left.attempt - right.attempt || left.started_at.localeCompare(right.started_at)),
    [comparisonCase, records],
  );
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [items, summary] = await Promise.all([
        api.evaluations(query),
        api.evaluationStatistics(query),
      ]);
      setRecords(items);
      setStatistics(summary);
    } catch (cause) {
      onError(String(cause));
    } finally {
      setLoading(false);
    }
  }, [onError, query]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="evaluation-results" aria-label="评测结果">
      <div className="settings-title">
        <div>
          <h3>评测结果</h3>
          <small>只显示已持久化的真实运行；执行请使用本地评测 CLI。</small>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading}>
          刷新
        </button>
      </div>
      <div className="evaluation-filters">
        <label>
          状态
          <select value={status} onChange={(event) => setStatus(event.target.value as "" | EvaluationStatus)}>
            <option value="">全部</option>
            {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label>
          分类
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">全部</option>
            {categories.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>
          难度
          <select value={difficulty} onChange={(event) => setDifficulty(event.target.value)}>
            <option value="">全部</option>
            {difficulties.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
      </div>
      {statistics && (
        <dl className="evaluation-statistics">
          <div><dt>执行</dt><dd>{statistics.total}</dd></div>
          <div><dt>成功率</dt><dd>{(statistics.success_rate * 100).toFixed(0)}%</dd></div>
          <div><dt>Pass@1</dt><dd>{(statistics.pass_at_1 * 100).toFixed(0)}%</dd></div>
          <div><dt>Pass@3</dt><dd>{(statistics.pass_at_3 * 100).toFixed(0)}%</dd></div>
          <div><dt>平均耗时</dt><dd>{milliseconds(statistics.average_duration_ms)}</dd></div>
          <div><dt>中位耗时</dt><dd>{milliseconds(statistics.median_duration_ms)}</dd></div>
          <div><dt>平均 Token</dt><dd>{statistics.average_tokens.toFixed(0)}</dd></div>
          <div><dt>平均费用</dt><dd>{statistics.average_cost.toFixed(4)}</dd></div>
          <div><dt>平均工具调用</dt><dd>{statistics.average_tool_calls.toFixed(1)}</dd></div>
          <div><dt>平均重规划</dt><dd>{statistics.average_replans.toFixed(1)}</dd></div>
          <div><dt>平均人工介入</dt><dd>{statistics.average_manual_interventions.toFixed(1)}</dd></div>
        </dl>
      )}
      {comparableCases.length > 0 && (
        <section className="evaluation-comparison" aria-label="同一任务运行比较">
          <label>
            比较同一任务的运行
            <select value={comparisonCase} onChange={(event) => setComparisonCase(event.target.value)}>
              <option value="">选择任务</option>
              {comparableCases.map((caseId) => <option key={caseId} value={caseId}>{caseId}</option>)}
            </select>
          </label>
          {comparison.length > 0 && (
            <div className="evaluation-comparison-table" role="table" aria-label={`${comparisonCase} 运行比较`}>
              <div role="row" className="comparison-header"><span>尝试</span><span>状态</span><span>耗时</span><span>工具</span><span>得分</span></div>
              {comparison.map((item) => (
                <div role="row" key={item.id}>
                  <span>{item.attempt}</span><span className={`evaluation-status status-${item.status}`}>{labels[item.status]}</span>
                  <span>{milliseconds(item.duration_ms)}</span><span>{item.tool_calls}</span>
                  <span>{item.score == null ? "-" : `${item.score}/${item.max_score ?? "-"}`}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
      <div className="evaluation-list">
        {records.length ? records.map((item) => (
          <details key={item.id} className="evaluation-row">
            <summary>
              <span>{item.case_id}</span>
              <span className={`evaluation-status status-${item.status}`}>{labels[item.status]}</span>
              <small>{item.provider ?? "未配置 Provider"} · {item.model ?? "未调用模型"}</small>
            </summary>
            <dl>
              <div><dt>分类</dt><dd>{item.category} / {item.difficulty}</dd></div>
              <div><dt>第几次</dt><dd>{item.attempt}</dd></div>
              <div><dt>耗时</dt><dd>{milliseconds(item.duration_ms)}</dd></div>
              <div><dt>调用</dt><dd>模型 {item.model_calls}，工具 {item.tool_calls}</dd></div>
              <div><dt>Token</dt><dd>{item.input_tokens} 输入，{item.output_tokens} 输出</dd></div>
              <div><dt>费用估算</dt><dd>{item.estimated_cost.toFixed(4)}</dd></div>
              <div><dt>结束原因</dt><dd>{item.finish_reason}</dd></div>
              {item.failure_category && <div><dt>失败分类</dt><dd>{item.failure_category}</dd></div>}
            </dl>
          </details>
        )) : <p className="evaluation-empty">暂无符合筛选条件的评测记录。</p>}
      </div>
    </section>
  );
}
