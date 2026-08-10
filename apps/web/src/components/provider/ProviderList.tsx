/** 展示 Provider 状态、模型目录和操作按钮，网络请求仍由上层调用真实 API。 */
import { useMemo, useState } from "react";
import type { ProviderConfig } from "../../types";

interface Props {
  providers: ProviderConfig[];
  busy: boolean;
  discoveredModels: Record<string, string[]>;
  onTest: (id: string) => void;
  onDiscoverModels: (id: string) => void;
  onUseModel: (provider: ProviderConfig, model: string) => void;
  onEdit: (provider: ProviderConfig) => void;
  onRemove: (provider: ProviderConfig) => void;
}

function connectionSummary(provider: ProviderConfig): string {
  if (provider.connection_status === "ok")
    return `成功 · ${provider.actual_model ?? provider.model}`;
  if (provider.connection_status === "failed")
    return `失败 · ${provider.last_test_error}`;
  return "尚未测试";
}

function modelKind(model: string): { label: string; selectable: boolean } {
  if (/(embedding|reranker|bge[-_/])/i.test(model))
    return { label: "嵌入 / 重排模型", selectable: false };
  if (/(tts|asr|voice|audio|sensevoice|cosyvoice)/i.test(model))
    return { label: "音频模型", selectable: false };
  if (/(image|kolors|wan\d|z-image|ocr)/i.test(model))
    return { label: "图像 / 多媒体模型", selectable: false };
  return { label: "对话 / 推理模型", selectable: true };
}

export default function ProviderList({
  providers,
  busy,
  discoveredModels,
  onTest,
  onDiscoverModels,
  onUseModel,
  onEdit,
  onRemove,
}: Props) {
  const [queries, setQueries] = useState<Record<string, string>>({});
  const normalizedQueries = useMemo(
    () => Object.fromEntries(
      Object.entries(queries).map(([id, value]) => [id, value.trim().toLowerCase()]),
    ),
    [queries],
  );
  return (
    <div className="provider-table">
      {providers.map((provider) => {
        const models = discoveredModels[provider.id];
        const query = normalizedQueries[provider.id] ?? "";
        const visibleModels = models?.filter((model) => model.toLowerCase().includes(query)) ?? [];
        return (
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
              连接：{connectionSummary(provider)}
              {provider.last_tested_at
                ? ` · ${new Date(provider.last_tested_at).toLocaleString()}`
                : ""}
            </small>
            <small>工具调用：{provider.tool_call_mode}</small>
          </div>
          <div className="provider-flags">
            <span>{provider.has_api_key ? "密钥已保存" : "缺少密钥"}</span>
            {provider.is_default && <span>默认</span>}
            {!provider.enabled && <span>已停用</span>}
          </div>
          <div>
            <button disabled={busy} onClick={() => onTest(provider.id)}>
              连接测试
            </button>
            <button disabled={busy} onClick={() => onDiscoverModels(provider.id)}>
              {models ? "刷新模型" : "查看可用模型"}
            </button>
            <button onClick={() => onEdit(provider)}>编辑</button>
            <button
              className="danger"
              disabled={busy}
              onClick={() => onRemove(provider)}
            >
              删除
            </button>
          </div>
          {models && (
            <section className="provider-model-catalog" aria-label={`${provider.name} 可用模型`}>
              <div className="provider-model-heading">
                <div>
                  <strong>可用模型</strong>
                  <small>来自中转服务的 /models 接口，共 {models.length} 个</small>
                </div>
                <input
                  aria-label={`搜索 ${provider.name} 模型`}
                  placeholder="搜索模型名称…"
                  value={queries[provider.id] ?? ""}
                  onChange={(event) => setQueries((current) => ({ ...current, [provider.id]: event.target.value }))}
                />
              </div>
              {visibleModels.length ? (
                <div className="provider-model-grid">
                  {visibleModels.map((model) => {
                    const kind = modelKind(model);
                    return (
                    <button
                      type="button"
                      className={model === provider.model ? "selected" : ""}
                      disabled={busy || model === provider.model || !kind.selectable}
                      key={model}
                      onClick={() => onUseModel(provider, model)}
                    >
                      <span><strong>{model}</strong><small>{kind.label}</small></span>
                      <small>{model === provider.model ? "当前使用" : kind.selectable ? "切换到此模型" : "不可用于 Agent 对话"}</small>
                    </button>
                    );
                  })}
                </div>
              ) : (
                <p className="muted">{models.length ? "没有匹配的模型。" : "中转服务没有返回模型列表，可使用“编辑”手动填写。"}</p>
              )}
            </section>
          )}
        </article>
      );})}
    </div>
  );
}
