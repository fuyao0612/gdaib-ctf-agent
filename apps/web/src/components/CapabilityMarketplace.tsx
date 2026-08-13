/** 本地策展的能力广场：Skill 可一键安装，MCP 只生成安全配置草稿。 */
import { useMemo, useState } from "react";
import {
  Binary,
  Blocks,
  Bug,
  Cable,
  Check,
  Download,
  Flag,
  Globe2,
  Search,
  Settings2,
  ShieldAlert,
  TerminalSquare,
  type LucideIcon,
} from "lucide-react";

import { api } from "../api";
import type { McpServerInput, SkillDefinition, SkillInput } from "../types";

export interface McpMarketplaceTemplate {
  id: string;
  title: string;
  description: string;
  tags: string[];
  input: McpServerInput;
}

interface SkillMarketplaceTemplate extends SkillInput {
  id: string;
  category: string;
  tags: string[];
}

interface Props {
  csrf: string;
  skills: SkillDefinition[];
  onSkillsChanged: () => Promise<void>;
  onConfigureMcp: (template: McpMarketplaceTemplate) => void;
  onNotice: (value: string) => void;
  onError: (value: string) => void;
}

const SKILL_CATALOG: SkillMarketplaceTemplate[] = [
  {
    id: "ctf-evidence",
    category: "CTF",
    tags: ["CTF", "证据", "Flag"],
    name: "CTF 证据化解题",
    description: "从附件检查开始，保留中间产物并验证 Flag 候选。",
    prompt: "仅分析用户明确提供或授权的 CTF 材料。先识别题型和附件，再制定最小步骤；每个结论引用证据，Flag 必须经过格式和来源验证。",
    steps: ["检查附件与题目约束", "选择低风险分析工具", "保存关键中间产物", "验证 Flag 候选"],
    checklist: ["未扩大授权目标", "关键结论有证据", "Flag 已验证"],
    enabled: true,
  },
  {
    id: "incident-timeline",
    category: "应急响应",
    tags: ["日志", "IOC", "时间线"],
    name: "应急响应时间线",
    description: "归一化日志时间，提取 IOC，并区分事实、假设与处置建议。",
    prompt: "针对已授权的日志和取证材料建立时间线。保留原始证据，明确时区与数据缺口；区分事实和推测，任何隔离、删除或封禁操作都必须再次获得授权。",
    steps: ["确认日志来源与时区", "建立事件时间线", "提取并关联 IOC", "给出处置与补充采集建议"],
    checklist: ["原始证据未被修改", "事实与假设已区分", "处置动作未越权"],
    enabled: true,
  },
  {
    id: "web-vulnerability",
    category: "漏洞分析",
    tags: ["Web", "CWE", "修复验证"],
    name: "Web 漏洞研判",
    description: "复现前确认授权范围，输出漏洞证据、风险和可验证修复建议。",
    prompt: "仅在用户明确授权的目标范围内分析 Web 漏洞。优先采用非破坏性验证，记录请求、响应和前置条件，并给出可回归验证的修复建议。",
    steps: ["确认目标和授权边界", "识别可疑输入与数据流", "执行最小化验证", "映射 CWE 并给出修复回归项"],
    checklist: ["目标在授权范围内", "未执行破坏性利用", "修复建议可验证"],
    enabled: true,
  },
  {
    id: "static-malware",
    category: "静态逆向",
    tags: ["PE", "ELF", "恶意样本"],
    name: "可疑样本静态分析",
    description: "不执行样本，先从元数据、字符串、导入表和结构异常建立证据。",
    prompt: "只对用户提供的样本进行静态分析，不执行未知二进制。优先提取哈希、格式、段信息、导入表和字符串，并把恶意性判断与证据对应。",
    steps: ["计算哈希并识别文件格式", "检查结构和导入表", "提取高价值字符串", "汇总 IOC 与不确定性"],
    checklist: ["样本未被执行", "IOC 有明确来源", "结论包含不确定性"],
    enabled: true,
  },
];

const MCP_CATALOG: McpMarketplaceTemplate[] = [
  {
    id: "remote-http",
    title: "远程 HTTPS MCP",
    description: "连接已经部署好的 Streamable HTTP 服务，适合云端代码、情报或资产查询能力。",
    tags: ["远程", "HTTPS", "需服务地址"],
    input: {
      name: "远程安全工具 MCP",
      transport: "streamable_http",
      command: null,
      args: [],
      url: "",
      enabled: true,
      connect_timeout_seconds: 10,
      call_timeout_seconds: 30,
      allowed_tools: [],
      blocked_tools: [],
    },
  },
  {
    id: "local-stdio",
    title: "本地受控 Stdio MCP",
    description: "从部署管理员允许的程序中选择，适合本地取证或代码分析服务。页面不会接受任意 Shell。",
    tags: ["本地", "白名单", "受控程序"],
    input: {
      name: "本地分析 MCP",
      transport: "stdio",
      command: null,
      args: [],
      url: null,
      enabled: true,
      connect_timeout_seconds: 10,
      call_timeout_seconds: 30,
      allowed_tools: [],
      blocked_tools: [],
    },
  },
  {
    id: "remote-readonly",
    title: "远程只读情报 MCP",
    description: "为威胁情报、漏洞数据库等只读查询服务建立草稿，保存前可填写令牌和工具白名单。",
    tags: ["情报", "只读", "工具白名单"],
    input: {
      name: "只读情报 MCP",
      transport: "streamable_http",
      command: null,
      args: [],
      url: "",
      enabled: true,
      connect_timeout_seconds: 10,
      call_timeout_seconds: 30,
      allowed_tools: [],
      blocked_tools: [],
    },
  },
];

