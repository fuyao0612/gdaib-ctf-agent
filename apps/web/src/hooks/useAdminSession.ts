/** 自动建立或恢复本机 HttpOnly 会话，并仅在内存保留 CSRF 令牌。 */
import { useEffect, useState } from "react";
import { api, setSessionCsrf } from "../api";

export function useAdminSession() {
  const [csrf, setCsrf] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const value = await api.adminSession().catch(() => null);
        if (value?.authenticated && value.csrf_token) {
          setCsrf(value.csrf_token);
          setSessionCsrf(value.csrf_token);
          return;
        }
        const session = await api.createAdminSession();
        setCsrf(session.csrf_token);
        setSessionCsrf(session.csrf_token);
      } catch (cause) {
        setError(String(cause));
      }
    })();
  }, []);

  return { csrf, authenticated: Boolean(csrf), error };
}
