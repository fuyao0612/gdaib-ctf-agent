/** 工作流、人工介入和完成验证，这些字段共同决定运行怎样结束。 */
import type { AgentProfileInput } from "../../types";
import { changeCompletionMode, changeWorkflowPreset } from "./model";

interface Props {
  form: AgentProfileInput;
  schemaText: string;
  onChange: (form: AgentProfileInput) => void;
  onSchemaChange: (value: string) => void;
}

export default function WorkflowValidationFields({
  form,
  schemaText,
  onChange,
  onSchemaChange,
}: Props) {
  return (
    <fieldset>
      <legend>工作流、人工介入与完成验证</legend>
      <div className="form-grid">
        <label>
          完成模式
          <select
            aria-label="完成模式"
            value={form.completion_mode}
            onChange={(event) =>
              onChange(
                changeCompletionMode(
                  form,
                  event.target.value as AgentProfileInput["completion_mode"],
                ),
              )
            }
          >
            <option value="advisory">建议回答（未经外部验证）</option>
            <option value="structured">结构化输出</option>
            <option value="evidence">证据验证</option>
          </select>
        </label>
        <label>
          工作流预设
          <select
            aria-label="工作流预设"
            value={form.workflow.preset}
            onChange={(event) =>
              onChange(
                changeWorkflowPreset(
                  form,
                  event.target
                    .value as AgentProfileInput["workflow"]["preset"],
                ),
              )
            }
          >
            <option value="direct">直接回答</option>
            <option value="planned">规划后执行</option>
            <option value="verified">规划、验证、失败后重规划</option>
          </select>
        </label>
      </div>
      {form.completion_mode === "structured" && (
        <label>
          JSON Schema
          <textarea
            aria-label="JSON Schema"
            value={schemaText}
            onChange={(event) => onSchemaChange(event.target.value)}
          />
        </label>
      )}
      <div className="form-grid">
        <label>
          普通模式补充
          <select
            value={form.intervention_policy.normal_mode}
            onChange={(event) =>
              onChange({
                ...form,
                intervention_policy: {
                  ...form.intervention_policy,
                  normal_mode: event.target.value as "wait" | "fail",
                },
              })
            }
          >
            <option value="wait">等待用户</option>
            <option value="fail">明确失败</option>
          </select>
        </label>
        <label>
          竞赛模式补充
          <select
            value={form.intervention_policy.competition_mode}
            onChange={(event) =>
              onChange({
                ...form,
                intervention_policy: {
                  ...form.intervention_policy,
                  competition_mode: event.target.value as "replan" | "fail",
                },
              })
            }
          >
            <option value="replan">自主重规划</option>
            <option value="fail">明确失败</option>
          </select>
        </label>
        <label>
          最多补充次数
          <input
            aria-label="最多补充次数"
            type="number"
            min="0"
            max="20"
            value={form.intervention_policy.max_requests}
            onChange={(event) =>
              onChange({
                ...form,
                intervention_policy: {
                  ...form.intervention_policy,
                  max_requests: Number(event.target.value),
                },
              })
            }
          />
        </label>
      </div>
    </fieldset>
  );
}
