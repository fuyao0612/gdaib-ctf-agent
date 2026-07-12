import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

class FakeEventSource { static CLOSED = 2; readyState = 1; onmessage: ((event: MessageEvent) => void) | null = null; onerror = null; constructor(public url: string) {} close() { this.readyState = 2 } }

describe('App', () => {
  beforeEach(() => { vi.stubGlobal('EventSource', FakeEventSource); vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    if (input.endsWith('/threads') && !init?.method) return Response.json([])
    if (input.endsWith('/threads') && init?.method === 'POST') return Response.json({ id: 't1', title: '测试任务', mode: 'competition', archived: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString() })
    if (input.endsWith('/threads/t1')) return Response.json({ id: 't1', title: '测试任务', mode: 'competition', archived: false, messages: [], runs: [], artifacts: [], created_at: '', updated_at: '' })
    return Response.json({})
  })) })
  afterEach(() => vi.unstubAllGlobals())

  it('creates and selects a competition thread', async () => {
    render(<App />); expect(await screen.findByText('从一个可审计的任务开始')).toBeInTheDocument()
    fireEvent.click(screen.getByText('创建第一个任务')); fireEvent.change(screen.getByLabelText('运行模式'), { target: { value: 'competition' } }); fireEvent.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(screen.getByText('测试任务')).toBeInTheDocument())
    expect(screen.getAllByText('competition').length).toBeGreaterThan(0)
  })
})
