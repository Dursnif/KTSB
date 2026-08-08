import { createContext, useContext, useEffect, useState } from "react";

export type SettingsLevel = "basic" | "expert";

const STORAGE_KEY = "kaare_settings_level";

type SettingsLevelContextValue = {
  level: SettingsLevel;
  setLevel: (level: SettingsLevel) => void;
  toggle: () => void;
};

const SettingsLevelContext = createContext<SettingsLevelContextValue>({
  level: "basic",
  setLevel: () => {},
  toggle: () => {},
});

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

export function useSettingsLevel() {
  return useContext(SettingsLevelContext);
}
