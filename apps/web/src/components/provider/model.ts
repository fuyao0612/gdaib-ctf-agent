/** Provider 表单默认值和服务端视图转换，不依赖 React。 */
import type {
  FallbackCategory,
  ProviderConfig,
  ProviderConfigInput,
  ProviderPreset,
} from "../../types";

export interface ProviderPresetDescriptor {
  base_url: string;
  model: string;
}

const PROVIDER_NAMES: Record<ProviderPreset, string> = {
  deepseek: "DeepSeek",
  qwen: "阿里云百炼 / 千问",
  glm: "智谱 GLM",
  custom: "自定义模型服务",
};

export const FALLBACK_CATEGORIES: FallbackCategory[] = [
  "rate_limit",
  "timeout",
  "service",
  "invalid_output",
];

export function createEmptyProvider(): ProviderConfigInput {
  return {
    name: PROVIDER_NAMES.deepseek,
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
    tool_call_mode: "structured",
    input_price_per_million: 0,
    output_price_per_million: 0,
    fallback_on: ["rate_limit", "timeout", "service"],
  };
}

/** 编辑时只复制可写字段，API Key 固定留空以防密文或旧密钥回显。 */
export function providerToInput(
  provider: ProviderConfig,
): ProviderConfigInput {
  return {
    name: provider.name,
    preset: provider.preset,
    base_url: provider.base_url,
    model: provider.model,
    api_key: "",
    enabled: provider.enabled,
    is_default: provider.is_default,
    fallback_order: provider.fallback_order,
    timeout_seconds: provider.timeout_seconds,
    max_retries: provider.max_retries,
    structured_mode: provider.structured_mode,
    tool_call_mode: provider.tool_call_mode,
    input_price_per_million: provider.input_price_per_million,
    output_price_per_million: provider.output_price_per_million,
    fallback_on: provider.fallback_on,
  };
}

export function selectProviderPreset(
  form: ProviderConfigInput,
  preset: ProviderPreset,
  presets: Record<string, ProviderPresetDescriptor>,
): ProviderConfigInput {
  const descriptor = presets[preset];
  return {
    ...form,
    preset,
    name:
      !form.name || Object.values(PROVIDER_NAMES).includes(form.name)
        ? PROVIDER_NAMES[preset]
        : form.name,
    ...(descriptor
      ? { base_url: descriptor.base_url, model: descriptor.model }
      : {}),
  };
}

/** 将底层错误归纳为新手可以直接行动的原因，同时保留服务端的原始安全提示。 */
export function explainProviderFailure(message: string): string {
  const detail = message.replace(/^Error:\s*/, "");
  if (/鉴权|API Key|HTTP 40[13]/i.test(detail))
    return `API Key 错误：请确认密钥完整、有效且有模型调用权限。${detail}`;
  if (/超时|timeout/i.test(detail))
    return `请求超时：请检查网络，或在高级模式适当增大超时时间。${detail}`;
  if (/结构化|JSON|invalid.output/i.test(detail))
    return `服务不兼容结构化输出：请核对厂商预设，或在高级模式切换结构化模式。${detail}`;
  if (/不存在|HTTP 404|Base URL|网络请求|无法访问/i.test(detail))
    return `API 地址或模型错误：请核对 API 地址末尾是否为兼容入口，并确认模型名称可用。${detail}`;
  if (/限流|额度|HTTP 429/i.test(detail))
    return `服务额度或限流：请检查账户余额、调用配额，稍后再试。${detail}`;
  return `连接失败：请依次检查 API 地址、API Key 和模型名称。${detail}`;
}
