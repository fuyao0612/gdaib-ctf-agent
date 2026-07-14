/** 用公开就绪状态展示首次配置进度，不在浏览器复制一套配置判断。 */
import type { SetupStatus } from "../types";

interface Props {
  authenticated: boolean;
  status: SetupStatus | null;
}

export default function SetupProgress({ authenticated, status }: Props) {
  const steps = [
    { label: "管理员会话", ready: authenticated },
    { label: "Provider 已连接", ready: status?.checks.provider ?? false },
    { label: "默认 Agent 可用", ready: status?.checks.agent ?? false },
    { label: "可以开始对话", ready: status?.configured ?? false },
  ];

  return (
    <div className="setup-progress" aria-label="配置进度">
      <strong>配置进度</strong>
      <ol>
        {steps.map((step, index) => (
          <li className={step.ready ? "ready" : "pending"} key={step.label}>
            <span aria-hidden="true">{step.ready ? "✓" : index + 1}</span>
            {step.label}
          </li>
        ))}
      </ol>
      <small>
        {status?.configured
          ? "准备完成。关闭设置后即可创建对话。"
          : "按顺序完成即可；所有状态均来自服务端真实配置。"}
      </small>
    </div>
  );
}
