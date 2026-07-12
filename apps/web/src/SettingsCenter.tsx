import { type FormEvent, useEffect, useState } from 'react'
import { api } from './api'
import type { AgentDefaults, ProviderConfig, ProviderConfigInput, ProviderPreset } from './types'

interface Props { onClose: () => void; onChanged: () => Promise<void> }

const emptyProvider: ProviderConfigInput = {
  name: '', preset: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-flash',
  api_key: '', enabled: true, is_default: false, fallback_order: null,
  timeout_seconds: 60, max_retries: 2, structured_mode: 'json_schema',
}

export default function SettingsCenter({ onClose, onChanged }: Props) {
  const [adminToken, setAdminToken] = useState('')
  const [authenticated, setAuthenticated] = useState(false)
  const [providers, setProviders] = useState<ProviderConfig[]>([])
  const [presets, setPresets] = useState<Record<string, { base_url: string; model: string }>>({})
  const [agent, setAgent] = useState<AgentDefaults | null>(null)
  const [form, setForm] = useState<ProviderConfigInput>(emptyProvider)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { void api.providerPresets().then(setPresets).catch(() => setPresets({})) }, [])

  async function load(token = adminToken) {
    const [items, defaults] = await Promise.all([api.adminProviders(token), api.agentDefaults(token)])
    setProviders(items); setAgent(defaults); setAuthenticated(true)
  }

  async function authenticate(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { await load(); setNotice('管理员身份验证成功') } catch (cause) { setError(String(cause)); setAuthenticated(false) } finally { setBusy(false) }
  }

  function selectPreset(preset: ProviderPreset) {
    const value = presets[preset]
    setForm(current => ({ ...current, preset, ...(value ?? {}) }))
  }

  function edit(value: ProviderConfig) {
    setEditingId(value.id)
    setForm({
      name: value.name, preset: value.preset, base_url: value.base_url, model: value.model,
      api_key: '', enabled: value.enabled, is_default: value.is_default,
      fallback_order: value.fallback_order, timeout_seconds: value.timeout_seconds,
      max_retries: value.max_retries, structured_mode: value.structured_mode,
    })
    setNotice('编辑时留空 API Key 将保留现有密钥。')
  }

  function resetForm() { setEditingId(null); setForm(emptyProvider); setNotice('') }

  async function saveProvider(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      const payload = { ...form, api_key: form.api_key?.trim() || null }
      if (editingId) await api.updateProvider(adminToken, editingId, payload)
      else await api.createProvider(adminToken, payload)
      setForm(emptyProvider); setEditingId(null); await load(); await onChanged()
      setNotice(editingId ? 'Provider 已更新' : 'Provider 已创建')
    } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }

  async function removeProvider(id: string) {
    setBusy(true); setError('')
    try { await api.deleteProvider(adminToken, id); await load(); await onChanged(); setNotice('Provider 已删除') } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }

  async function testProvider(id: string) {
    setBusy(true); setError(''); setNotice('正在调用模型进行真实连接测试…')
    try { await api.testProvider(adminToken, id); setNotice('连接测试成功') } catch (cause) { setError(String(cause)); setNotice('') } finally { setBusy(false) }
  }

  async function saveAgent(event: FormEvent) {
    event.preventDefault(); if (!agent) return; setBusy(true); setError('')
    try { setAgent(await api.saveAgentDefaults(adminToken, agent)); setNotice('Agent 默认预算已保存') } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }

  return <div className="settings-backdrop" role="dialog" aria-label="设置中心">
    <section className="settings-panel">
      <header><div><span className="eyebrow">ADMIN SETTINGS</span><h2>设置中心</h2></div><button onClick={onClose}>关闭</button></header>
      {!authenticated ? <form className="admin-login" onSubmit={authenticate}>
        <h3>管理员验证</h3><p>令牌仅保存在当前页面内存中，关闭或刷新后即清除。</p>
        <label>管理员令牌<input type="password" aria-label="管理员令牌" autoComplete="off" value={adminToken} onChange={event => setAdminToken(event.target.value)} /></label>
        <button className="primary" disabled={busy || !adminToken}>进入设置</button>
      </form> : <div className="settings-content">
        <section><div className="settings-title"><h3>模型 Provider</h3><button onClick={resetForm}>新增配置</button></div>
          <div className="provider-table">{providers.map(value => <article key={value.id} className={value.is_default ? 'provider-row default' : 'provider-row'}>
            <div><strong>{value.name}</strong><small>{value.preset} · {value.model}</small><small>{value.base_url}</small></div>
            <div className="provider-flags"><span>{value.has_api_key ? '密钥已保存' : '缺少密钥'}</span>{value.is_default && <span>默认</span>}{!value.enabled && <span>已停用</span>}</div>
            <div><button onClick={() => void testProvider(value.id)}>连接测试</button><button onClick={() => edit(value)}>编辑</button><button className="danger" disabled={value.is_default} onClick={() => void removeProvider(value.id)}>删除</button></div>
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
              <label>备用顺序<input type="number" min="0" max="100" value={form.fallback_order ?? ''} onChange={event => setForm({ ...form, fallback_order: event.target.value === '' ? null : Number(event.target.value) })} /></label>
              <label>结构化模式<select value={form.structured_mode} onChange={event => setForm({ ...form, structured_mode: event.target.value as 'json_schema' | 'json_object' })}><option value="json_schema">JSON Schema</option><option value="json_object">JSON Object</option></select></label></div>
            <div className="check-row"><label><input type="checkbox" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} />启用</label><label><input type="checkbox" checked={form.is_default} onChange={event => setForm({ ...form, is_default: event.target.checked })} />设为默认</label></div>
            <button className="primary" disabled={busy}>{editingId ? '保存修改' : '创建 Provider'}</button>
          </form>
        </section>
        {agent && <section><div className="settings-title"><h3>Agent 默认预算</h3></div><form className="settings-form" onSubmit={saveAgent}><div className="form-grid">
          {([['最大步骤','max_steps'],['模型调用','max_model_calls'],['工具调用','max_tool_calls'],['最大 Token','max_tokens'],['总时长（秒）','max_duration_seconds'],['单步超时（秒）','step_timeout_seconds']] as const).map(([label,key]) => <label key={key}>{label}<input type="number" value={agent.budget[key]} onChange={event => setAgent({ ...agent, budget: { ...agent.budget, [key]: Number(event.target.value) } })} /></label>)}
          <label>Provider 重试预算<input type="number" value={agent.provider_retry_budget} onChange={event => setAgent({ ...agent, provider_retry_budget: Number(event.target.value) })} /></label>
          <label>上下文 Token 预算<input type="number" value={agent.context_token_budget} onChange={event => setAgent({ ...agent, context_token_budget: Number(event.target.value) })} /></label>
          <label>观察字符预算<input type="number" value={agent.observation_char_budget} onChange={event => setAgent({ ...agent, observation_char_budget: Number(event.target.value) })} /></label>
        </div><button className="primary" disabled={busy}>保存 Agent 设置</button></form></section>}
      </div>}
      {notice && <div className="settings-notice">{notice}</div>}{error && <div role="alert" className="settings-error">{error}</div>}
    </section>
  </div>
}
