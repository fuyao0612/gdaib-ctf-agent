import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type {
  KnowledgeDocument,
  KnowledgeHit,
  SecurityScenario,
} from "../types";

interface Props {
  csrf: string;
  onNotice: (value: string) => void;
  onError: (value: string) => void;
}

const scenarioOptions: Array<{ value: SecurityScenario; label: string }> = [
  { value: "general", label: "通用安全" },
  { value: "ctf", label: "CTF" },
  { value: "incident_response", label: "应急响应" },
  { value: "vulnerability_analysis", label: "漏洞分析" },
  { value: "reverse_static", label: "静态逆向" },
];

export default function KnowledgeBaseSettings(props: Props) {
  const { csrf, onError, onNotice } = props;
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [scenario, setScenario] = useState<SecurityScenario>("general");
  const [allowProviderContext, setAllowProviderContext] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setDocuments(await api.knowledgeDocuments(csrf));
  }, [csrf]);

  useEffect(() => {
    void load().catch((cause) => onError(String(cause)));
  }, [load, onError]);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !content.trim()) return;
    setBusy(true);
    onError("");
    try {
      await api.createKnowledgeDocument(csrf, {
        title: title.trim(),
        content: content.trim(),
        source_uri: null,
        tags: tags.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
        scenarios: scenario === "general" ? [] : [scenario],
        enabled: true,
        allow_provider_context: allowProviderContext,
      });
      setTitle("");
      setContent("");
      setTags("");
      setAllowProviderContext(false);
      await load();
      onNotice("知识文档已切分并建立本地检索索引");
    } catch (cause) {
      onError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function update(
    document: KnowledgeDocument,
    value: { enabled?: boolean; allow_provider_context?: boolean },
  ) {
    try {
      await api.updateKnowledgeDocument(csrf, document.id, value);
      await load();
    } catch (cause) {
      onError(String(cause));
    }
  }

  async function remove(document: KnowledgeDocument) {
    try {
      await api.deleteKnowledgeDocument(csrf, document.id);
      await load();
      onNotice("知识文档已删除");
    } catch (cause) {
      onError(String(cause));
    }
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    try {
      setHits(await api.searchKnowledge(csrf, query.trim(), scenario));
    } catch (cause) {
      onError(String(cause));
    }
  }

  return (
    <section className="knowledge-settings">
      <div className="settings-title">
        <div>
          <h3>本地安全知识库（RAG）</h3>
          <p className="muted">
            文档在本机 SQLite 中切分和检索；命中片段会随 Run 固化并显示来源哈希。
          </p>
        </div>
      </div>
      <p className="knowledge-warning">
        知识内容始终是不可信资料。只有开启“允许进入模型上下文”后，命中片段才可能发送给当前 Provider。
      </p>

      <form className="settings-form knowledge-import" onSubmit={create}>
        <div className="form-grid">
          <label>
            文档标题
            <input value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label>
            适用场景
            <select value={scenario} onChange={(event) => setScenario(event.target.value as SecurityScenario)}>
              {scenarioOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label className="wide">
            标签（逗号分隔）
            <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="例如：OWASP, SQL 注入, 修复" />
          </label>
          <label className="wide">
            文档正文
            <textarea rows={7} value={content} onChange={(event) => setContent(event.target.value)} placeholder="粘贴你有权使用的安全规范、比赛笔记或内部处置手册。" />
          </label>
        </div>
        <label className="toggle-line">
          <input type="checkbox" checked={allowProviderContext} onChange={(event) => setAllowProviderContext(event.target.checked)} />
          允许命中片段进入模型上下文
        </label>
        <button className="primary" disabled={busy || !title.trim() || !content.trim()}>
          导入并建立索引
        </button>
      </form>

      <div className="knowledge-list">
        {documents.map((document) => (
          <article key={document.id}>
            <div>
              <strong>{document.title}</strong>
              <small>{document.origin === "builtin" ? "内置基线" : "用户文档"} · {document.chunk_count} 个片段 · {document.sha256.slice(0, 12)}…</small>
              <small>{document.scenarios.length ? document.scenarios.join("、") : "所有场景"} · {document.tags.join("、") || "无标签"}</small>
            </div>
            <div className="knowledge-actions">
              <label><input type="checkbox" checked={document.enabled} onChange={(event) => void update(document, { enabled: event.target.checked })} />启用</label>
              <label><input type="checkbox" checked={document.allow_provider_context} onChange={(event) => void update(document, { allow_provider_context: event.target.checked })} />进入模型</label>
              {document.origin === "user" && <button className="danger" type="button" onClick={() => void remove(document)}>删除</button>}
            </div>
          </article>
        ))}
      </div>

      <form className="knowledge-search" onSubmit={search}>
        <label>
          检索预览
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：发现多次登录失败后应该如何建立时间线" />
        </label>
        <button type="submit" disabled={!query.trim()}>测试检索</button>
      </form>
      {hits.length > 0 && <div className="knowledge-hits">
        {hits.map((hit) => <article key={hit.chunk_id}>
          <strong>{hit.title} · 片段 {hit.chunk_ordinal}</strong>
          <small>相关度 {hit.score.toFixed(2)} · {hit.content_sha256.slice(0, 12)}…</small>
          <p>{hit.content}</p>
        </article>)}
      </div>}
    </section>
  );
}
