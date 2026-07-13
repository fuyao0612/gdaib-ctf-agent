import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { api } from './api'
import SettingsCenter from './SettingsCenter'
import type { AgentProfileSummary, Artifact, Event, MemoryRecord, Mode, ProviderConfig, Report, Run, RunAudit, Thread, ThreadDetail } from './types'
import './styles.css'

const terminal = new Set(['completed', 'failed', 'stopped'])

function Badge({ status }: { status: string }) { return <span className={`badge badge-${status}`}>{status}</span> }

function EventCard({ event }: { event: Event }) {
  const tool = event.type.startsWith('tool_')
  return <article className={`event-card ${tool ? 'tool-event' : ''}`} data-testid={`event-${event.type}`}>
    <div className="event-head"><span className="event-sequence">#{event.sequence}</span><strong>{event.type.replaceAll('_', ' ')}</strong><time>{new Date(event.timestamp).toLocaleTimeString()}</time></div>
    <p>{event.summary}</p>
    {tool && <details><summary>工具审计详情</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>}
  </article>
}

export default function App() {
  const [threads, setThreads] = useState<Thread[]>([])
  const [detail, setDetail] = useState<ThreadDetail | null>(null)
  const [events, setEvents] = useState<Event[]>([])
  const [activeRun, setActiveRun] = useState<Run | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [message, setMessage] = useState('')
  const [providers, setProviders] = useState<ProviderConfig[]>([])
  const [agentProfiles, setAgentProfiles] = useState<AgentProfileSummary[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState('')
  const [selectedProviderId, setSelectedProviderId] = useState('')
  const [successPattern, setSuccessPattern] = useState('')
  const [pendingArtifacts, setPendingArtifacts] = useState<Artifact[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [newTitle, setNewTitle] = useState('新的安全任务')
  const [newMode, setNewMode] = useState<Mode>('normal')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [initialSetup, setInitialSetup] = useState(false)
  const [audit, setAudit] = useState<RunAudit | null>(null)
  const [supplementalInput, setSupplementalInput] = useState('')
  const [memories, setMemories] = useState<MemoryRecord[]>([])
  const sourceRef = useRef<EventSource | null>(null)

  const loadThreads = useCallback(async () => setThreads(await api.listThreads()), [])
  const loadProviders = useCallback(async () => {
    const values = await api.listProviders(); setProviders(values)
    setSelectedProviderId(current => current && values.some(value => value.id === current) ? current : (values.find(value => value.is_default)?.id ?? values[0]?.id ?? ''))
  }, [])
  const loadProfiles = useCallback(async () => {
    const values = await api.listAgentProfiles(); setAgentProfiles(values)
    setSelectedProfileId(current => current && values.some(value => value.profile_id === current) ? current : (values.find(value => value.is_default)?.profile_id ?? values[0]?.profile_id ?? ''))
  }, [])
  const refreshSettings = useCallback(async () => { await Promise.all([loadProviders(), loadProfiles()]) }, [loadProviders, loadProfiles])
  const selectThread = useCallback(async (id: string) => {
    sourceRef.current?.close(); setError(''); setReport(null)
    const value = await api.detail(id); setDetail(value); setPendingArtifacts([]); setMemories(await api.memories(id))
    const run = value.runs.at(-1) ?? null; setActiveRun(run)
    if (run) { setEvents(await api.events(run.id)); setAudit(await api.audit(run.id)); if (run.status === 'completed') setReport(await api.report(run.id)) }
    else { setEvents([]); setAudit(null) }
  }, [])

  useEffect(() => {
    void Promise.all([loadThreads(), loadProviders(), loadProfiles()])
    void api.setupStatus().then(status => {
      if (!status.configured) { setInitialSetup(true); setSettingsOpen(true) }
    }).catch(() => undefined)
  }, [loadThreads, loadProviders, loadProfiles])

  const connect = useCallback((run: Run) => {
    sourceRef.current?.close()
    const source = new EventSource(`/api/v1/runs/${run.id}/events/stream`); sourceRef.current = source
    source.onmessage = (messageEvent) => {
      const event = JSON.parse(messageEvent.data) as Event
      setEvents(previous => previous.some(item => item.sequence === event.sequence) ? previous : [...previous, event])
      void api.audit(run.id).then(setAudit)
      if (event.type === 'run_waiting_input') {
        void api.detail(run.thread_id).then(value => {
          setDetail(value)
          setActiveRun(value.runs.find(item => item.id === run.id) ?? run)
        })
      }
      if (terminal.has(event.type.replace('run_', ''))) {
        source.close()
        void api.detail(run.thread_id).then(value => { const latest = value.runs.find(item => item.id === run.id) ?? run; setDetail(value); setActiveRun(latest); void loadThreads(); void api.memories(run.thread_id).then(setMemories); if (event.type === 'run_completed') void api.report(run.id).then(setReport) })
      }
    }
    source.onerror = () => { if (source.readyState === EventSource.CLOSED) source.close() }
  }, [loadThreads])

  useEffect(() => () => sourceRef.current?.close(), [])

  async function createThread() {
    setBusy(true); setError('')
    try { const value = await api.createThread(newTitle, newMode, selectedProfileId); await loadThreads(); await selectThread(value.id); setCreateOpen(false) } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  async function upload(file?: File) {
    if (!detail || !file) return
    setBusy(true); try { const artifact = await api.upload(detail.id, file); setPendingArtifacts(items => [...items, artifact]) } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  async function sendAndRun() {
    if (!detail || !message.trim() || !selectedProviderId || (needsEvidencePattern && !successPattern.trim())) return
    setBusy(true); setError(''); setEvents([]); setReport(null)
    try {
      const run = await api.turn(detail.id, message, pendingArtifacts.map(item => item.id), selectedProviderId, successPattern); setActiveRun(run); setMessage(''); setPendingArtifacts([]); connect(run)
      const updated = await api.detail(detail.id); setDetail(updated)
    } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  async function stop() { if (activeRun) { await api.stop(activeRun.id); setActiveRun({ ...activeRun, stop_requested: true }) } }
  async function retry() { if (activeRun) { const run = await api.retry(activeRun.id); setEvents([]); setReport(null); setActiveRun(run); connect(run) } }
  async function submitSupplement() { if (!activeRun || !supplementalInput.trim()) return; const run = await api.submitInput(activeRun.id, supplementalInput); setSupplementalInput(''); setActiveRun(run); connect(run) }
  async function toggleMemory(enabled: boolean) { if (!detail) return; await api.toggleMemories(detail.id, enabled); setMemories(await api.memories(detail.id)) }
  async function removeMemory(id: string) { if (!detail) return; await api.deleteMemory(detail.id, id); setMemories(await api.memories(detail.id)) }
  async function clearMemory() { if (!detail) return; await api.clearMemories(detail.id); setMemories([]) }

  const running = activeRun?.status === 'queued' || activeRun?.status === 'running'
  const inputLocked = detail?.mode === 'competition' && running
  const currentProfile = agentProfiles.find(value => value.profile_id === detail?.agent_profile_id)
  const needsEvidencePattern = currentProfile?.completion_mode === 'evidence'
  const metrics = useMemo(() => ({ tools: events.filter(item => item.type === 'tool_finished').length, replans: events.filter(item => item.type === 'replanned').length, events: events.length }), [events])

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">御</span><div><h1>御网智元</h1><p>安全 Agent 工作台</p></div></div>
      <button className="primary full" onClick={() => setCreateOpen(true)}>＋ 新建任务</button>
      <button className="settings-button full" onClick={() => setSettingsOpen(true)}>⚙ 设置中心</button>
      <div className="section-label">任务线程</div>
      <nav className="thread-list">{threads.filter(item => !item.archived).map(thread => <button key={thread.id} className={`thread-item ${detail?.id === thread.id ? 'selected' : ''}`} onClick={() => void selectThread(thread.id)}><span>{thread.title}</span><small>{thread.mode}</small></button>)}</nav>
      <div className="security-note"><span>●</span><div><strong>安全边界已启用</strong><p>公网默认拒绝 · 凭据自动脱敏</p></div></div>
    </aside>

    <main className="workspace">
      <header className="topbar"><div><span className="eyebrow">THREAD</span><h2>{detail?.title ?? '选择或创建一个任务'}</h2>{detail && <small>{currentProfile?.name ?? '历史 Agent 配置'} · v{detail.agent_profile_version ?? '?'} · {currentProfile?.completion_mode ?? activeRun?.completion_mode}</small>}</div>{detail && <div className="top-meta"><span className="mode">{detail.mode}</span>{activeRun && <><span className="mode">{activeRun.provider}</span><span className="mode">{activeRun.evidence_level}</span><Badge status={activeRun.status} /></>}</div>}</header>
      {!detail ? <section className="empty"><div className="radar">⌁</div><h2>从一个可审计的任务开始</h2><p>创建对话，上传安全样本，并实时观察 Agent 的结构化计划、工具证据和策略判断。</p><button className="primary" onClick={() => setCreateOpen(true)}>创建第一个任务</button></section> : <>
        <section className="conversation" aria-label="对话时间线">
          {detail.messages.map(item => <div key={item.id} className={`message ${item.role}`}><span className="avatar">{item.role === 'user' ? '你' : '智'}</span><div><div className="message-meta">{item.role === 'user' ? '用户任务' : 'Agent'} · {new Date(item.created_at).toLocaleTimeString()}</div><p>{item.content}</p></div></div>)}
          {events.length > 0 && <div className="agent-progress"><div className="progress-title"><span className="pulse" />Agent 执行记录 <span>{events.length} 项</span></div>{events.filter(item => !item.type.startsWith('tool_')).map(event => <EventCard key={event.event_id} event={event} />)}</div>}
          {report && <section className="report" data-testid="final-report"><div className="report-header"><h3>最终报告</h3><div><a href={api.reportUrl(activeRun!.id, 'md')}>Markdown</a><a href={api.reportUrl(activeRun!.id, 'json')}>JSON</a></div></div><ReactMarkdown>{report.markdown}</ReactMarkdown></section>}
        </section>
        <footer className="composer">
          {activeRun?.status === 'waiting_input' ? <section className="input-request" data-testid="waiting-input"><strong>Agent 正在等待补充信息</strong><p>{events.filter(item => item.type === 'run_waiting_input').at(-1)?.summary}</p><textarea aria-label="补充信息" value={supplementalInput} onChange={event => setSupplementalInput(event.target.value)} placeholder="补充必要事实；内容仍按不可信输入处理" /><button className="primary" disabled={!supplementalInput.trim()} onClick={() => void submitSupplement()}>提交并继续</button></section> : <>
            <div className="attachments">{pendingArtifacts.map(file => <span key={file.id}>📎 {file.filename} · {file.size} B</span>)}</div>
            {inputLocked && <div className="lock-note">竞赛模式运行中：已锁定补充提示，仅可观察或停止。</div>}
            {providers.length === 0 && <div className="model-required">需要配置模型：请先进入设置中心添加、测试并启用 Provider。</div>}
            <textarea aria-label="任务消息" value={message} onChange={event => setMessage(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void sendAndRun() } }} disabled={inputLocked || busy} placeholder="描述任务、期望输出与必要上下文…" />
            {needsEvidencePattern ? <div className="verification-row"><label>成功答案正则<input aria-label="成功答案正则" value={successPattern} onChange={event => setSuccessPattern(event.target.value)} placeholder="例如 FLAG\{[A-Za-z0-9_-]+\}" disabled={running} /></label><small>证据验证模式：候选必须绑定外部证据并通过此规则。</small></div> : <div className="trust-level">{currentProfile?.completion_mode === 'advisory' ? '建议回答：模型生成，未经外部验证' : '结构化输出：按 Agent 配置的 JSON Schema 验证'}</div>}
            <div className="composer-actions"><label className="file-button">＋ 附件<input aria-label="上传附件" type="file" accept=".txt,.json,.md,.log,.bin" onChange={event => void upload(event.target.files?.[0])} /></label><label className="provider-select">模型<select aria-label="运行模型" value={selectedProviderId} onChange={event => setSelectedProviderId(event.target.value)} disabled={running || providers.length === 0}><option value="">未配置</option>{providers.map(value => <option key={value.id} value={value.id}>{value.name} · {value.model}</option>)}</select></label><span className="authorization">Enter 发送 · Shift+Enter 换行</span><div className="run-actions">{running ? <button className="danger" onClick={() => void stop()}>停止</button> : activeRun && ['failed', 'stopped'].includes(activeRun.status) ? <button onClick={() => void retry()}>重试</button> : null}<button className="primary" disabled={busy || inputLocked || running || !message.trim() || !selectedProviderId || (needsEvidencePattern && !successPattern.trim())} onClick={() => void sendAndRun()}>{busy ? '正在发送…' : '发送'}</button></div></div>
          </>}
        </footer>
      </>}
      {error && <div role="alert" className="toast">{error}</div>}
    </main>

    <aside className="inspector">
      <div className="inspector-head"><span>运行审计</span><small>LIVE</small></div>
      <div className="metrics"><div><strong>{metrics.events}</strong><span>事件</span></div><div><strong>{metrics.tools}</strong><span>工具调用</span></div><div><strong>{metrics.replans}</strong><span>重规划</span></div></div>
      {audit && <section className="budget-audit" data-testid="budget-audit"><div className="section-label">配置与剩余预算</div><p>{audit.profile?.name} · v{audit.profile?.version} · {audit.profile?.completion_mode}</p><p>策略：{audit.profile?.planning_strategy} · 工作流：{audit.profile?.workflow_preset}</p><p>Provider：{audit.run.provider}</p><p>验证：{audit.run.validation_status} · 证据：{audit.run.evidence_level}</p><dl><dt>步骤</dt><dd>{audit.usage.steps ?? 0} / {audit.limits.max_steps ?? '-'}</dd><dt>模型调用</dt><dd>{audit.usage.model_calls ?? 0} / {audit.limits.max_model_calls ?? '-'}</dd><dt>Token</dt><dd>{audit.usage.tokens ?? 0} / {audit.limits.max_tokens ?? '-'}</dd><dt>上下文</dt><dd>{audit.usage.context_tokens ?? 0} Token</dd><dt>观察</dt><dd>{audit.usage.observation_chars ?? 0} 字符</dd><dt>裁剪</dt><dd>{audit.usage.context_truncations ?? 0} 次</dd></dl></section>}
      <div className="section-label">工具与证据</div><div className="tool-list">{events.filter(item => item.type.startsWith('tool_')).map(event => <EventCard key={event.event_id} event={event} />)}{!events.some(item => item.type.startsWith('tool_')) && <p className="muted">运行后将在此展示折叠的工具审计卡片。</p>}</div>
      <div className="section-label">附件</div>{detail?.artifacts.map(item => <a className="artifact" key={item.id} href={api.artifactUrl(item.id)}><span>TXT</span><div><strong>{item.filename}</strong><small>{item.sha256.slice(0, 12)}… · {item.size} B</small></div></a>)}
      {detail && <section className="memory-panel"><div className="section-label">对话记忆</div><div className="memory-controls"><button onClick={() => void toggleMemory(true)}>启用</button><button onClick={() => void toggleMemory(false)}>停用</button><button className="danger" onClick={() => void clearMemory()}>全部清除</button></div><div className="memory-list">{memories.map(item => <article key={item.id} className={item.enabled ? '' : 'disabled'}><strong>{item.kind}</strong><p>{item.content}</p><button aria-label={`删除记忆 ${item.content.slice(0, 20)}`} onClick={() => void removeMemory(item.id)}>删除</button></article>)}{memories.length === 0 && <p className="muted">暂无已保存记忆。</p>}</div></section>}
    </aside>

    {createOpen && <div className="modal-backdrop"><form className="modal" onSubmit={event => { event.preventDefault(); void createThread() }}><h2>创建 Agent 对话</h2><label>任务名称<input aria-label="任务名称" value={newTitle} onChange={event => setNewTitle(event.target.value)} /></label><label>Agent 配置<select aria-label="Agent 配置" value={selectedProfileId} onChange={event => { const value = agentProfiles.find(item => item.profile_id === event.target.value); setSelectedProfileId(event.target.value); if (value) setNewMode(value.run_mode) }}><option value="">请选择</option>{agentProfiles.map(value => <option key={value.profile_id} value={value.profile_id}>{value.name} · v{value.version} · {value.completion_mode}</option>)}</select></label><label>运行模式<select aria-label="运行模式" value={newMode} onChange={event => setNewMode(event.target.value as Mode)}><option value="normal">normal · 可继续交流</option><option value="competition">competition · 运行中锁定输入</option></select></label><p>Thread 将绑定当前 Agent 配置版本；后续编辑不会改变历史运行。</p><div><button type="button" onClick={() => setCreateOpen(false)}>取消</button><button className="primary" type="submit" disabled={busy || !selectedProfileId}>创建</button></div></form></div>}
    {settingsOpen && <SettingsCenter initialSetup={initialSetup} onClose={() => setSettingsOpen(false)} onChanged={async () => { await refreshSettings(); const status = await api.setupStatus(); setInitialSetup(!status.configured) }} />}
  </div>
}
