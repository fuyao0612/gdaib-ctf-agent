import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { api } from './api'
import SettingsCenter from './SettingsCenter'
import type { Artifact, Event, Mode, ProviderConfig, Report, Run, Thread, ThreadDetail } from './types'
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
  const [selectedProviderId, setSelectedProviderId] = useState('')
  const [pendingArtifacts, setPendingArtifacts] = useState<Artifact[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [newTitle, setNewTitle] = useState('新的安全任务')
  const [newMode, setNewMode] = useState<Mode>('normal')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)

  const loadThreads = useCallback(async () => setThreads(await api.listThreads()), [])
  const loadProviders = useCallback(async () => {
    const values = await api.listProviders(); setProviders(values)
    setSelectedProviderId(current => current && values.some(value => value.id === current) ? current : (values.find(value => value.is_default)?.id ?? values[0]?.id ?? ''))
  }, [])
  const selectThread = useCallback(async (id: string) => {
    sourceRef.current?.close(); setError(''); setReport(null)
    const value = await api.detail(id); setDetail(value); setPendingArtifacts([])
    const run = value.runs.at(-1) ?? null; setActiveRun(run)
    if (run) { setEvents(await api.events(run.id)); if (run.status === 'completed') setReport(await api.report(run.id)) }
    else setEvents([])
  }, [])

  useEffect(() => { void Promise.all([loadThreads(), loadProviders()]) }, [loadThreads, loadProviders])

  const connect = useCallback((run: Run) => {
    sourceRef.current?.close()
    const source = new EventSource(`/api/v1/runs/${run.id}/events/stream`); sourceRef.current = source
    source.onmessage = (messageEvent) => {
      const event = JSON.parse(messageEvent.data) as Event
      setEvents(previous => previous.some(item => item.sequence === event.sequence) ? previous : [...previous, event])
      if (terminal.has(event.type.replace('run_', ''))) {
        source.close()
        void api.detail(run.thread_id).then(value => { const latest = value.runs.find(item => item.id === run.id) ?? run; setDetail(value); setActiveRun(latest); void loadThreads(); if (event.type === 'run_completed') void api.report(run.id).then(setReport) })
      }
    }
    source.onerror = () => { if (source.readyState === EventSource.CLOSED) source.close() }
  }, [loadThreads])

  useEffect(() => () => sourceRef.current?.close(), [])

  async function createThread() {
    setBusy(true); setError('')
    try { const value = await api.createThread(newTitle, newMode); await loadThreads(); await selectThread(value.id); setCreateOpen(false) } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  async function upload(file?: File) {
    if (!detail || !file) return
    setBusy(true); try { const artifact = await api.upload(detail.id, file); setPendingArtifacts(items => [...items, artifact]) } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  async function sendAndRun() {
    if (!detail || !message.trim() || !selectedProviderId) return
    setBusy(true); setError(''); setEvents([]); setReport(null)
    try {
      await api.message(detail.id, message, pendingArtifacts.map(item => item.id))
      const run = await api.start(detail.id, selectedProviderId); setActiveRun(run); setPendingArtifacts([]); connect(run)
      const updated = await api.detail(detail.id); setDetail(updated)
    } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  async function stop() { if (activeRun) { await api.stop(activeRun.id); setActiveRun({ ...activeRun, stop_requested: true }) } }
  async function retry() { if (activeRun) { const run = await api.retry(activeRun.id); setEvents([]); setReport(null); setActiveRun(run); connect(run) } }

  const running = activeRun?.status === 'queued' || activeRun?.status === 'running'
  const inputLocked = detail?.mode === 'competition' && running
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
      <header className="topbar"><div><span className="eyebrow">THREAD</span><h2>{detail?.title ?? '选择或创建一个任务'}</h2></div>{detail && <div className="top-meta"><span className="mode">{detail.mode}</span>{activeRun && <Badge status={activeRun.status} />}</div>}</header>
      {!detail ? <section className="empty"><div className="radar">⌁</div><h2>从一个可审计的任务开始</h2><p>创建对话，上传安全样本，并实时观察 Agent 的结构化计划、工具证据和策略判断。</p><button className="primary" onClick={() => setCreateOpen(true)}>创建第一个任务</button></section> : <>
        <section className="conversation" aria-label="对话时间线">
          {detail.messages.map(item => <div key={item.id} className={`message ${item.role}`}><span className="avatar">{item.role === 'user' ? '你' : '智'}</span><div><div className="message-meta">{item.role === 'user' ? '用户任务' : 'Agent'} · {new Date(item.created_at).toLocaleTimeString()}</div><p>{item.content}</p></div></div>)}
          {events.length > 0 && <div className="agent-progress"><div className="progress-title"><span className="pulse" />Agent 执行记录 <span>{events.length} 项</span></div>{events.filter(item => !item.type.startsWith('tool_')).map(event => <EventCard key={event.event_id} event={event} />)}</div>}
          {report && <section className="report" data-testid="final-report"><div className="report-header"><h3>最终报告</h3><div><a href={api.reportUrl(activeRun!.id, 'md')}>Markdown</a><a href={api.reportUrl(activeRun!.id, 'json')}>JSON</a></div></div><ReactMarkdown>{report.markdown}</ReactMarkdown></section>}
        </section>
        <footer className="composer"><div className="attachments">{pendingArtifacts.map(file => <span key={file.id}>📎 {file.filename} · {file.size} B</span>)}</div>{inputLocked && <div className="lock-note">竞赛模式运行中：已锁定补充提示，仅可观察或停止。</div>}{providers.length === 0 && <div className="model-required">需要配置模型：请先进入设置中心添加、测试并启用 Provider。</div>}<textarea aria-label="任务消息" value={message} onChange={event => setMessage(event.target.value)} disabled={inputLocked || busy} placeholder="描述已授权的安全任务、目标范围与成功条件…" /><div className="composer-actions"><label className="file-button">＋ 附件<input aria-label="上传附件" type="file" accept=".txt,.json,.md,.log,.bin" onChange={event => void upload(event.target.files?.[0])} /></label><label className="provider-select">模型<select aria-label="运行模型" value={selectedProviderId} onChange={event => setSelectedProviderId(event.target.value)} disabled={running || providers.length === 0}><option value="">未配置</option>{providers.map(value => <option key={value.id} value={value.id}>{value.name} · {value.model}</option>)}</select></label><span className="authorization">盾 已授权范围内执行</span><div className="run-actions">{running ? <button className="danger" onClick={() => void stop()}>停止</button> : activeRun && ['failed', 'stopped'].includes(activeRun.status) ? <button onClick={() => void retry()}>重试</button> : null}<button className="primary" disabled={busy || inputLocked || running || !message.trim() || !selectedProviderId} onClick={() => void sendAndRun()}>{busy ? '处理中…' : '启动运行 ↗'}</button></div></div></footer>
      </>}
      {error && <div role="alert" className="toast">{error}</div>}
    </main>

    <aside className="inspector">
      <div className="inspector-head"><span>运行审计</span><small>LIVE</small></div>
      <div className="metrics"><div><strong>{metrics.events}</strong><span>事件</span></div><div><strong>{metrics.tools}</strong><span>工具调用</span></div><div><strong>{metrics.replans}</strong><span>重规划</span></div></div>
      <div className="section-label">工具与证据</div><div className="tool-list">{events.filter(item => item.type.startsWith('tool_')).map(event => <EventCard key={event.event_id} event={event} />)}{!events.some(item => item.type.startsWith('tool_')) && <p className="muted">运行后将在此展示折叠的工具审计卡片。</p>}</div>
      <div className="section-label">附件</div>{detail?.artifacts.map(item => <a className="artifact" key={item.id} href={api.artifactUrl(item.id)}><span>TXT</span><div><strong>{item.filename}</strong><small>{item.sha256.slice(0, 12)}… · {item.size} B</small></div></a>)}
    </aside>

    {createOpen && <div className="modal-backdrop"><form className="modal" onSubmit={event => { event.preventDefault(); void createThread() }}><h2>创建安全任务</h2><label>任务名称<input aria-label="任务名称" value={newTitle} onChange={event => setNewTitle(event.target.value)} /></label><label>运行模式<select aria-label="运行模式" value={newMode} onChange={event => setNewMode(event.target.value as Mode)}><option value="normal">normal · 可继续交流</option><option value="competition">competition · 运行中锁定输入</option></select></label><p>所有网络目标默认拒绝，仅执行已注册且经策略批准的工具。</p><div><button type="button" onClick={() => setCreateOpen(false)}>取消</button><button className="primary" type="submit" disabled={busy}>创建</button></div></form></div>}
    {settingsOpen && <SettingsCenter onClose={() => setSettingsOpen(false)} onChanged={loadProviders} />}
  </div>
}
