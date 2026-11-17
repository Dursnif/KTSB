// A Frigate model role card: `frigate` (P144-s03, which LLM/VLM analyses Frigate events) or `frigate_conclude`
// (P144-s06b, which model concludes per closed episode). Self-contained so it drops into Integrations → Frigate
// with one line; shares Kåre's model (`default`) until the user picks one.
import { useCallback, useEffect, useState } from "react";
import {
  apiGetLlmSettings, apiGetLlmModelCheck, type LlmRoleConfig, type LlmModelCheck,
} from "@/services/api";
import { LlmRoleCard } from "./LlmRoleCard";

export function FrigateModelCard({ role = "frigate" }: { role?: "frigate" | "frigate_conclude" }) {
  const [configs, setConfigs] = useState<Record<string, LlmRoleConfig> | null>(null);
  const [modelCheck, setModelCheck] = useState<Record<string, LlmModelCheck>>({});

  const load = useCallback(() => {
    apiGetLlmSettings().then(setConfigs).catch(() => {});
  }, []);

  useEffect(() => { load(); }, [load]);
  // Asks every Ollama instance — kept apart so a slow one does not delay the card.
  useEffect(() => { apiGetLlmModelCheck().then(setModelCheck).catch(() => {}); }, []);

  if (!configs?.[role]) return null;
  return (
    <LlmRoleCard
      role={role}
      config={configs[role]}
      onSaved={load}
      allConfigs={configs}
      modelCheck={modelCheck[role]}
    />
  );
}
