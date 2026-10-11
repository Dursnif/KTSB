import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { apiPutLlmRole, type LlmRoleConfig } from "@/services/api";
import { FieldRow, SaveFeedback } from "../shared";
import { useSaveState } from "../useSaveState";

/**
 * Generic sampling-options card for a tool that borrows another role's vLLM/Ollama
 * instance but needs its own sampling profile (e.g. `reason_freely` on top of `default`).
 * Own top-level Card (same visual weight as LlmRoleCard) — never nest this inside
 * another role's card again, that's what caused N09 (silent mis-save onto the wrong
 * role because the two Save buttons and field labels were visually indistinguishable).
 */
export function ToolOptionsCard({ role, titleKey, descKey, fields, config }: {
  role: string;
  titleKey: string;
  descKey: string;
  fields: readonly string[];
  config?: LlmRoleConfig;
}) {
  const { t } = useTranslation();
  const [local, setLocal] = useState<Record<string, number>>({ ...(config?.options ?? {}) });
  const ss = useSaveState();

  const setOpt = (k: string, v: number) => setLocal(p => ({ ...p, [k]: v }));

  const save = async () => {
    ss.saving();
    try {
      await apiPutLlmRole(role, { options: local });
      ss.saved();
    } catch {
      ss.error();
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{t(titleKey)}</CardTitle>
        <CardDescription className="text-xs">{t(descKey)}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="divide-y divide-border">
          {fields.map((k) => (
            <FieldRow key={k} label={t(`settings.llm.vllm_fields.${k}.label`)} hint={t(`settings.llm.vllm_fields.${k}.hint`)}>
              <input
                type="number"
                value={local[k] ?? ""}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setOpt(k, Number(e.target.value))}
                className="flex h-9 w-32 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
                step={k === "max_tokens" ? "1" : "0.01"}
              />
            </FieldRow>
          ))}
        </div>
        <div className="flex items-center gap-3 mt-3">
          <Button size="sm" variant="outline" onClick={save} disabled={ss.state === "saving"}>
            {ss.state === "saving" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            {t("common.save")}
          </Button>
          <SaveFeedback state={ss.state} />
        </div>
      </CardContent>
    </Card>
  );
}
