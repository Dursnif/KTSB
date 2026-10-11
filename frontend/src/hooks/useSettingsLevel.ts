import { createContext, useContext } from "react";

export type SettingsLevel = "basic" | "expert";

export type SettingsLevelContextValue = {
  level: SettingsLevel;
  setLevel: (level: SettingsLevel) => void;
  toggle: () => void;
};

export const SettingsLevelContext = createContext<SettingsLevelContextValue>({
  level: "basic",
  setLevel: () => {},
  toggle: () => {},
});

export function useSettingsLevel() {
  return useContext(SettingsLevelContext);
}
