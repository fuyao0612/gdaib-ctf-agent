import { type FormEvent, useEffect, useState } from 'react'
import { api } from './api'
import AgentProfileCenter from './components/AgentProfileCenter'
import { useAdminSession } from './hooks/useAdminSession'
import type { AgentDefaults, FallbackCategory, ProviderConfig, ProviderConfigInput, ProviderPreset, StructuredMode } from './types'

interface Props { onClose: () => void; onChanged: () => Promise<void>; initialSetup?: boolean }

const emptyProvider: ProviderConfigInput = {
  name: '', preset: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-flash',
  api_key: '', enabled: true, is_default: false, fallback_order: null,
  timeout_seconds: 60, max_retries: 2, structured_mode: 'auto',
  input_price_per_million: 0, output_price_per_million: 0,
  fallback_on: ['rate_limit', 'timeout', 'service'],
}

export default function SettingsCenter({ onClose, onChanged, initialSetup = false }: Props) {
  const [adminToken, setAdminToken] = useState('')
  const session = useAdminSession()
  const [providers, setProviders] = useState<ProviderConfig[]>([])
  const [presets, setPresets] = useState<Record<string, { base_url: string; model: string }>>({})
  const [agent, setAgent] = useState<AgentDefaults | null>(null)
  const [form, setForm] = useState<ProviderConfigInput>(emptyProvider)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { void api.providerPresets().then(setPresets).catch(() => setPresets({})) }, [])

  async function load(csrf = session.csrf) {
    const [items, defaults] = await Promise.all([api.adminProviders(csrf), api.agentDefaults(csrf)])
    setProviders(items); setAgent(defaults)
  }

  async function authenticate(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { const csrf = await session.login(adminToken); setAdminToken(''); await load(csrf); setNotice('已建立安全管理员会话') } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }

  function selectPreset(preset: ProviderPreset) {
    const value = presets[preset]
    setForm(current => ({
      ...current,
      preset,
      ...(value ? { base_url: value.base_url, model: value.model } : {}),
    }))
  }

  function edit(value: ProviderConfig) {
    setEditingId(value.id)
    setForm({
      name: value.name, preset: value.preset, base_url: value.base_url, model: value.model,
      api_key: '', enabled: value.enabled, is_default: value.is_default,
      fallback_order: value.fallback_order, timeout_seconds: value.timeout_seconds,
      max_retries: value.max_retries, structured_mode: value.structured_mode,
      input_price_per_million: value.input_price_per_million,
      output_price_per_million: value.output_price_per_million,
      fallback_on: value.fallback_on,
    })
    setNotice('编辑时留空 API Key 将保留现有密钥。')
  }

  function resetForm() { setEditingId(null); setForm(emptyProvider); setNotice('') }

  async function saveProvider(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      const payload = { ...form, api_key: form.api_key?.trim() || null }
      if (editingId) await api.updateProvider(session.csrf, editingId, payload)
      else await api.createProvider(session.csrf, payload)
      setForm(emptyProvider); setEditingId(null); await load(); await onChanged()
      setNotice(editingId ? 'Provider 已更新' : 'Provider 已创建')
    } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }

  async function removeProvider(id: string) {
    setBusy(true); setError('')
    try { await api.deleteProvider(session.csrf, id); await load(); await onChanged(); setNotice('Provider 已删除') } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }

  async function testProvider(id: string) {
    setBusy(true); setError(''); setNotice('正在调用模型进行真实连接测试…')
    try { const result = await api.testProvider(session.csrf, id); setNotice(`连接测试成功：${result.model} · ${result.structured_mode} · ${result.latency_ms} ms`) } catch (cause) { setError(String(cause)); setNotice('') } finally { setBusy(false) }
  }

  async function discoverModels(id: string) {
    setBusy(true); setError('')
    try { const result = await api.discoverProviderModels(session.csrf, id); setNotice(result.models.length ? `发现模型：${result.models.join('、')}` : '端点未返回模型列表，可继续手动填写模型名称') } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }

  async function saveAgent(event: FormEvent) {
    event.preventDefault(); if (!agent) return; setBusy(true); setError('')
    try { setAgent(await api.saveAgentDefaults(session.csrf, agent)); setNotice('Agent 默认预算已保存') } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }

  return <div className="settings-backdrop" role="dialog" aria-label="设置中心">
    <section className="settings-panel">
      <header><div><span className="eyebrow">ADMIN SETTINGS</span><h2>设置中心</h2></div><button onClick={() => { void session.logout().finally(onClose) }}>关闭</button></header>
      {initialSetup && <div className="setup-progress"><strong>首次配置向导</strong><span>1 管理员登录 → 2 配置 Provider → 3 连接测试 → 4 确认默认 Agent → 5 开始对话</span><small>管理员令牌只用于建立服务端会话，不会保存到浏览器。</small></div>}
      {!session.authenticated ? <form className="admin-login" onSubmit={authenticate}>
        <h3>管理员验证</h3><p>令牌仅保存在当前页面内存中，关闭或刷新后即清除。</p>
        <label>管理员令牌<input type="password" aria-label="管理员令牌" autoComplete="off" value={adminToken} onChange={event => setAdminToken(event.target.value)} /></label>
        <button className="primary" disabled={busy || !adminToken}>进入设置</button>
      </form> : <div className="settings-content">
        <section><div className="settings-title"><h3>模型 Provider</h3><button onClick={resetForm}>新增配置</button></div>
          <div className="provider-table">{providers.map(value => <article key={value.id} className={value.is_default ? 'provider-row default' : 'provider-row'}>
            <div><strong>{value.name}</strong><small>{value.preset} · {value.model}</small><small>{value.base_url}</small></div>
            <div className="provider-flags"><span>{value.has_api_key ? '密钥已保存' : '缺少密钥'}</span>{value.is_default && <span>默认</span>}{!value.enabled && <span>已停用</span>}</div>
            <div><button onClick={() => void testProvider(value.id)}>连接测试</button><button onClick={() => void discoverModels(value.id)}>发现模型</button><button onClick={() => edit(value)}>编辑</button><button className="danger" disabled={value.is_default} onClick={() => void removeProvider(value.id)}>删除</button></div>
          </article>)}</div>
          <form className="settings-form" onSubmit={saveProvider}>
            <h4>{editingId ? '编辑 Provider' : '新增 Provider'}</h4>
            <div className="form-grid"><label>名称<input value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} required /></label>
              <label>厂商预设<select value={form.preset} onChange={event => selectPreset(event.target.value as ProviderPreset)}><option value="deepseek">DeepSeek</option><option value="qwen">阿里云百炼 / 千问</option><option value="glm">智谱 GLM</option><option value="custom">自定义兼容 API</option></select></label>
              <label className="wide">Base URL<input value={form.base_url} onChange={event => setForm({ ...form, base_url: event.target.value })} required /></label>
              <label>模型<input value={form.model} onChange={event => setForm({ ...form, model: event.target.value })} required /></label>
              <label>API Key<input type="password" autoComplete="new-password" value={form.api_key ?? ''} placeholder={editingId ? '留空以保留现有密钥' : '输入 Provider API Key'} onChange={event => setForm({ ...form, api_key: event.target.value })} required={!editingId} /></label>
              <label>超时（秒）<input type="number" min="1" max="600" value={form.timeout_seconds} onChange={event => setForm({ ...form, timeout_seconds: Number(event.target.value) })} /></label>
              <label>重试次数<input type="number" min="0" max="8" value={form.max_retries} onChange={event => setForm({ ...form, max_retries: Number(event.target.value) })} /></label>
              <label>输入价格/百万 Token<input type="number" min="0" step="0.0001" value={form.input_price_per_million} onChange={event => setForm({ ...form, input_price_per_million: Number(event.target.value) })} /></label>
              <label>输出价格/百万 Token<input type="number" min="0" step="0.0001" value={form.output_price_per_million} onChange={event => setForm({ ...form, output_price_per_million: Number(event.target.value) })} /></label>
              <label>备用顺序<input type="number" min="0" max="100" value={form.fallback_order ?? ''} onChange={event => setForm({ ...form, fallback_order: event.target.value === '' ? null : Number(event.target.value) })} /></label>
              <label>结构化模式<select value={form.structured_mode} onChange={event => setForm({ ...form, structured_mode: event.target.value as StructuredMode })}><option value="auto">自动协商（推荐）</option><option value="json_schema">JSON Schema</option><option value="json_object">JSON Object</option><option value="prompt_json">提示词兼容模式</option></select></label></div>
            <fieldset className="check-row"><legend>允许触发备用模型的错误</legend>{(['rate_limit','timeout','service','invalid_output'] as FallbackCategory[]).map(category => <label key={category}><input type="checkbox" checked={form.fallback_on.includes(category)} onChange={event => setForm({ ...form, fallback_on: event.target.checked ? [...form.fallback_on, category] : form.fallback_on.filter(value => value !== category) })} />{category}</label>)}</fieldset>
            <div className="check-row"><label><input type="checkbox" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} />启用</label><label><input type="checkbox" checked={form.is_default} onChange={event => setForm({ ...form, is_default: event.target.checked })} />设为默认</label></div>
            <button className="primary" disabled={busy}>{editingId ? '保存修改' : '创建 Provider'}</button>
          </form>
        </section>
        <AgentProfileCenter csrf={session.csrf} providers={providers} onChanged={onChanged} />
        {agent && <section><div className="settings-title"><h3>平台默认预算</h3></div><form className="settings-form" onSubmit={saveAgent}><div className="form-grid">
          {([['最大步骤','max_steps'],['模型调用','max_model_calls'],['工具调用','max_tool_calls'],['最大 Token','max_tokens'],['最大模型费用','max_model_cost'],['总时长（秒）','max_duration_seconds'],['单步超时（秒）','step_timeout_seconds']] as const).map(([label,key]) => <label key={key}>{label}<input type="number" value={agent.budget[key]} onChange={event => setAgent({ ...agent, budget: { ...agent.budget, [key]: Number(event.target.value) } })} /></label>)}
          <label>Provider 重试预算<input type="number" value={agent.provider_retry_budget} onChange={event => setAgent({ ...agent, provider_retry_budget: Number(event.target.value) })} /></label>
          <label>上下文 Token 预算<input type="number" value={agent.context_token_budget} onChange={event => setAgent({ ...agent, context_token_budget: Number(event.target.value) })} /></label>
          <label>观察字符预算<input type="number" value={agent.observation_char_budget} onChange={event => setAgent({ ...agent, observation_char_budget: Number(event.target.value) })} /></label>
        </div><button className="primary" disabled={busy}>保存 Agent 设置</button></form></section>}
      </div>}
      {notice && <div className="settings-notice">{notice}</div>}{error && <div role="alert" className="settings-error">{error}</div>}
    </section>
  </div>
}
