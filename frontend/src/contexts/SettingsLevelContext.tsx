import { useEffect, useState } from "react";
import { SettingsLevelContext, type SettingsLevel } from "@/hooks/useSettingsLevel";

const STORAGE_KEY = "kaare_settings_level";

export function SettingsLevelProvider({ children }: { children: React.ReactNode }) {
  const [level, setLevelState] = useState<SettingsLevel>(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "expert" ? "expert" : "basic";
  });

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, level);
  }, [level]);

  const setLevel = (next: SettingsLevel) => setLevelState(next);
  const toggle = () => setLevelState(l => (l === "basic" ? "expert" : "basic"));

  return (
    <SettingsLevelContext.Provider value={{ level, setLevel, toggle }}>
      {children}
    </SettingsLevelContext.Provider>
  );
}
