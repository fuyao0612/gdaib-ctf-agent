import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { AgentProfile, AgentProfileInput, ProviderConfig } from '../types'

const workflowNodes = ['normalize_task','plan','select_action','policy_check','execute_tool','observe','verify','replan','request_input','generate_report']
const emptyProfile: AgentProfileInput = {
  name: '新的 Agent', description: '', run_mode: 'normal', default_provider_id: null,
  fallback_provider_ids: [], user_prompt_template: '请处理以下任务：{task}', planning_strategy: 'dynamic',
  budget: { max_steps: 20, max_model_calls: 8, max_tool_calls: 8, max_tokens: 8000, max_model_cost: 10, max_duration_seconds: 120, step_timeout_seconds: 15 },
  context_policy: { recent_message_limit: 20, include_thread_summary: true, include_run_summaries: true, include_memories: true, text_attachment_char_limit: 20000 },
  memory_policy: { enabled: true, persist_important_facts: true, max_facts: 100 },
  completion_mode: 'evidence', validation_policy: { require_external_evidence: true, json_schema: null },
  intervention_policy: { normal_mode: 'wait', competition_mode: 'fail', max_requests: 2 },
  workflow: { nodes: workflowNodes }, report_template: '# {task}\n\n{observations}', enabled: true, is_default: false,
}

function inputFromProfile(value: AgentProfile): AgentProfileInput {
  const { profile_id: _id, version: _version, schema_version: _schema, created_at: _created, ...input } = value
  void _id; void _version; void _schema; void _created
  return input
}

