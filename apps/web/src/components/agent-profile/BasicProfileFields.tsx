/** Agent 名称、运行模式和启用状态等低门槛基础字段。 */
import type { AgentProfileInput } from "../../types";

interface Props {
  form: AgentProfileInput;
  onChange: (form: AgentProfileInput) => void;
}

export default function BasicProfileFields({ form, onChange }: Props) {
  return (
    <fieldset>
      <legend>基础配置</legend>
      <div className="form-grid">
        <label>
          名称
          <input
            aria-label="Agent 名称"
            value={form.name}
            onChange={(event) => onChange({ ...form, name: event.target.value })}
          />
        </label>
        <label>
          运行模式
          <select
            value={form.run_mode}
            onChange={(event) =>
              onChange({
                ...form,
                run_mode: event.target.value as "normal" | "competition",
              })
            }
          >
            <option value="normal">普通</option>
            <option value="competition">竞赛</option>
          </select>
        </label>
        <label className="wide">
          说明
          <textarea
            value={form.description}
            onChange={(event) =>
              onChange({ ...form, description: event.target.value })
            }
          />
        </label>
      </div>
      <div className="check-row">
        <label>
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(event) =>
              onChange({ ...form, enabled: event.target.checked })
            }
          />
          启用
        </label>
        <label>
          <input
            type="checkbox"
            checked={form.is_default}
            onChange={(event) =>
              onChange({ ...form, is_default: event.target.checked })
            }
          />
          默认
        </label>
      </div>
    </fieldset>
  );
}
