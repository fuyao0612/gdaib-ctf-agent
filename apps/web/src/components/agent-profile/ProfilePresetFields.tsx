/** 面向大多数用户的 Agent 预设；复杂字段仍保留在后续向导步骤。 */
import type { AgentProfileInput } from "../../types";
import {
  applyProfilePreset,
  PROFILE_PRESETS,
  type ProfilePreset,
} from "./model";

interface Props {
  form: AgentProfileInput;
  onChange: (form: AgentProfileInput) => void;
}

export default function ProfilePresetFields({ form, onChange }: Props) {
  const selected: ProfilePreset =
    form.planning_strategy === "direct"
      ? "fast"
      : form.planning_strategy === "hybrid"
        ? "deep"
        : form.budget.max_duration_seconds >= 240
          ? "standard"
          : "standard";

  return (
    <section className="profile-presets" aria-labelledby="profile-presets-title">
      <div className="profile-presets-heading">
        <div>
          <h4 id="profile-presets-title">先选一个运行预设</h4>
          <p>推荐预设已经包含合理的预算、验证和重规划策略，通常不需要逐项调整。</p>
        </div>
        <span>可随时在高级步骤修改</span>
      </div>
      <div className="profile-preset-grid" role="radiogroup" aria-label="Agent 运行预设">
        {PROFILE_PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            className={selected === preset.id ? "selected" : ""}
            role="radio"
            aria-checked={selected === preset.id}
            onClick={() => onChange(applyProfilePreset(form, preset.id))}
          >
            <strong>{preset.label}</strong>
            <small>{preset.description}</small>
          </button>
        ))}
      </div>
    </section>
  );
}
