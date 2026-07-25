/** Agent Profile 的工具白名单：默认全选，受限模式仅暴露明确勾选的工具。 */
import type { AgentProfileInput, ToolSpec } from "../../types";

interface Props {
  form: AgentProfileInput;
  tools: ToolSpec[];
  onChange: (form: AgentProfileInput) => void;
}

export default function ToolSelectionFields({ form, tools, onChange }: Props) {
  const enabled = tools.filter((tool) => tool.enabled && tool.health.status !== "disabled");
  return (
    <fieldset>
      <legend>允许使用的工具</legend>
      <label>
        工具范围
        <select
          aria-label="Agent 工具范围"
          value={form.tool_selection_mode}
          onChange={(event) =>
            onChange({
              ...form,
              tool_selection_mode: event.target.value as AgentProfileInput["tool_selection_mode"],
              tool_ids: event.target.value === "all" ? [] : form.tool_ids,
            })
          }
        >
          <option value="all">允许所有当前已启用工具</option>
          <option value="selected">仅允许下列工具</option>
        </select>
      </label>
      {form.tool_selection_mode === "selected" && (
        <div className="tool-choice-list" aria-label="Agent 工具白名单">
          {enabled.map((tool) => {
            const checked = form.tool_ids.includes(tool.id);
            return (
              <label key={tool.id}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() =>
                    onChange({
                      ...form,
                      tool_ids: checked
                        ? form.tool_ids.filter((id) => id !== tool.id)
                        : [...form.tool_ids, tool.id],
                    })
                  }
                />
                <span>{tool.display_name}</span>
                <small>{tool.source_type} · {tool.risk}</small>
              </label>
            );
          })}
          {!enabled.length && <small>当前没有可选择的已启用工具。</small>}
        </div>
      )}
    </fieldset>
  );
}
