/** 提示词模板、上下文窗口与线程记忆策略。 */
import type { AgentProfileInput } from "../../types";

interface Props {
  form: AgentProfileInput;
  preview: string;
  onChange: (form: AgentProfileInput) => void;
  onPreview: () => void;
}

export default function ContextMemoryFields({
  form,
  preview,
  onChange,
  onPreview,
}: Props) {
  return (
    <fieldset>
      <legend>提示词、上下文与记忆</legend>
      <label>
        用户提示词模板
        <textarea
          aria-label="用户提示词模板"
          value={form.user_prompt_template}
          onChange={(event) =>
            onChange({ ...form, user_prompt_template: event.target.value })
          }
        />
      </label>
      <button type="button" onClick={onPreview}>
        模板预览
      </button>
      {preview && <pre data-testid="template-preview">{preview}</pre>}
      <div className="form-grid">
        <label>
          最近消息
          <input
            type="number"
            value={form.context_policy.recent_message_limit}
            onChange={(event) =>
              onChange({
                ...form,
                context_policy: {
                  ...form.context_policy,
                  recent_message_limit: Number(event.target.value),
                },
              })
            }
          />
        </label>
        <label>
          附件字符
          <input
            type="number"
            value={form.context_policy.text_attachment_char_limit}
            onChange={(event) =>
              onChange({
                ...form,
                context_policy: {
                  ...form.context_policy,
                  text_attachment_char_limit: Number(event.target.value),
                },
              })
            }
          />
        </label>
        <label>
          最大事实
          <input
            type="number"
            value={form.memory_policy.max_facts}
            onChange={(event) =>
              onChange({
                ...form,
                memory_policy: {
                  ...form.memory_policy,
                  max_facts: Number(event.target.value),
                },
              })
            }
          />
        </label>
      </div>
      <div className="check-row">
        <label>
          <input
            type="checkbox"
            checked={form.memory_policy.enabled}
            onChange={(event) =>
              onChange({
                ...form,
                memory_policy: {
                  ...form.memory_policy,
                  enabled: event.target.checked,
                },
              })
            }
          />
          启用记忆
        </label>
        <label>
          <input
            type="checkbox"
            checked={form.context_policy.include_thread_summary}
            onChange={(event) =>
              onChange({
                ...form,
                context_policy: {
                  ...form.context_policy,
                  include_thread_summary: event.target.checked,
                },
              })
            }
          />
          包含线程摘要
        </label>
        <label>
          <input
            type="checkbox"
            checked={form.context_policy.include_run_summaries}
            onChange={(event) =>
              onChange({
                ...form,
                context_policy: {
                  ...form.context_policy,
                  include_run_summaries: event.target.checked,
                },
              })
            }
          />
          包含运行摘要
        </label>
        <label>
          <input
            type="checkbox"
            checked={form.context_policy.include_memories}
            onChange={(event) =>
              onChange({
                ...form,
                context_policy: {
                  ...form.context_policy,
                  include_memories: event.target.checked,
                },
              })
            }
          />
          包含重要事实
        </label>
        <label>
          <input
            type="checkbox"
            checked={form.memory_policy.persist_important_facts}
            onChange={(event) =>
              onChange({
                ...form,
                memory_policy: {
                  ...form.memory_policy,
                  persist_important_facts: event.target.checked,
                },
              })
            }
          />
          提取重要事实
        </label>
      </div>
    </fieldset>
  );
}
