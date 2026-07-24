import type { ProviderConfig } from "../types";

interface Props {
  providers: ProviderConfig[];
  value: string | null;
  disabled: boolean;
  onChange: (providerId: string) => void;
}

function statusLabel(provider: ProviderConfig): string {
  if (provider.connection_status === "ok") return "可用";
  if (provider.connection_status === "failed") return "测试失败";
  return "未测试";
}

/** 输入框附近的会话级模型选择，不修改全局默认配置。 */
export default function ProviderSelector({ providers, value, disabled, onChange }: Props) {
  // 网络异常或旧页面状态不应让输入区崩溃；正常路径仍由调用方传入已启用配置。
  const availableProviders = Array.isArray(providers)
    ? providers.filter((provider) => provider.enabled)
    : [];

  return (
    <label className="provider-selector">
      <span>模型</span>
      <select
        aria-label="当前对话模型"
        value={value ?? ""}
        disabled={disabled || !availableProviders.length}
        onChange={(event) => onChange(event.target.value)}
      >
        {!availableProviders.length && <option value="">未配置可用模型</option>}
        {availableProviders.map((provider) => (
          <option key={provider.id} value={provider.id}>
            {provider.name} · {provider.model}（{statusLabel(provider)}）
          </option>
        ))}
      </select>
    </label>
  );
}
