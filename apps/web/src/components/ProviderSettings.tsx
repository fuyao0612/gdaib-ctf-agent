/** Provider 数据协调器：管理真实 API 操作，列表和表单分别负责展示。 */
import { useEffect, useState } from "react";
import { api } from "../api";
import type {
  ProviderConfig,
  ProviderDeletionImpact,
  ProviderConfigInput,
  ProviderPreset,
  SettingsMode,
} from "../types";
import ProviderForm from "./provider/ProviderForm";
import ProviderList from "./provider/ProviderList";
import {
  type ProviderPresetDescriptor,
  createEmptyProvider,
  explainProviderFailure,
  providerToInput,
  selectProviderPreset,
} from "./provider/model";

interface Props {
  csrf: string;
  providers: ProviderConfig[];
  onRefresh: () => Promise<void>;
  onChanged: () => Promise<void>;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  mode: SettingsMode;
}

export default function ProviderSettings(props: Props) {
  const [presets, setPresets] = useState<
    Record<string, ProviderPresetDescriptor>
  >({});
  const [form, setForm] = useState<ProviderConfigInput>(createEmptyProvider);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<ProviderConfig | null>(null);
  const [deleteImpact, setDeleteImpact] = useState<ProviderDeletionImpact | null>(null);
  const [deleteConfirmed, setDeleteConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api
      .providerPresets()
      .then(setPresets)
      .catch(() => setPresets({}));
  }, []);

  function edit(provider: ProviderConfig) {
    setEditingId(provider.id);
    setForm(providerToInput(provider));
    props.onNotice("编辑时留空 API Key 将保留现有密钥。");
  }

  function resetForm() {
    setEditingId(null);
    setForm(createEmptyProvider());
    props.onNotice("");
  }

  async function saveProvider() {
    setBusy(true);
    props.onError("");
    props.onNotice("");
    try {
      const payload = {
        ...form,
        api_key: form.api_key?.trim() || null,
        // 新手创建首个 Provider 时自动完成“启用 + 默认”，编辑时仍保留真实原值。
        enabled: props.mode === "beginner" ? true : form.enabled,
        is_default:
          props.mode === "beginner" && !editingId
            ? !props.providers.some((provider) => provider.is_default)
            : form.is_default,
      };
      const wasEditing = Boolean(editingId);
      if (editingId) await api.updateProvider(props.csrf, editingId, payload);
      else await api.createProvider(props.csrf, payload);
      resetForm();
      await props.onRefresh();
      await props.onChanged();
      props.onNotice(wasEditing ? "Provider 已更新" : "Provider 已创建");
    } catch (cause) {
      props.onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function requestRemove(provider: ProviderConfig) {
    setBusy(true);
    props.onError("");
    try {
      const impact = await api.providerDeletionImpact(props.csrf, provider.id);
      setDeleteCandidate(provider);
      setDeleteImpact(impact);
      setDeleteConfirmed(false);
    } catch (cause) {
      props.onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function removeProvider() {
    if (!deleteCandidate || !deleteImpact || deleteImpact.blocking_reasons.length) return;
    if (!deleteConfirmed) {
      setDeleteConfirmed(true);
      return;
    }
    setBusy(true);
    props.onError("");
    try {
      await api.deleteProvider(props.csrf, deleteCandidate.id);
      if (editingId === deleteCandidate.id) resetForm();
      setDeleteCandidate(null);
      setDeleteImpact(null);
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
        `连接测试成功：${result.model} · ${result.structured_mode} · ${result.tool_call_mode} · ${result.latency_ms} ms`,
      );
    } catch (cause) {
      await props.onRefresh().catch(() => undefined);
      props.onError(explainProviderFailure(String(cause)));
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

  function changePreset(preset: ProviderPreset) {
    setForm((current) => selectProviderPreset(current, preset, presets));
  }

  return (
    <section>
      <div className="settings-title">
        <h3>模型 Provider</h3>
        <button onClick={resetForm}>新增配置</button>
      </div>
      <ProviderList
        providers={props.providers}
        busy={busy}
        mode={props.mode}
        onTest={(id) => void testProvider(id)}
        onDiscoverModels={(id) => void discoverModels(id)}
        onEdit={edit}
        onRemove={(provider) => void requestRemove(provider)}
      />
      {deleteCandidate && deleteImpact && (
        <section
          className="provider-delete-confirmation"
          role="dialog"
          aria-modal="true"
          aria-labelledby="provider-delete-title"
        >
          <h4 id="provider-delete-title">删除模型配置</h4>
          <p>
            配置：<strong>{deleteCandidate.name} · {deleteCandidate.model}</strong>
          </p>
          {deleteImpact.blocking_reasons.length ? (
            <>
              <p role="alert">当前不能安全删除：</p>
              <ul>
                {deleteImpact.blocking_reasons.map((reason) => <li key={reason}>{reason}</li>)}
              </ul>
              <button onClick={() => { setDeleteCandidate(null); setDeleteImpact(null); }}>关闭</button>
            </>
          ) : (
            <>
              <p>
                {deleteImpact.affected_thread_count
                  ? `${deleteImpact.affected_thread_count} 个会话会回退到 ${deleteImpact.fallback_provider?.name} · ${deleteImpact.fallback_provider?.model}。`
                  : "没有会话正在使用该配置。"}
                删除后 API Key 将从本地配置中移除，历史 Run 继续使用已保存的脱敏快照展示。
              </p>
              <div className="provider-delete-actions">
                <button onClick={() => { setDeleteCandidate(null); setDeleteImpact(null); }}>取消</button>
                <button className="danger" disabled={busy} onClick={() => void removeProvider()}>
                  {deleteConfirmed ? "确认永久删除" : "我已了解影响，继续"}
                </button>
              </div>
            </>
          )}
        </section>
      )}
      <ProviderForm
        form={form}
        editing={Boolean(editingId)}
        busy={busy}
        mode={props.mode}
        onChange={setForm}
        onPresetChange={changePreset}
        onSubmit={() => void saveProvider()}
      />
    </section>
  );
}
