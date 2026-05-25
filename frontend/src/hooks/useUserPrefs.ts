import { useState, useCallback } from "react";

const PREFS_KEY = "kaare_user_prefs";

export interface UserPrefs {
  accentColor: string | null;
  fontSize: "small" | "normal" | "large";
  animations: "standard" | "minimal";
  ttsAutoplay: boolean;
  showTrace: boolean;
  mkPanelEnabled: boolean;
}

export const DEFAULT_PREFS: UserPrefs = {
  accentColor: null,
  fontSize: "normal",
  animations: "standard",
  ttsAutoplay: false,
  showTrace: false,
  mkPanelEnabled: true,
};

export function readUserPrefs(): UserPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (raw) return { ...DEFAULT_PREFS, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { ...DEFAULT_PREFS };
}

export function useUserPrefs() {
  const [prefs, setPrefsState] = useState<UserPrefs>(readUserPrefs);

  const updatePrefs = useCallback((updates: Partial<UserPrefs>) => {
    setPrefsState(prev => {
      const next = { ...prev, ...updates };
      try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  }, []);

  const resetPrefs = useCallback(() => {
    try { localStorage.removeItem(PREFS_KEY); } catch { /* ignore */ }
    setPrefsState({ ...DEFAULT_PREFS });
  }, []);

  return { prefs, updatePrefs, resetPrefs };
}
