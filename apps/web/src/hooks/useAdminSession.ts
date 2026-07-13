/** 恢复 HttpOnly 会话对应的 CSRF 令牌；管理员令牌本身从不持久化。 */
import { useCallback, useEffect, useState } from "react";
import { api, setSessionCsrf } from "../api";

export function useAdminSession() {
  const [csrf, setCsrf] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void api
      .adminSession()
      .then((value) => {
        if (value.authenticated && value.csrf_token) {
          setCsrf(value.csrf_token);
          setSessionCsrf(value.csrf_token);
        }
      })
      .catch(() => undefined);
  }, []);

  const login = useCallback(async (token: string) => {
    setBusy(true);
    setError("");
    try {
      const session = await api.createAdminSession(token);
      setCsrf(session.csrf_token);
      setSessionCsrf(session.csrf_token);
      return session.csrf_token;
    } catch (cause) {
      setError(String(cause));
      throw cause;
    } finally {
      setBusy(false);
    }
  }, []);

  const logout = useCallback(async () => {
    if (csrf) await api.deleteAdminSession(csrf).catch(() => undefined);
    setCsrf("");
    setSessionCsrf("");
  }, [csrf]);

  return { csrf, authenticated: Boolean(csrf), busy, error, login, logout };
}
