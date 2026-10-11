// Settings → General → «Logs and data» (Expert, P131): log write interval + data retention.
// Self-contained card so the GUI redesign (I21) can move it as one block.
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import {
  apiGetLogsAndData,
  apiPutLogsAndData,
  type LogsAndDataSettings,
  type RetentionDays,
} from "@/services/api";
import { apiErrorDetail } from "@/lib/apiError";
import { FieldRow, SaveFeedback } from "./shared";
import { useSaveState } from "./useSaveState";

const RETENTION_FIELDS: { key: keyof RetentionDays; label: string }[] = [
  { key: "interactions_retention_days", label: "settings.generelt.logs.interactions_label" },
  { key: "episodes_retention_days", label: "settings.generelt.logs.episodes_label" },
  { key: "think_cache_retention_days", label: "settings.generelt.logs.think_label" },
];

/** Amber line under a field when the chosen value is outside the recommended range (P131 D14). */
function Warning({ text }: { text: string }) {
  return <p className="text-xs mt-1.5" style={{ color: "#f5a623" }}>⚠ {text}</p>;
}

export default function LogsAndDataCard() {
  const { t } = useTranslation();
  const ss = useSaveState();
  const [info, setInfo] = useState<LogsAndDataSettings | null>(null);
  const [flush, setFlush] = useState("");
  const [days, setDays] = useState<Record<keyof RetentionDays, string>>({
    interactions_retention_days: "",
    episodes_retention_days: "",
    think_cache_retention_days: "",
  });
  const [error, setError] = useState("");

  const apply = (d: LogsAndDataSettings) => {
    setInfo(d);
    setFlush(String(d.flush_interval_seconds));
    setDays({
      interactions_retention_days: String(d.retention.interactions_retention_days),
      episodes_retention_days: String(d.retention.episodes_retention_days),
      think_cache_retention_days: String(d.retention.think_cache_retention_days),
    });
  };

  useEffect(() => {
    apiGetLogsAndData().then(apply).catch(() => setError(t("settings.generelt.logs.load_error")));
  }, [t]);

  const save = () => {
    setError("");
    ss.saving();
    apiPutLogsAndData({
      flush_interval_seconds: Number(flush),
      retention: Object.fromEntries(
        RETENTION_FIELDS.map(f => [f.key, Number(days[f.key])]),
      ) as RetentionDays,
    })
      .then(d => { apply(d); ss.saved(); })
      .catch(err => { setError(apiErrorDetail(err) ?? t("common.error")); ss.error(); });
  };

  const rec = info?.recommended;
  const flushNum = Number(flush);
  const flushWarning =
    !rec || flush === "" || Number.isNaN(flushNum) ? null
    : flushNum < rec.flush_min ? t("settings.generelt.logs.flush_warn_low")
    : flushNum > rec.flush_max ? t("settings.generelt.logs.flush_warn_high", { seconds: flushNum })
    : null;

  const retentionWarning = (value: string) => {
    const n = Number(value);
    if (!rec || value === "" || Number.isNaN(n) || n === 0) return null;
    return n < rec.retention_warn_below_days
      ? t("settings.generelt.logs.retention_warn_low", { days: n })
      : null;
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.generelt.logs.title")}</CardTitle>
        <CardDescription>{t("settings.generelt.logs.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="divide-y divide-border">
          <FieldRow label={t("settings.generelt.logs.flush_label")} hint={t("settings.generelt.logs.flush_hint")}>
            <Input
              type="number" min={0} max={info?.limits.flush_max ?? 30} step={1} value={flush}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFlush(e.target.value)}
            />
            {flushWarning && <Warning text={flushWarning} />}
          </FieldRow>
          {RETENTION_FIELDS.map(f => (
            <FieldRow key={f.key} label={t(f.label)} hint={t("settings.generelt.logs.retention_hint")}>
              <Input
                type="number" min={0} step={1} value={days[f.key]}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setDays(prev => ({ ...prev, [f.key]: e.target.value }))}
              />
              {(() => { const w = retentionWarning(days[f.key]); return w && <Warning text={w} />; })()}
            </FieldRow>
          ))}
        </div>
        <div className="flex items-center gap-3 mt-4">
          <Button onClick={save} disabled={!info || ss.state === "saving"}>
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