export default function AgentProfileCenter({ csrf, providers, onChanged }: { csrf: string; providers: ProviderConfig[]; onChanged: () => Promise<void> }) {
  const [profiles, setProfiles] = useState<AgentProfile[]>([])
  const [form, setForm] = useState<AgentProfileInput>(emptyProfile)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [versions, setVersions] = useState<AgentProfile[]>([])
  const [expert, setExpert] = useState(false)
  const [wizardStep, setWizardStep] = useState(1)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [preview, setPreview] = useState('')
  const [schemaText, setSchemaText] = useState('')

  async function load() { setProfiles(await api.adminProfiles(csrf)); await onChanged() }
  useEffect(() => { void load() }, [csrf]) // eslint-disable-line react-hooks/exhaustive-deps
  const selected = useMemo(() => profiles.find(value => value.profile_id === editingId), [profiles, editingId])

  function edit(value: AgentProfile) { setEditingId(value.profile_id); setForm(inputFromProfile(value)); setSchemaText(value.validation_policy.json_schema ? JSON.stringify(value.validation_policy.json_schema, null, 2) : ''); setWizardStep(1); setNotice('') }
  function reset() { setEditingId(null); setForm(emptyProfile); setSchemaText(''); setVersions([]); setWizardStep(1); setPreview('') }

  async function save() {
    setError('')
    try {
      const payload = { ...form, validation_policy: { ...form.validation_policy, json_schema: schemaText.trim() ? JSON.parse(schemaText) : null } }
      if (editingId) await api.updateProfile(csrf, editingId, payload); else await api.createProfile(csrf, payload)
      await load(); reset(); setNotice('Agent 配置已保存为新版本')
    } catch (cause) { setError(String(cause)) }
  }
  async function showVersions(value: AgentProfile) { edit(value); setVersions(await api.profileVersions(csrf, value.profile_id)) }
  async function rollback(version: number) { if (!editingId) return; await api.rollbackProfile(csrf, editingId, version); await load(); setVersions(await api.profileVersions(csrf, editingId)); setNotice(`已回滚并创建版本 ${version} 的后继版本`) }
  async function copy(value: AgentProfile) { await api.copyProfile(csrf, value.profile_id, `${value.name} 副本`); await load() }
  async function makeDefault(value: AgentProfile) { await api.defaultProfile(csrf, value.profile_id); await load() }
  async function remove(value: AgentProfile) { await api.deleteProfile(csrf, value.profile_id); await load() }
  async function previewTemplate() { try { setPreview((await api.previewTemplate(csrf, form.user_prompt_template)).rendered) } catch (cause) { setError(String(cause)) } }
  async function exportConfig() { const bundle = await api.exportProfiles(csrf); const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'yuwang-agent-profiles.json'; anchor.click(); URL.revokeObjectURL(url) }
  async function importConfig(file?: File) { if (!file) return; try { await api.importProfiles(csrf, JSON.parse(await file.text())); await load(); setNotice('无密钥配置已导入') } catch (cause) { setError(String(cause)) } }

  return <section className="profile-center" data-testid="agent-profile-center">
    <div className="settings-title"><h3>Agent 配置</h3><div><button onClick={() => setExpert(value => !value)}>{expert ? '向导模式' : '专家模式'}</button><button onClick={reset}>新建配置</button><button onClick={() => void exportConfig()}>无密钥导出</button><label className="file-button">导入<input type="file" accept="application/json" onChange={event => void importConfig(event.target.files?.[0])} /></label></div></div>
    <div className="provider-table">{profiles.map(value => <article className={`provider-row ${value.is_default ? 'default' : ''}`} key={value.profile_id}><div><strong>{value.name}</strong><small>v{value.version} · {value.completion_mode} · {value.run_mode}</small><small>{value.description || '无说明'}</small></div><div className="provider-flags">{value.is_default && <span>默认</span>}{!value.enabled && <span>停用</span>}</div><div><button onClick={() => edit(value)}>编辑</button><button onClick={() => void showVersions(value)}>版本</button><button onClick={() => void copy(value)}>复制</button><button disabled={value.is_default} onClick={() => void makeDefault(value)}>设为默认</button><button className="danger" disabled={value.is_default} onClick={() => void remove(value)}>删除</button></div></article>)}</div>

    <div className="wizard-progress">配置向导 {wizardStep}/5：{['基础','模型与预算','提示词与上下文','工作流与验证','预览保存'][wizardStep - 1]}</div>
    <form className="settings-form" onSubmit={event => { event.preventDefault(); void save() }}>
      {(expert || wizardStep === 1) && <fieldset><legend>基础配置</legend><div className="form-grid"><label>名称<input aria-label="Agent 名称" value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label><label>运行模式<select value={form.run_mode} onChange={event => setForm({ ...form, run_mode: event.target.value as 'normal' | 'competition' })}><option value="normal">普通</option><option value="competition">竞赛</option></select></label><label className="wide">说明<textarea value={form.description} onChange={event => setForm({ ...form, description: event.target.value })} /></label></div><div className="check-row"><label><input type="checkbox" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} />启用</label><label><input type="checkbox" checked={form.is_default} onChange={event => setForm({ ...form, is_default: event.target.checked })} />默认</label></div></fieldset>}
      {(expert || wizardStep === 2) && <fieldset><legend>模型与预算</legend><div className="form-grid"><label>默认 Provider<select value={form.default_provider_id ?? ''} onChange={event => setForm({ ...form, default_provider_id: event.target.value || null })}><option value="">沿用平台默认</option>{providers.filter(value => value.enabled).map(value => <option value={value.id} key={value.id}>{value.name}</option>)}</select></label><label>规划策略<select value={form.planning_strategy} onChange={event => setForm({ ...form, planning_strategy: event.target.value as AgentProfileInput['planning_strategy'] })}><option value="dynamic">动态规划</option><option value="direct">直接回答</option><option value="hybrid">混合</option></select></label>{Object.entries(form.budget).map(([key,value]) => <label key={key}>{key}<input type="number" value={value} onChange={event => setForm({ ...form, budget: { ...form.budget, [key]: Number(event.target.value) } })} /></label>)}</div></fieldset>}
      {(expert || wizardStep === 3) && <fieldset><legend>提示词、上下文与记忆</legend><label>用户提示词模板<textarea aria-label="用户提示词模板" value={form.user_prompt_template} onChange={event => setForm({ ...form, user_prompt_template: event.target.value })} /></label><button type="button" onClick={() => void previewTemplate()}>模板预览</button>{preview && <pre data-testid="template-preview">{preview}</pre>}<div className="form-grid"><label>最近消息<input type="number" value={form.context_policy.recent_message_limit} onChange={event => setForm({ ...form, context_policy: { ...form.context_policy, recent_message_limit: Number(event.target.value) } })} /></label><label>附件字符<input type="number" value={form.context_policy.text_attachment_char_limit} onChange={event => setForm({ ...form, context_policy: { ...form.context_policy, text_attachment_char_limit: Number(event.target.value) } })} /></label><label>最大事实<input type="number" value={form.memory_policy.max_facts} onChange={event => setForm({ ...form, memory_policy: { ...form.memory_policy, max_facts: Number(event.target.value) } })} /></label></div><div className="check-row"><label><input type="checkbox" checked={form.memory_policy.enabled} onChange={event => setForm({ ...form, memory_policy: { ...form.memory_policy, enabled: event.target.checked } })} />启用记忆</label><label><input type="checkbox" checked={form.context_policy.include_memories} onChange={event => setForm({ ...form, context_policy: { ...form.context_policy, include_memories: event.target.checked } })} />上下文包含记忆</label></div></fieldset>}
      {(expert || wizardStep === 4) && <fieldset><legend>工作流、人工介入与完成验证</legend><label>完成模式<select value={form.completion_mode} onChange={event => setForm({ ...form, completion_mode: event.target.value as AgentProfileInput['completion_mode'] })}><option value="advisory">建议回答（未经外部验证）</option><option value="structured">结构化输出</option><option value="evidence">证据验证</option></select></label>{form.completion_mode === 'structured' && <label>JSON Schema<textarea aria-label="JSON Schema" value={schemaText} onChange={event => setSchemaText(event.target.value)} /></label>}<div className="check-row workflow-nodes">{workflowNodes.map(node => <label key={node}><input type="checkbox" checked={form.workflow.nodes.includes(node)} disabled={['normalize_task','select_action','verify','generate_report'].includes(node)} onChange={event => setForm({ ...form, workflow: { nodes: event.target.checked ? [...form.workflow.nodes,node] : form.workflow.nodes.filter(value => value !== node) } })} />{node}</label>)}</div><div className="form-grid"><label>普通模式补充<select value={form.intervention_policy.normal_mode} onChange={event => setForm({ ...form, intervention_policy: { ...form.intervention_policy, normal_mode: event.target.value as 'wait' | 'fail' } })}><option value="wait">等待用户</option><option value="fail">明确失败</option></select></label><label>竞赛模式补充<select value={form.intervention_policy.competition_mode} onChange={event => setForm({ ...form, intervention_policy: { ...form.intervention_policy, competition_mode: event.target.value as 'replan' | 'fail' } })}><option value="replan">自主重规划</option><option value="fail">明确失败</option></select></label></div></fieldset>}
      {(expert || wizardStep === 5) && <fieldset><legend>报告与配置预览</legend><label>报告模板<textarea value={form.report_template} onChange={event => setForm({ ...form, report_template: event.target.value })} /></label><pre className="config-preview">{JSON.stringify(form, null, 2)}</pre></fieldset>}
      {!expert && <div className="wizard-actions"><button type="button" disabled={wizardStep === 1} onClick={() => setWizardStep(step => step - 1)}>上一步</button><button type="button" disabled={wizardStep === 5} onClick={() => setWizardStep(step => step + 1)}>下一步</button></div>}
      <button className="primary" type="submit">{editingId ? `保存新版本（当前 v${selected?.version ?? '?'}）` : '创建 Agent 配置'}</button>
    </form>
    {versions.length > 0 && <section className="version-history"><h4>版本历史与对比</h4>{versions.map(value => <details key={value.version}><summary>v{value.version} · {value.created_at}<button onClick={() => void rollback(value.version)}>回滚到此版本</button></summary><pre>{JSON.stringify(value, null, 2)}</pre></details>)}</section>}
    {notice && <div className="settings-notice">{notice}</div>}{error && <div className="settings-error" role="alert">{error}</div>}
  </section>
}
