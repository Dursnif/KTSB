import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import {
  apiGetReflectionSettings, apiPutReflectionSettings,
  apiGetReflectionMeetingSettings, apiPutReflectionMeetingSettings,
  apiGetDevMeetingSettings, apiPutDevMeetingSettings,
  type ReflectionConfig, type ReflectionMeetingSettings, type DevMeetingSettings,
} from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback } from "./shared";

export default function TabRefleksjon() {
  const { t } = useTranslation();

  const [cfg, setCfg] = useState<ReflectionConfig>({ enabled: false, interval_seconds: 600 });
  const [loadingCfg, setLoadingCfg] = useState(true);
  const ss = useSaveState();

  const [refMtg, setRefMtg] = useState<ReflectionMeetingSettings | null>(null);
  const [loadingRef, setLoadingRef] = useState(true);
  const ssRef = useSaveState();

  const [devMtg, setDevMtg] = useState<DevMeetingSettings | null>(null);
  const [loadingDev, setLoadingDev] = useState(true);
  const ssDev = useSaveState();

  useEffect(() => {
    apiGetReflectionSettings()
      .then(d => { setCfg(d); setLoadingCfg(false); })
      .catch(() => setLoadingCfg(false));
    apiGetReflectionMeetingSettings()
      .then(d => { setRefMtg(d); setLoadingRef(false); })
      .catch(() => setLoadingRef(false));
    apiGetDevMeetingSettings()
      .then(d => { setDevMtg(d); setLoadingDev(false); })
      .catch(() => setLoadingDev(false));
  }, []);

  const saveSchedule = async () => {
    ss.saving();
    try { await apiPutReflectionSettings(cfg); ss.saved(); }
    catch { ss.error(); }
  };

  const saveRefMtg = async () => {
    if (!refMtg) return;
    ssRef.saving();
    try { await apiPutReflectionMeetingSettings(refMtg); ssRef.saved(); }
    catch { ssRef.error(); }
  };

  const saveDevMtg = async () => {
    if (!devMtg) return;
    ssDev.saving();
    try { await apiPutDevMeetingSettings(devMtg); ssDev.saved(); }
    catch { ssDev.error(); }
  };

  const intervalMinutes = Math.round(cfg.interval_seconds / 60);

  const REF_PRESETS = ["standard", "analytisk", "utfordrende", "egendefinert"];
  const DEV_PRESETS = ["standard", "streng", "utforskende", "egendefinert"];

  if (loadingCfg) return <div className="text-muted-foreground text-sm">{t("common.loading")}</div>;

  return (
    <div className="space-y-6">

      {/* ── Schedule ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.refleksjon.title")}</CardTitle>
          <CardDescription>{t("settings.refleksjon.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.refleksjon.enabled_label")} hint={t("settings.refleksjon.enabled_hint")}>
              <div className="flex items-center gap-3 h-9">
                <button
                  type="button"
                  role="switch"
                  aria-checked={cfg.enabled}
                  onClick={() => setCfg(p => ({ ...p, enabled: !p.enabled }))}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${cfg.enabled ? "bg-green-600" : "bg-red-600"}`}
                >
                  <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${cfg.enabled ? "translate-x-4" : "translate-x-0.5"}`} />
                </button>
                <span className="text-sm text-muted-foreground">{cfg.enabled ? t("common.on") : t("common.off")}</span>
              </div>
            </FieldRow>

            <FieldRow label={t("settings.refleksjon.interval_label")} hint={t("settings.refleksjon.interval_hint")}>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  min={1}
                  value={intervalMinutes}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                    const mins = Math.max(1, Number(e.target.value));
                    setCfg(p => ({ ...p, interval_seconds: mins * 60 }));
                  }}
                  className="w-24"
                />
                <span className="text-xs text-muted-foreground">{t("settings.refleksjon.seconds_suffix", { seconds: cfg.interval_seconds })}</span>
              </div>
            </FieldRow>
          </div>

          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveSchedule} disabled={ss.state === "saving"}>
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ss.state} />
          </div>
        </CardContent>
      </Card>

      {/* ── Reflection meeting ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("reflections.tab_reflection")}</CardTitle>
          <CardDescription>{t("reflections.settings_title")}</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingRef || !refMtg
            ? <div className="text-muted-foreground text-sm">{t("common.loading")}</div>
            : (
            <div className="divide-y divide-border">
              <FieldRow label={t("reflections.settings_max_rounds")}>
                <Input type="number" min={2} max={12} className="w-24"
                  value={refMtg.max_rounds}
                  onChange={e => setRefMtg({ ...refMtg, max_rounds: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_kare_tokens")}>
                <Input type="number" min={200} max={8000} step={100} className="w-28"
                  value={refMtg.kare_max_tokens}
                  onChange={e => setRefMtg({ ...refMtg, kare_max_tokens: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_miss_kare_tokens")}>
                <Input type="number" min={100} max={4000} step={100} className="w-28"
                  value={refMtg.miss_kare_max_tokens}
                  onChange={e => setRefMtg({ ...refMtg, miss_kare_max_tokens: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_leder_preset")}>
                <div className="flex flex-wrap gap-2">
                  {REF_PRESETS.map(p => (
                    <button key={p} type="button"
                      onClick={() => setRefMtg(prev => prev ? { ...prev, leder_preset: p } : prev)}
                      className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                        refMtg.leder_preset === p
                          ? "bg-pink-500/20 text-pink-400 border-pink-500/40"
                          : "bg-muted text-muted-foreground border-border hover:border-pink-500/30"
                      }`}>
                      {t(`reflections.leder_preset_${p}`)}
                    </button>
                  ))}
                </div>
              </FieldRow>
              {refMtg.leder_preset !== "egendefinert" ? (
                <FieldRow label={t("reflections.settings_leder_default_label")}>
                  <pre className="bg-muted/30 border border-border rounded-md p-3 text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                    {refMtg.leder_preset_default || "—"}
                  </pre>
                </FieldRow>
              ) : (
                <FieldRow label={t("reflections.settings_leder_custom")}>
                  <textarea rows={8}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-mono leading-relaxed resize-y focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    placeholder={t("reflections.settings_leder_custom_ph")}
                    value={refMtg.leder_preset_custom}
                    onChange={e => setRefMtg({ ...refMtg, leder_preset_custom: e.target.value })} />
                </FieldRow>
              )}
            </div>
          )}
          {refMtg && (
            <div className="flex items-center gap-3 mt-4">
              <Button onClick={saveRefMtg} disabled={ssRef.state === "saving"}>
                {ssRef.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {t("common.save")}
              </Button>
              <SaveFeedback state={ssRef.state} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Dev meeting ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("reflections.tab_dev")}</CardTitle>
          <CardDescription>{t("reflections.settings_title")}</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingDev || !devMtg
            ? <div className="text-muted-foreground text-sm">{t("common.loading")}</div>
            : (
            <div className="divide-y divide-border">
              <FieldRow label={t("reflections.settings_max_rounds")}>
                <Input type="number" min={2} max={12} className="w-24"
                  value={devMtg.max_rounds}
                  onChange={e => setDevMtg({ ...devMtg, max_rounds: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_max_invest_rounds")}>
                <Input type="number" min={1} max={10} className="w-24"
                  value={devMtg.max_invest_rounds}
                  onChange={e => setDevMtg({ ...devMtg, max_invest_rounds: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_kare_tokens")}>
                <Input type="number" min={500} max={8000} step={100} className="w-28"
                  value={devMtg.kare_max_tokens}
                  onChange={e => setDevMtg({ ...devMtg, kare_max_tokens: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_kare_invest_tokens")}>
                <Input type="number" min={200} max={4000} step={100} className="w-28"
                  value={devMtg.kare_invest_tokens}
                  onChange={e => setDevMtg({ ...devMtg, kare_invest_tokens: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_leder_preset")}>
                <div className="flex flex-wrap gap-2">
                  {DEV_PRESETS.map(p => (
                    <button key={p} type="button"
                      onClick={() => setDevMtg(prev => prev ? { ...prev, leder_preset: p } : prev)}
                      className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                        devMtg.leder_preset === p
                          ? "bg-blue-500/20 text-blue-400 border-blue-500/40"
                          : "bg-muted text-muted-foreground border-border hover:border-blue-500/30"
                      }`}>
                      {t(`reflections.leder_preset_${p}`)}
                    </button>
                  ))}
                </div>
              </FieldRow>
              {devMtg.leder_preset !== "egendefinert" ? (
                <FieldRow label={t("reflections.settings_leder_default_label")}>
                  <pre className="bg-muted/30 border border-border rounded-md p-3 text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                    {devMtg.leder_preset_default || "—"}
                  </pre>
                </FieldRow>
              ) : (
                <FieldRow label={t("reflections.settings_leder_custom")}>
                  <textarea rows={8}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-mono leading-relaxed resize-y focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    placeholder={t("reflections.settings_leder_custom_ph")}
                    value={devMtg.leder_preset_custom}
                    onChange={e => setDevMtg({ ...devMtg, leder_preset_custom: e.target.value })} />
                </FieldRow>
              )}
            </div>
          )}
          {devMtg && (
            <div className="flex items-center gap-3 mt-4">
              <Button onClick={saveDevMtg} disabled={ssDev.state === "saving"}>
                {ssDev.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {t("common.save")}
              </Button>
              <SaveFeedback state={ssDev.state} />
            </div>
          )}
        </CardContent>
      </Card>

    </div>
  );
}
