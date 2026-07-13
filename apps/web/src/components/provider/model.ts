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

export const FALLBACK_CATEGORIES: FallbackCategory[] = [
  "rate_limit",
  "timeout",
  "service",
  "invalid_output",
];

export function createEmptyProvider(): ProviderConfigInput {
  return {
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
    ...(descriptor
      ? { base_url: descriptor.base_url, model: descriptor.model }
      : {}),
  };
}
