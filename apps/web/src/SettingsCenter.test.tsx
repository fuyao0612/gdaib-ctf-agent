import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsCenter from './SettingsCenter'

const defaults = { budget: { max_steps: 20, max_model_calls: 8, max_tool_calls: 8, max_tokens: 8000, max_duration_seconds: 120, step_timeout_seconds: 15 }, provider_retry_budget: 2, context_token_budget: 32000, observation_char_budget: 20000 }

describe('SettingsCenter', () => {
  const storageSet = vi.fn()
  beforeEach(() => { storageSet.mockClear(); vi.stubGlobal('localStorage', { setItem: storageSet, getItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn() }); vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    if (input.endsWith('/provider-presets')) return Response.json({ deepseek: { base_url: 'https://api.deepseek.com', model: 'deepseek-v4-flash' } })
    if (input.endsWith('/admin/settings/providers') && !init?.method) return Response.json([])
    if (input.endsWith('/admin/settings/agent') && !init?.method) return Response.json(defaults)
    if (input.endsWith('/admin/settings/providers') && init?.method === 'POST') return Response.json({ id: 'p1', name: 'DeepSeek', preset: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-flash', enabled: true, is_default: true, fallback_order: 0, timeout_seconds: 60, max_retries: 2, structured_mode: 'json_schema', has_api_key: true, created_at: '', updated_at: '' }, { status: 201 })
    return Response.json({})
  })) })
  afterEach(() => vi.unstubAllGlobals())

  it('keeps the admin token in memory and creates a masked provider', async () => {
    const changed = vi.fn(async () => undefined)
    render(<SettingsCenter onClose={() => undefined} onChanged={changed} />)
    fireEvent.change(screen.getByLabelText('管理员令牌'), { target: { value: 'admin-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '进入设置' }))
    await screen.findByText('模型 Provider')
    const keyInput = screen.getByPlaceholderText('输入 Provider API Key')
    expect(keyInput).toHaveAttribute('type', 'password')
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'DeepSeek' } })
    fireEvent.change(keyInput, { target: { value: 'provider-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '创建 Provider' }))
    await waitFor(() => expect(changed).toHaveBeenCalled())
    expect(storageSet).not.toHaveBeenCalled()
  })
})
