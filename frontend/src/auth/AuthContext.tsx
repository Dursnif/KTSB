import React, { useMemo, useState, useEffect, useRef, useCallback } from "react";
import type { KaareUser } from "../services/api";
import { apiLogout } from "../services/api";
import { AuthContext } from "../hooks/useAuth";

const INACTIVITY_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

// Restore the session from sessionStorage on reload.
function restoreUser(): KaareUser | null {
  const raw = sessionStorage.getItem("kaare_user");
  if (!raw) return null;
  try {
    return JSON.parse(raw) as KaareUser;
  } catch {
    sessionStorage.clear();
    return null;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Read synchronously in the initial state, so the very first render already has the
  // restored user — route guards (RequireAuth/RequireAdmin) never see a false "logged out"
  // and never bounce a valid session to /login. `loading` stays in the context for the
  // guards' API but is always false now.
  const [user, setUser] = useState<KaareUser | null>(restoreUser);
  const loading = false;
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const logout = useCallback(async () => {
    // Await before clearing storage — axios interceptors run as a microtask in the
    // promise chain, not synchronously, so removing the token first would race ahead
    // of the request interceptor reading it and send the call with no Authorization header.
    await apiLogout().catch(() => {});
    sessionStorage.removeItem("kaare_token");
    sessionStorage.removeItem("kaare_user");
    setUser(null);
  }, []);

  const resetTimer = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(logout, INACTIVITY_TIMEOUT_MS);
  }, [logout]);

  // Start inactivity timer when logged in, clear when logged out
  useEffect(() => {
    if (!user) {
      if (timerRef.current) clearTimeout(timerRef.current);
      return;
    }
    const events: string[] = ["mousemove", "keydown", "touchstart", "click"];
    events.forEach(e => window.addEventListener(e, resetTimer, { passive: true }));
    resetTimer();
    return () => {
      events.forEach(e => window.removeEventListener(e, resetTimer));
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [user, resetTimer]);

  const login = useCallback((u: KaareUser, token: string) => {
    sessionStorage.setItem("kaare_token", token);
    sessionStorage.setItem("kaare_user", JSON.stringify(u));
    setUser(u);
  }, []);

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
