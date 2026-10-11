// Settings → Assistant settings → «Kåre's memory» (P133 C6): who may mark a topic resolved
// (Basic, D11) and how the memory drawers reach the prompt (Expert). Self-contained card.
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Loader2 } from "lucide-react";
import { apiGetMemoryPrompt, apiPutMemoryPrompt, type MemoryPromptSettings } from "@/services/api";
import { apiErrorDetail } from "@/lib/apiError";
import { ExpertOnly, FieldRow, SaveFeedback } from "./shared";
import { useSaveState } from "./useSaveState";

type NumKey = "top_k" | "lessons_max_chars" | "lessons_write_max_chars" | "core_facts_max_chars" | "index_max_chars";
type ScoreKey = "min_score" | "echo_min_score";
type KareKey = keyof MemoryPromptSettings["kare_reminders"];

const NUM_FIELDS: { key: NumKey; min: number; max: number }[] = [
  { key: "top_k", min: 1, max: 10 },
  { key: "lessons_max_chars", min: 200, max: 6000 },
  { key: "lessons_write_max_chars", min: 100, max: 4000 },
  { key: "core_facts_max_chars", min: 200, max: 8000 },
  { key: "index_max_chars", min: 200, max: 8000 },
];
const SCORE_FIELDS: { key: ScoreKey; min: number; max: number }[] = [
  { key: "min_score", min: 0.2, max: 0.95 },
  { key: "echo_min_score", min: 0.3, max: 0.98 },
];
const KARE_FIELDS: { key: KareKey; min: number; max: number }[] = [
  { key: "injection_max_chars", min: 200, max: 8000 },
  { key: "note_prompt_chars", min: 60, max: 2000 },
  { key: "write_max_chars", min: 100, max: 4000 },
  { key: "max_entries", min: 10, max: 1000 },
];

export default function MemoryCard() {
  const { t } = useTranslation();
  const ss = useSaveState();
  const [cfg, setCfg] = useState<MemoryPromptSettings | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiGetMemoryPrompt().then(setCfg).catch(() => setError(t("settings.kare.memory.load_error")));
  }, [t]);

  if (!cfg) {
    return (
      <Card>
        <CardHeader><CardTitle>{t("settings.kare.memory.title")}</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground">{error || t("common.loading")}</CardContent>
      </Card>
    );
  }

  const toggleRole = (role: string) =>
    setCfg({
      ...cfg,
      resolve_roles: cfg.resolve_roles.includes(role)
        ? cfg.resolve_roles.filter(r => r !== role)
        : [...cfg.resolve_roles, role],
    });

  const save = () => {
    setError("");
    ss.saving();
    apiPutMemoryPrompt({
      relevance_enabled: cfg.relevance_enabled,
      top_k: cfg.top_k,
      min_score: cfg.min_score,
      echo_min_score: cfg.echo_min_score,
      lessons_max_chars: cfg.lessons_max_chars,
      lessons_write_max_chars: cfg.lessons_write_max_chars,
      core_facts_max_chars: cfg.core_facts_max_chars,
      index_max_chars: cfg.index_max_chars,
      resolve_roles: cfg.resolve_roles,
      kare_reminders: cfg.kare_reminders,
    })
      .then(() => { ss.saved(); return apiGetMemoryPrompt().then(setCfg); })
      .catch(err => { setError(apiErrorDetail(err) ?? t("common.error")); ss.error(); });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.kare.memory.title")}</CardTitle>
        <CardDescription>{t("settings.kare.memory.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="divide-y divide-border">
          <FieldRow label={t("settings.kare.memory.resolve_roles_label")} hint={t("settings.kare.memory.resolve_roles_hint")}>
            <div className="flex flex-wrap gap-2">
              {cfg.roles.map(role => {
                const checked = cfg.resolve_roles.includes(role);
                return (
                  <button
                    key={role}
                    type="button"
                    aria-pressed={checked}
                    onClick={() => toggleRole(role)}
                    className={`px-3 py-1.5 rounded-full border text-sm transition-colors ${
                      checked
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:border-muted-foreground"
                    }`}
                  >
                    {t(`settings.kare.memory.roles.${role}`)}
                  </button>
                );
              })}
            </div>
          </FieldRow>
          <ExpertOnly><>
            <FieldRow label={t("settings.kare.memory.relevance_label")} hint={t("settings.kare.memory.relevance_hint")}>
              <Switch
                checked={cfg.relevance_enabled}
                onCheckedChange={(v: boolean) => setCfg({ ...cfg, relevance_enabled: v })}
              />
            </FieldRow>
            {cfg.relevance_enabled && cfg.relevance_active === false && (
              <p className="text-xs text-amber-400 py-2">{t("settings.kare.memory.relevance_inactive")}</p>
            )}
            {NUM_FIELDS.map(f => (
              <FieldRow key={f.key} label={t(`settings.kare.memory.${f.key}_label`)} hint={t(`settings.kare.memory.${f.key}_hint`)}>
                <Input
                  type="number" min={f.min} max={f.max} step={1} className="w-28"
                  value={cfg[f.key]}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setCfg({ ...cfg, [f.key]: Number(e.target.value) })}
                />
              </FieldRow>
            ))}
            {SCORE_FIELDS.map(f => (
              <FieldRow key={f.key} label={t(`settings.kare.memory.${f.key}_label`)} hint={t(`settings.kare.memory.${f.key}_hint`)}>
                <Input
                  type="number" min={f.min} max={f.max} step={0.05} className="w-28"
                  value={cfg[f.key]}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setCfg({ ...cfg, [f.key]: Number(e.target.value) })}
                />
              </FieldRow>
            ))}
            {KARE_FIELDS.map(f => (
              <FieldRow key={f.key} label={t(`settings.kare.memory.kare_${f.key}_label`)} hint={t(`settings.kare.memory.kare_${f.key}_hint`)}>
                <Input
                  type="number" min={f.min} max={f.max} step={1} className="w-28"
                  value={cfg.kare_reminders[f.key]}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                    setCfg({ ...cfg, kare_reminders: { ...cfg.kare_reminders, [f.key]: Number(e.target.value) } })}
                />
              </FieldRow>
            ))}
          </></ExpertOnly>
        </div>
        <div className="flex items-center gap-3 mt-4">
          <Button onClick={save} disabled={ss.state === "saving"}>
            {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t("common.save")}
          </Button>
          <SaveFeedback state={ss.state} />
          {error && <span className="text-sm text-destructive">{error}</span>}
        </div>
      </CardContent>
    </Card>
  );
}
