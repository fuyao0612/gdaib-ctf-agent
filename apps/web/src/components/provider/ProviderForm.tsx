/** Provider 的全部可写字段；组件不保存密钥，也不直接发网络请求。 */
import type { FormEvent } from "react";
import type {
  ProviderConfigInput,
  ProviderPreset,
  StructuredMode,
} from "../../types";
import { FALLBACK_CATEGORIES } from "./model";

interface Props {
  form: ProviderConfigInput;
  editing: boolean;
  busy: boolean;
  onChange: (form: ProviderConfigInput) => void;
  onPresetChange: (preset: ProviderPreset) => void;
  onSubmit: () => void;
}

export default function ProviderForm({
  form,
  editing,
  busy,
  onChange,
  onPresetChange,
  onSubmit,
}: Props) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  return (
    <form className="settings-form" onSubmit={submit}>
      <h4>{editing ? "编辑 Provider" : "新增 Provider"}</h4>
      <div className="form-grid">
        <label>
          名称
          <input
            value={form.name}
            onChange={(event) =>
              onChange({ ...form, name: event.target.value })
            }
            required
          />
        </label>
        <label>
          厂商预设
          <select
            value={form.preset}
            onChange={(event) =>
              onPresetChange(event.target.value as ProviderPreset)
            }
          >
            <option value="deepseek">DeepSeek</option>
            <option value="qwen">阿里云百炼 / 千问</option>
            <option value="glm">智谱 GLM</option>
            <option value="custom">自定义兼容 API</option>
          </select>
        </label>
        <label className="wide">
          Base URL
          <input
            value={form.base_url}
            onChange={(event) =>
              onChange({ ...form, base_url: event.target.value })
            }
            required
          />
        </label>
        <label>
          模型
          <input
            value={form.model}
            onChange={(event) =>
              onChange({ ...form, model: event.target.value })
            }
            required
          />
        </label>
        <label>
          API Key
          <input
            type="password"
            autoComplete="new-password"
            value={form.api_key ?? ""}
            placeholder={editing ? "留空以保留现有密钥" : "输入 Provider API Key"}
            onChange={(event) =>
              onChange({ ...form, api_key: event.target.value })
            }
            required={!editing}
          />
        </label>
        <label>
          超时（秒）
          <input
            type="number"
            min="1"
            max="600"
            value={form.timeout_seconds}
            onChange={(event) =>
              onChange({
                ...form,
                timeout_seconds: Number(event.target.value),
              })
            }
          />
        </label>
        <label>
          重试次数
          <input
            type="number"
            min="0"
            max="8"
            value={form.max_retries}
            onChange={(event) =>
              onChange({ ...form, max_retries: Number(event.target.value) })
            }
          />
        </label>
        <label>
          输入价格/百万 Token
          <input
            type="number"
            min="0"
            step="0.0001"
            value={form.input_price_per_million}
            onChange={(event) =>
              onChange({
                ...form,
                input_price_per_million: Number(event.target.value),
              })
            }
          />
        </label>
        <label>
          输出价格/百万 Token
          <input
            type="number"
            min="0"
            step="0.0001"
            value={form.output_price_per_million}
            onChange={(event) =>
              onChange({
                ...form,
                output_price_per_million: Number(event.target.value),
              })
            }
          />
        </label>
        <label>
          备用顺序
          <input
            type="number"
            min="0"
            max="100"
            value={form.fallback_order ?? ""}
            onChange={(event) =>
              onChange({
                ...form,
                fallback_order:
                  event.target.value === ""
                    ? null
                    : Number(event.target.value),
              })
            }
          />
        </label>
        <label>
          结构化模式
          <select
            value={form.structured_mode}
            onChange={(event) =>
              onChange({
                ...form,
                structured_mode: event.target.value as StructuredMode,
              })
            }
          >
            <option value="auto">自动协商（推荐）</option>
            <option value="json_schema">JSON Schema</option>
            <option value="json_object">JSON Object</option>
            <option value="prompt_json">提示词兼容模式</option>
          </select>
        </label>
      </div>
      <fieldset className="check-row">
        <legend>允许触发备用模型的错误</legend>
        {FALLBACK_CATEGORIES.map((category) => (
          <label key={category}>
            <input
              type="checkbox"
              checked={form.fallback_on.includes(category)}
              onChange={(event) =>
                onChange({
                  ...form,
                  fallback_on: event.target.checked
                    ? [...form.fallback_on, category]
                    : form.fallback_on.filter((value) => value !== category),
                })
              }
            />
            {category}
          </label>
        ))}
      </fieldset>
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
          设为默认
        </label>
      </div>
      <button className="primary" disabled={busy}>
        {editing ? "保存修改" : "创建 Provider"}
      </button>
    </form>
  );
}
