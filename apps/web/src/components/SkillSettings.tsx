/** 声明式 Skills 设置：只编辑任务模板文本，不接收或执行文件和脚本。 */
import { useEffect, useState } from "react";
import { api } from "../api";
import type { SkillDefinition, SkillInput } from "../types";

interface Props {
  csrf: string;
  skills: SkillDefinition[];
  onRefresh: () => Promise<void>;
  onNotice: (value: string) => void;
  onError: (value: string) => void;
}

const emptySkill: SkillInput = {
  name: "",
  description: "",
  prompt: "",
  steps: [],
  checklist: [],
  enabled: true,
};

function toForm(skill: SkillDefinition): SkillInput {
  return {
    name: skill.name,
    description: skill.description,
    prompt: skill.prompt,
    steps: skill.steps,
    checklist: skill.checklist,
    enabled: skill.enabled,
  };
}

function lines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

export default function SkillSettings(props: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<SkillInput>(emptySkill);
  const [busy, setBusy] = useState(false);
  const [deletePending, setDeletePending] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId || props.skills.some((skill) => skill.id === selectedId)) return;
    setSelectedId(null);
    setForm(emptySkill);
  }, [props.skills, selectedId]);

  const selected = props.skills.find((skill) => skill.id === selectedId) ?? null;
  const choose = (skill: SkillDefinition | null) => {
    setSelectedId(skill?.id ?? null);
    setDeletePending(null);
    setForm(skill ? toForm(skill) : emptySkill);
  };

  async function save() {
    setBusy(true);
    props.onError("");
    try {
      if (selected) await api.updateSkill(props.csrf, selected.id, form);
      else await api.createSkill(props.csrf, form);
      await props.onRefresh();
      props.onNotice(selected ? "Skill 已更新" : "Skill 已创建");
      if (!selected) setForm(emptySkill);
    } catch (cause) {
      props.onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selected) return;
    setBusy(true);
    props.onError("");
    try {
      await api.deleteSkill(props.csrf, selected.id);
      await props.onRefresh();
      choose(null);
      props.onNotice("Skill 已删除，引用它的对话已取消选择");
    } catch (cause) {
      props.onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="skill-settings">
      <div className="settings-title">
        <div><h3>Skills</h3><small>声明式任务模板</small></div>
        <button onClick={() => choose(null)} disabled={busy}>新建</button>
      </div>
      <div className="skill-list" aria-label="Skill 列表">
        {props.skills.map((skill) => (
          <button
            key={skill.id}
            className={selectedId === skill.id ? "selected" : ""}
            onClick={() => choose(skill)}
          >
            <span>{skill.name}</span><small>{skill.enabled ? "已启用" : "已停用"}</small>
          </button>
        ))}
        {!props.skills.length && <p className="muted">暂无 Skill。</p>}
      </div>
      <form
        className="settings-form"
        onSubmit={(event) => { event.preventDefault(); void save(); }}
      >
        <div className="form-grid">
          <label>
            名称
            <input aria-label="Skill 名称" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
          </label>
          <label>
            状态
            <select aria-label="Skill 状态" value={form.enabled ? "enabled" : "disabled"} onChange={(event) => setForm({ ...form, enabled: event.target.value === "enabled" })}>
              <option value="enabled">启用</option><option value="disabled">停用</option>
            </select>
          </label>
          <label className="wide">
            说明
            <input aria-label="Skill 说明" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
          </label>
          <label className="wide">
            提示词
            <textarea aria-label="Skill 提示词" value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })} />
          </label>
          <label>
            步骤（每行一项）
            <textarea aria-label="Skill 步骤" value={form.steps.join("\n")} onChange={(event) => setForm({ ...form, steps: lines(event.target.value) })} />
          </label>
          <label>
            检查清单（每行一项）
            <textarea aria-label="Skill 检查清单" value={form.checklist.join("\n")} onChange={(event) => setForm({ ...form, checklist: lines(event.target.value) })} />
          </label>
        </div>
        <div className="skill-actions">
          <button className="primary" disabled={busy || !form.name.trim() || !form.prompt.trim()}>{selected ? "保存 Skill" : "创建 Skill"}</button>
          {selected && (deletePending === selected.id ? (
            <><span>确认删除“{selected.name}”？</span><button className="danger" type="button" disabled={busy} onClick={() => void remove()}>确认删除</button><button type="button" onClick={() => setDeletePending(null)}>取消</button></>
          ) : <button className="danger" type="button" disabled={busy} onClick={() => setDeletePending(selected.id)}>删除</button>)}
        </div>
      </form>
    </section>
  );
}
