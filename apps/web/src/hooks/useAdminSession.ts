import { useCallback, useState } from 'react'
import { api } from '../api'

export function useAdminSession() {
  const [csrf, setCsrf] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const login = useCallback(async (token: string) => {
    setBusy(true); setError('')
    try { const session = await api.createAdminSession(token); setCsrf(session.csrf_token); return session.csrf_token }
    catch (cause) { setError(String(cause)); throw cause }
    finally { setBusy(false) }
  }, [])

  const logout = useCallback(async () => {
    if (csrf) await api.deleteAdminSession(csrf).catch(() => undefined)
    setCsrf('')
  }, [csrf])

  return { csrf, authenticated: Boolean(csrf), busy, error, login, logout }
}
