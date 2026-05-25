import React, { createContext, useContext, useMemo, useState, useEffect, useRef, useCallback } from "react";
import type { KaareUser } from "../services/api";

const INACTIVITY_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

type AuthContextValue = {
  user: KaareUser | null;
  login: (user: KaareUser, token: string) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<KaareUser | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Gjenopprett sesjon fra sessionStorage ved reload
  useEffect(() => {
    const raw = sessionStorage.getItem("kaare_user");
    if (raw) {
      try { setUser(JSON.parse(raw)); } catch { sessionStorage.clear(); }
    }
  }, []);

  const logout = useCallback(() => {
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

  const value = useMemo(() => ({ user, login, logout }), [user, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
