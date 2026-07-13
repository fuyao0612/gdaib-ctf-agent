/** 模型选择、规划策略、备用链和运行预算。 */
import type { AgentProfileInput, ProviderConfig } from "../../types";
import { BUDGET_FIELDS, changePlanningStrategy } from "./model";

interface Props {
  form: AgentProfileInput;
  providers: ProviderConfig[];
  onChange: (form: AgentProfileInput) => void;
}

export default function ProviderBudgetFields({
  form,
  providers,
  onChange,
}: Props) {
  const enabledProviders = providers.filter((provider) => provider.enabled);

  function moveFallback(index: number) {
    if (index === 0) return;
    const ids = [...form.fallback_provider_ids];
    [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]];
    onChange({ ...form, fallback_provider_ids: ids });
  }

  return (
    <fieldset>
      <legend>模型与预算</legend>
      <div className="form-grid">
        <label>
          默认 Provider
          <select
            value={form.default_provider_id ?? ""}
            onChange={(event) =>
              onChange({
                ...form,
                default_provider_id: event.target.value || null,
                fallback_provider_ids: event.target.value
                  ? form.fallback_provider_ids.filter(
                      (id) => id !== event.target.value,
                    )
                  : [],
              })
            }
          >
            <option value="">沿用平台默认</option>
            {enabledProviders.map((provider) => (
              <option value={provider.id} key={provider.id}>
                {provider.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          规划策略
          <select
            aria-label="规划策略"
            value={form.planning_strategy}
            onChange={(event) =>
              onChange(
                changePlanningStrategy(
                  form,
                  event.target
                    .value as AgentProfileInput["planning_strategy"],
                ),
              )
            }
          >
            <option value="dynamic">每次先规划</option>
            <option value="direct">直接回答或选择动作</option>
            <option value="hybrid">建议任务直答，复杂任务规划</option>
          </select>
        </label>
        <label className="wide">
          备用 Provider 链
          <select
            aria-label="添加备用 Provider"
            value=""
            disabled={!form.default_provider_id}
            onChange={(event) => {
              if (!event.target.value) return;
              onChange({
                ...form,
                fallback_provider_ids: [
                  ...form.fallback_provider_ids,
                  event.target.value,
                ],
              });
            }}
          >
            <option value="">选择后追加</option>
            {enabledProviders
              .filter(
                (provider) =>
                  provider.id !== form.default_provider_id &&
                  !form.fallback_provider_ids.includes(provider.id),
              )
              .map((provider) => (
                <option value={provider.id} key={provider.id}>
                  {provider.name}
                </option>
              ))}
          </select>
        </label>
        <div className="wide provider-chain">
          {form.fallback_provider_ids.map((id, index) => (
            <span key={id}>
              {index + 1}.{" "}
              {providers.find((provider) => provider.id === id)?.name ?? id}
              <button
                type="button"
                disabled={index === 0}
                onClick={() => moveFallback(index)}
              >
                上移
              </button>
              <button
                type="button"
                onClick={() =>
                  onChange({
                    ...form,
                    fallback_provider_ids: form.fallback_provider_ids.filter(
                      (value) => value !== id,
                    ),
                  })
                }
              >
                移除
              </button>
            </span>
          ))}
        </div>
        {BUDGET_FIELDS.map(({ key, label }) => (
          <label key={key}>
            {label}
            <input
              type="number"
              value={form.budget[key]}
              onChange={(event) =>
                onChange({
                  ...form,
                  budget: {
                    ...form.budget,
                    [key]: Number(event.target.value),
                  },
                })
              }
            />
          </label>
        ))}
      </div>
    </fieldset>
  );
}
