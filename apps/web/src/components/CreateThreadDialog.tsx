/** 新建任务工作区：用完整、连续的启动流程替代遮挡当前任务的居中弹窗。 */
import type { ProviderConfig, SecurityScenario } from "../types";

interface Props {
  title: string;
  prompt: string;
  scenario: SecurityScenario;
  providerConfigId: string;
  authorizedTarget: string;
  providers: ProviderConfig[];
  busy: boolean;
  onTitleChange: (value: string) => void;
  onPromptChange: (value: string) => void;
  onScenarioChange: (value: SecurityScenario) => void;
  onProviderChange: (value: string) => void;
  onAuthorizedTargetChange: (value: string) => void;
  onOpenSettings: () => void;
  onCancel: () => void;
  onSubmit: () => void;
}

const scenarios: Array<{
  id: SecurityScenario;
  label: string;
  description: string;
}> = [
  { id: "general", label: "通用研判", description: "从材料中识别风险并给出处置建议" },
  { id: "ctf", label: "CTF 题目", description: "解码、取证、分析与 Flag 验证" },
  { id: "incident_response", label: "应急响应", description: "日志时间线、IOC 提取与事件复盘" },
  { id: "vulnerability_analysis", label: "漏洞分析", description: "复现证据、影响判断与修复验证" },
  { id: "reverse_static", label: "静态逆向", description: "不执行样本，检查结构、导入和字符串" },
];

export default function CreateThreadDialog(props: Props) {
  const selectedProvider = props.providers.find(
    (provider) => provider.id === props.providerConfigId,
  );
  const selectedScenario = scenarios.find((scenario) => scenario.id === props.scenario);
  const canSubmit = Boolean(
    props.title.trim() &&
      props.prompt.trim() &&
      props.providerConfigId &&
      !props.busy,
  );

  return (
    <section className="task-launcher" aria-labelledby="task-launcher-title">
      <div className="task-launcher-main">
        <header className="task-launcher-intro">
          <span className="eyebrow">NEW SECURITY TASK</span>
          <h2 id="task-launcher-title">把目标说清楚，其余交给 Agent</h2>
          <p>先选择安全场景，再描述要分析的材料和期望结果。任务创建后会立即开始。</p>
        </header>

        <form
          className="task-launcher-form"
          onSubmit={(event) => {
            event.preventDefault();
            props.onSubmit();
          }}
        >
          <fieldset className="launcher-section">
            <legend><span>1</span>选择安全场景</legend>
            <div className="scenario-options">
              {scenarios.map((scenario) => (
                <button
                  key={scenario.id}
                  type="button"
                  className={props.scenario === scenario.id ? "selected" : ""}
                  aria-label={`${scenario.label} ${scenario.description}`}
                  aria-pressed={props.scenario === scenario.id}
                  onClick={() => props.onScenarioChange(scenario.id)}
                >
                  <strong>{scenario.label}</strong>
                </button>
              ))}
            </div>
            {selectedScenario && (
              <p className="selected-scenario-copy">{selectedScenario.description}</p>
            )}
          </fieldset>

          <fieldset className="launcher-section">
            <legend><span>2</span>描述任务</legend>
            <label className="launcher-title-field">
              <span>任务名称（可稍后修改）</span>
              <input
                aria-label="任务名称"
                value={props.title}
                onChange={(event) => props.onTitleChange(event.target.value)}
                placeholder="例如：分析这份 Web 访问日志"
              />
            </label>
            <label className="launcher-prompt-field">
              <span>你希望 Agent 完成什么？</span>
              <textarea
                aria-label="任务说明"
                value={props.prompt}
                onChange={(event) => props.onPromptChange(event.target.value)}
                placeholder="说明已获授权的分析目标、已有材料，以及你希望 Agent 交付什么结果。"
              />
            </label>
            <p className="launcher-hint">附件可在任务创建后继续添加。写清授权范围、已有材料和期望交付，Agent 会先规划再调用受控工具。</p>
          </fieldset>

        </form>
      </div>

      <aside className="task-launcher-rail" aria-label="运行前检查">
        <div>
          <span className="eyebrow">RUN CONFIGURATION</span>
          <h3>运行前检查</h3>
          <p>把关键配置放在开始按钮旁边，避免进入任务后才发现模型或权限不对。</p>
        </div>

        <label className="launcher-config-field">
          <span>本次使用的模型</span>
          {props.providers.length > 0 ? (
            <select
              aria-label="本次使用的模型"
              value={props.providerConfigId}
              onChange={(event) => props.onProviderChange(event.target.value)}
            >
              {props.providers.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.name} · {provider.actual_model ?? provider.model}
                </option>
              ))}
            </select>
          ) : (
            <button type="button" className="launcher-warning" onClick={props.onOpenSettings}>
              尚无可用模型，去设置中心配置
            </button>
          )}
          {selectedProvider && (
            <small>
              {selectedProvider.connection_status === "ok" ? "连接已验证" : "尚未完成连接测试"}
              {selectedProvider.is_default ? " · 默认 Provider" : ""}
            </small>
          )}
        </label>

        <label className="launcher-config-field">
          <span>本次授权目标（可选）</span>
          <input
            aria-label="本次授权目标"
            value={props.authorizedTarget}
            onChange={(event) => props.onAuthorizedTargetChange(event.target.value)}
            placeholder="域名、IP 或靶场地址"
          />
          <small>仅填写你拥有测试权限的目标；多个目标用逗号分隔。</small>
        </label>

        <section className="launcher-guardrails" aria-label="默认安全边界">
          <strong>默认安全边界</strong>
          <ul>
            <li>公网目标默认拒绝</li>
            <li>高风险动作需要确认</li>
            <li>工具调用与证据全程留痕</li>
          </ul>
        </section>

        <button
          type="button"
          className="launcher-settings-link"
          onClick={props.onOpenSettings}
        >
          管理模型、Skills 与 MCP
        </button>

      </aside>

      <div className="launcher-actions">
        <button type="button" onClick={props.onCancel}>返回当前任务</button>
        <button className="primary" type="button" disabled={!canSubmit} onClick={props.onSubmit}>
          {props.busy ? "正在启动…" : "创建并开始"}
        </button>
      </div>
    </section>
  );
}
