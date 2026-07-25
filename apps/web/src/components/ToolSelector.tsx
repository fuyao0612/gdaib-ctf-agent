/** 会话级工具选择：只能继承 Profile 或收缩为白名单，影响下一次 Run。 */
import type { ThreadToolSelectionMode, ToolSpec } from "../types";

interface Props {
  tools: ToolSpec[];
  mode: ThreadToolSelectionMode;
  value: string[];
  disabled: boolean;
  onChange: (mode: ThreadToolSelectionMode, toolIds: string[]) => void;
}

export default function ToolSelector({ tools, mode, value, disabled, onChange }: Props) {
  const enabled = tools.filter((tool) => tool.enabled && tool.health.status !== "disabled");
  if (!enabled.length) return null;
  return (
    <details className="skill-selector">
      <summary>工具{mode === "selected" ? ` · ${value.length}` : " · 继承 Agent"}</summary>
      <div>
        <label>
          <select
            aria-label="本次对话工具范围"
            value={mode}
            disabled={disabled}
            onChange={(event) => onChange(event.target.value as ThreadToolSelectionMode, [])}
          >
            <option value="inherit">继承 Agent 配置</option>
            <option value="selected">仅使用下列工具</option>
          </select>
        </label>
        {mode === "selected" && enabled.map((tool) => {
          const checked = value.includes(tool.id);
          return (
            <label key={tool.id}>
              <input
                type="checkbox"
                checked={checked}
                disabled={disabled}
                onChange={() => onChange(mode, checked ? value.filter((id) => id !== tool.id) : [...value, tool.id])}
              />
              <span>{tool.display_name}</span>
            </label>
          );
        })}
      </div>
    </details>
  );
}
