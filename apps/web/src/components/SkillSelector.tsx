/** 会话级 Skill 选择：只影响下一次创建的 Run，活动运行继续使用已有快照。 */
import type { SkillDefinition } from "../types";

interface Props {
  skills: SkillDefinition[];
  value: string[];
  disabled: boolean;
  onChange: (skillIds: string[]) => void;
}

export default function SkillSelector({ skills, value, disabled, onChange }: Props) {
  const enabled = skills.filter((skill) => skill.enabled);
  if (!enabled.length) return null;
  return (
    <details className="skill-selector">
      <summary>Skills{value.length ? ` · ${value.length}` : ""}</summary>
      <div>
        {enabled.map((skill) => {
          const checked = value.includes(skill.id);
          return (
            <label key={skill.id}>
              <input
                type="checkbox"
                checked={checked}
                disabled={disabled}
                onChange={() => {
                  if (disabled) return;
                  onChange(checked ? value.filter((id) => id !== skill.id) : [...value, skill.id]);
                }}
              />
              <span>{skill.name}</span>
            </label>
          );
        })}
      </div>
    </details>
  );
}
