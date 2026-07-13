/** Provider 列表、连接测试与编辑表单。密钥字段只发送到后端，不在列表回显。 */
import { type FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import type {
  FallbackCategory,
  ProviderConfig,
  ProviderConfigInput,
  ProviderPreset,
  StructuredMode,
} from "../types";

const emptyProvider: ProviderConfigInput = {
  name: "",
  preset: "deepseek",
  base_url: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  api_key: "",
  enabled: true,
  is_default: false,
  fallback_order: null,
  timeout_seconds: 60,
  max_retries: 2,
  structured_mode: "auto",
  input_price_per_million: 0,
  output_price_per_million: 0,
  fallback_on: ["rate_limit", "timeout", "service"],
};

interface Props {
  csrf: string;
  providers: ProviderConfig[];
  onRefresh: () => Promise<void>;
  onChanged: () => Promise<void>;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
}

export default function ProviderSettings(props: Props) {
  const [presets, setPresets] = useState<
    Record<string, { base_url: string; model: string }>
  >({});
  const [form, setForm] = useState<ProviderConfigInput>(emptyProvider);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api
      .providerPresets()
      .then(setPresets)
      .catch(() => setPresets({}));
  }, []);

  function selectPreset(preset: ProviderPreset) {
    const value = presets[preset];
    setForm((current) => ({
      ...current,
      preset,
      ...(value ? { base_url: value.base_url, model: value.model } : {}),
    }));
  }

  function edit(value: ProviderConfig) {
    setEditingId(value.id);
    setForm({
      name: value.name,
      preset: value.preset,
      base_url: value.base_url,
      model: value.model,
      api_key: "",
      enabled: value.enabled,
      is_default: value.is_default,
      fallback_order: value.fallback_order,
      timeout_seconds: value.timeout_seconds,
      max_retries: value.max_retries,
      structured_mode: value.structured_mode,
      input_price_per_million: value.input_price_per_million,
      output_price_per_million: value.output_price_per_million,
      fallback_on: value.fallback_on,
    });
    props.onNotice("编辑时留空 API Key 将保留现有密钥。");
  }

  function resetForm() {
    setEditingId(null);
    setForm(emptyProvider);
    props.onNotice("");
  }

  async function saveProvider(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    props.onError("");
    props.onNotice("");
    try {
      const payload = { ...form, api_key: form.api_key?.trim() || null };
      const wasEditing = Boolean(editingId);
      if (editingId) await api.updateProvider(props.csrf, editingId, payload);
      else await api.createProvider(props.csrf, payload);
      setForm(emptyProvider);
      setEditingId(null);
      await props.onRefresh();
      await props.onChanged();
      props.onNotice(wasEditing ? "Provider 已更新" : "Provider 已创建");
    } catch (cause) {
      props.onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function removeProvider(id: string) {
    setBusy(true);
    props.onError("");
    try {
      await api.deleteProvider(props.csrf, id);
      await props.onRefresh();
      await props.onChanged();
      props.onNotice("Provider 已删除");
    } catch (cause) {
      props.onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function testProvider(id: string) {
    setBusy(true);
    props.onError("");
    props.onNotice("正在调用模型进行真实连接测试…");
    try {
      const result = await api.testProvider(props.csrf, id);
      await props.onRefresh();
      await props.onChanged();
      props.onNotice(
        `连接测试成功：${result.model} · ${result.structured_mode} · ${result.latency_ms} ms`,
      );
    } catch (cause) {
      await props.onRefresh().catch(() => undefined);
      props.onError(String(cause));
      props.onNotice("");
    } finally {
      setBusy(false);
    }
  }

  async function discoverModels(id: string) {
    setBusy(true);
    props.onError("");
    try {
      const result = await api.discoverProviderModels(props.csrf, id);
      props.onNotice(
        result.models.length
          ? `发现模型：${result.models.join("、")}`
          : "端点未返回模型列表，可继续手动填写模型名称",
      );
    } catch (cause) {
      props.onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="settings-title">
        <h3>模型 Provider</h3>
        <button onClick={resetForm}>新增配置</button>
      </div>
      <div className="provider-table">
        {props.providers.map((provider) => (
          <article
            key={provider.id}
            className={
              provider.is_default ? "provider-row default" : "provider-row"
            }
          >
            <div>
              <strong>{provider.name}</strong>
              <small>
                {provider.preset} · {provider.model}
              </small>
              <small>{provider.base_url}</small>
              <small>
                连接：
                {provider.connection_status === "ok"
                  ? `成功 · ${provider.actual_model ?? provider.model}`
                  : provider.connection_status === "failed"
                    ? `失败 · ${provider.last_test_error}`
                    : "尚未测试"}
                {provider.last_tested_at
                  ? ` · ${new Date(provider.last_tested_at).toLocaleString()}`
                  : ""}
              </small>
            </div>
            <div className="provider-flags">
              <span>{provider.has_api_key ? "密钥已保存" : "缺少密钥"}</span>
              {provider.is_default && <span>默认</span>}
              {!provider.enabled && <span>已停用</span>}
            </div>
            <div>
              <button
                disabled={busy}
                onClick={() => void testProvider(provider.id)}
              >
                连接测试
              </button>
              <button
                disabled={busy}
                onClick={() => void discoverModels(provider.id)}
              >
                发现模型
              </button>
              <button onClick={() => edit(provider)}>编辑</button>
              <button
                className="danger"
                disabled={provider.is_default || busy}
                onClick={() => void removeProvider(provider.id)}
              >
                删除
              </button>
            </div>
          </article>
        ))}
      </div>
      <form className="settings-form" onSubmit={saveProvider}>
        <h4>{editingId ? "编辑 Provider" : "新增 Provider"}</h4>
        <div className="form-grid">
          <label>
            名称
            <input
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
              required
            />
          </label>
          <label>
            厂商预设
            <select
              value={form.preset}
              onChange={(event) =>
                selectPreset(event.target.value as ProviderPreset)
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
                setForm({ ...form, base_url: event.target.value })
              }
              required
            />
          </label>
          <label>
            模型
            <input
              value={form.model}
              onChange={(event) =>
                setForm({ ...form, model: event.target.value })
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
              placeholder={
                editingId ? "留空以保留现有密钥" : "输入 Provider API Key"
              }
              onChange={(event) =>
                setForm({ ...form, api_key: event.target.value })
              }
              required={!editingId}
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
                setForm({
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
                setForm({ ...form, max_retries: Number(event.target.value) })
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
                setForm({
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
                setForm({
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
                setForm({
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
                setForm({
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
          {(
            [
              "rate_limit",
              "timeout",
              "service",
              "invalid_output",
            ] as FallbackCategory[]
          ).map((category) => (
            <label key={category}>
              <input
                type="checkbox"
                checked={form.fallback_on.includes(category)}
                onChange={(event) =>
                  setForm({
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
                setForm({ ...form, enabled: event.target.checked })
              }
            />
            启用
          </label>
          <label>
            <input
              type="checkbox"
              checked={form.is_default}
              onChange={(event) =>
                setForm({ ...form, is_default: event.target.checked })
              }
            />
            设为默认
          </label>
        </div>
        <button className="primary" disabled={busy}>
          {editingId ? "保存修改" : "创建 Provider"}
        </button>
      </form>
    </section>
  );
}
