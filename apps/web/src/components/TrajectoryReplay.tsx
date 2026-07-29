import { useId, useState } from "react";
import type { ExecutionStep } from "../types";
import { ExecutionTimeline } from "./RunSummary";

interface ImportedTrajectory {
  schema_version: string;
  run?: { id?: string; status?: string };
  execution_mode?: string;
  steps: ExecutionStep[];
}

function validateTrajectory(value: unknown): ImportedTrajectory {
  if (!value || typeof value !== "object") throw new Error("文件不是有效的 JSON 对象。");
  const data = value as Partial<ImportedTrajectory>;
  if (data.schema_version !== "2.0") throw new Error("仅支持轨迹 schema 2.0 文件。");
  if (!Array.isArray(data.steps)) throw new Error("轨迹缺少 steps 数组。");
  if (!data.steps.every((item) => item && typeof item.sequence === "number" && typeof item.goal === "string")) {
    throw new Error("轨迹步骤字段不完整。");
  }
  return data as ImportedTrajectory;
}

/** 导入只存于浏览器状态，绝不写回原 Run 或触发工具调用。 */
export function TrajectoryReplay() {
  const inputId = useId();
  const [importOpen, setImportOpen] = useState(false);
  const [trajectory, setTrajectory] = useState<ImportedTrajectory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const readFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { setError("轨迹文件不能超过 5 MB。"); return; }
    try {
      setTrajectory(validateTrajectory(JSON.parse(await file.text())));
      setError(null);
    } catch (reason) {
      setTrajectory(null);
      setError(reason instanceof Error ? reason.message : "轨迹读取失败。");
    }
  };
  return <section className="trajectory-replay" aria-label="导入轨迹只读回放">
    <button type="button" onClick={() => setImportOpen((open) => !open)}>导入轨迹</button>
    {importOpen && <>
      <label htmlFor={inputId}>选择轨迹 JSON 文件</label>
      <input id={inputId} type="file" accept="application/json,.json" onChange={(event) => void readFile(event.target.files?.[0])} />
    </>}
    {error && <p role="alert" className="step-error">{error}</p>}
    {trajectory && <div><p>只读回放 · {trajectory.execution_mode ?? "历史轨迹"}</p><ExecutionTimeline steps={trajectory.steps} /></div>}
  </section>;
}