const SKILL_ICONS: Record<string, LucideIcon> = {
  "ctf-evidence": Flag,
  "incident-timeline": ShieldAlert,
  "web-vulnerability": Bug,
  "static-malware": Binary,
};

const MCP_ICONS: Record<string, LucideIcon> = {
  "remote-http": Globe2,
  "local-stdio": TerminalSquare,
  "remote-readonly": Cable,
};

export default function CapabilityMarketplace(props: Props) {
  const [tab, setTab] = useState<"skills" | "mcp">("skills");
  const [query, setQuery] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const installedNames = useMemo(() => new Set(props.skills.map((skill) => skill.name)), [props.skills]);
  const normalizedQuery = query.trim().toLowerCase();
  const skillItems = SKILL_CATALOG.filter((item) =>
    [item.name, item.description, item.category, ...item.tags].join(" ").toLowerCase().includes(normalizedQuery),
  );
  const mcpItems = MCP_CATALOG.filter((item) =>
    [item.title, item.description, ...item.tags].join(" ").toLowerCase().includes(normalizedQuery),
  );

  async function installSkill(template: SkillMarketplaceTemplate) {
    setBusyId(template.id);
    props.onError("");
    try {
      const input: SkillInput = {
        name: template.name,
        description: template.description,
        prompt: template.prompt,
        steps: template.steps,
        checklist: template.checklist,
        enabled: template.enabled,
      };
      await api.createSkill(props.csrf, input);
      await props.onSkillsChanged();
      props.onNotice(`Skill“${template.name}”已安装并启用，可在任务中直接选择。`);
    } catch (cause) {
      props.onError(String(cause));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="capability-marketplace">
      <div className="marketplace-hero">
        <div>
          <span className="eyebrow">安全能力目录</span>
          <h3>选择适合任务的能力</h3>
          <p>先选择能力，再进入任务。Skill 可以一键安装；MCP 会打开受控配置草稿，不会静默连接外部服务。</p>
        </div>
        <label className="marketplace-search">
          <span className="sr-only">搜索能力</span>
          <Search size={16} aria-hidden="true" />
          <input aria-label="搜索能力" placeholder="搜索 CTF、日志、MCP…" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
      </div>
      <div className="marketplace-tabs" role="tablist" aria-label="能力类型">
        <button role="tab" aria-selected={tab === "skills"} className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}><Blocks size={16} aria-hidden="true" />Skills 模板</button>
        <button role="tab" aria-selected={tab === "mcp"} className={tab === "mcp" ? "active" : ""} onClick={() => setTab("mcp")}><Cable size={16} aria-hidden="true" />MCP 接入模板</button>
      </div>
      <div className="marketplace-grid">
        {tab === "skills" && skillItems.map((item) => {
          const installed = installedNames.has(item.name);
          const SkillIcon = SKILL_ICONS[item.id] ?? Blocks;
          return <article className="marketplace-card" key={item.id}>
            <div className="marketplace-card-top"><span><SkillIcon size={16} aria-hidden="true" />{item.category}</span><small>本地声明式 Skill</small></div>
            <h4>{item.name}</h4>
            <p>{item.description}</p>
            <div className="marketplace-tags">{item.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
            <button className={installed ? "" : "primary"} disabled={installed || busyId === item.id} onClick={() => void installSkill(item)}>
              {installed ? <Check size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
              {installed ? "已安装" : busyId === item.id ? "正在安装…" : "一键安装"}
            </button>
          </article>;
        })}
        {tab === "mcp" && mcpItems.map((item) => {
          const McpIcon = MCP_ICONS[item.id] ?? Cable;
          return <article className="marketplace-card" key={item.id}>
            <div className="marketplace-card-top"><span><McpIcon size={16} aria-hidden="true" />MCP</span><small>{item.input.transport === "stdio" ? "Stdio" : "Streamable HTTP"}</small></div>
            <h4>{item.title}</h4>
            <p>{item.description}</p>
            <div className="marketplace-tags">{item.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
            <button className="primary" onClick={() => props.onConfigureMcp(item)}><Settings2 size={16} aria-hidden="true" />开始配置</button>
          </article>;
        })}
        {((tab === "skills" && !skillItems.length) || (tab === "mcp" && !mcpItems.length)) && <p className="marketplace-empty">没有匹配的能力，换个关键词试试。</p>}
      </div>
    </section>
  );
}
