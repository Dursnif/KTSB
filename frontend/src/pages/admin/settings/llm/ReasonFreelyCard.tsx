import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiPutLlmRole, type LlmRoleConfig } from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback } from "../shared";

const REASON_FREELY_FIELDS = ["max_tokens", "temperature", "top_p", "presence_penalty"] as const;

/**
 * `reason_freely` is a tool the main agent (`default`) calls on itself — not an
 * independent agent — so it renders nested here rather than as its own top-level
 * card in `roleOrder`. Shares `default`'s vLLM instance/base_url (kaare_core/tools/executor_library.py).
 */
export function ReasonFreelyCard({ config }: { config?: LlmRoleConfig }) {
  const { t } = useTranslation();
  const [local, setLocal] = useState<Record<string, number>>({ ...(config?.options ?? {}) });
  const ss = useSaveState();

  const setOpt = (k: string, v: number) => setLocal(p => ({ ...p, [k]: v }));

  const save = async () => {
    ss.saving();
    try {
      await apiPutLlmRole("reason_freely", { options: local });
      ss.saved();
    } catch {
      ss.error();
    }
  };

  return (
    <div className="rounded-md border border-border/50 bg-muted/30 p-3">
      <p className="text-sm font-medium">{t("settings.llm.reason_freely_title")}</p>
      <p className="text-xs text-muted-foreground mb-2">{t("settings.llm.reason_freely_desc")}</p>
      <div className="divide-y divide-border">
        {REASON_FREELY_FIELDS.map((k) => (
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
      <div className="flex items-center gap-3 mt-2">
        <Button size="sm" variant="outline" onClick={save} disabled={ss.state === "saving"}>
          {ss.state === "saving" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
          {t("common.save")}
        </Button>
        <SaveFeedback state={ss.state} />
      </div>
    </div>
  );
}
