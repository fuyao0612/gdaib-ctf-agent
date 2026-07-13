/** 只展示 Provider 状态和操作按钮，连接测试仍由上层调用真实 API。 */
import type { ProviderConfig } from "../../types";

interface Props {
  providers: ProviderConfig[];
  busy: boolean;
  onTest: (id: string) => void;
  onDiscoverModels: (id: string) => void;
  onEdit: (provider: ProviderConfig) => void;
  onRemove: (id: string) => void;
}

function connectionSummary(provider: ProviderConfig): string {
  if (provider.connection_status === "ok")
    return `成功 · ${provider.actual_model ?? provider.model}`;
  if (provider.connection_status === "failed")
    return `失败 · ${provider.last_test_error}`;
  return "尚未测试";
}

export default function ProviderList({
  providers,
  busy,
  onTest,
  onDiscoverModels,
  onEdit,
  onRemove,
}: Props) {
  return (
    <div className="provider-table">
      {providers.map((provider) => (
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
            <button
              disabled={busy}
              onClick={() => onDiscoverModels(provider.id)}
            >
              发现模型
            </button>
            <button onClick={() => onEdit(provider)}>编辑</button>
            <button
              className="danger"
              disabled={provider.is_default || busy}
              onClick={() => onRemove(provider.id)}
            >
              删除
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}
