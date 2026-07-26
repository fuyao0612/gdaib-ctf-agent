/** 自动建立或恢复本机 HttpOnly 会话，并仅在内存保留 CSRF 令牌。 */
import { useEffect, useState } from "react";
import { api, getSessionCsrf } from "../api";

export function useAdminSession() {
  const [csrf, setCsrf] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const existingCsrf = getSessionCsrf();
        if (existingCsrf) {
          setCsrf(existingCsrf);
          return;
        }
        // 设置中心按需打开。直接创建/更新本机会话，避免无 Cookie 时先发
        // GET /admin/session 并把正常初始化误报为认证错误。
        const session = await api.createAdminSession();
        setCsrf(session.csrf_token);
      } catch (cause) {
        setError(String(cause));
      }
    })();
  }, []);

  return { csrf, authenticated: Boolean(csrf), error };
}
