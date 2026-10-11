import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Loader2 } from "lucide-react";
import {
  apiGetReflectionSettings, apiPutReflectionSettings,
  apiGetReflectionMeetingSettings, apiPutReflectionMeetingSettings,
  apiGetDevMeetingSettings, apiPutDevMeetingSettings,
  type ReflectionConfig, type ReflectionMeetingSettings, type DevMeetingSettings,
  api,
} from "@/services/api";
import { FieldRow, SaveFeedback, ExpertOnly } from "./shared";
import { useSaveState } from "./useSaveState";

// N17: start time of the night sequence — kaare_night_runner.py reads it on every poll.
type NightSequenceSettings = { start_time: string; default: string; timezone: string; last_slot: string | null };

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

  const [night, setNight] = useState<NightSequenceSettings | null>(null);
  const ssNight = useSaveState();

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
    api.get<NightSequenceSettings>("/api/settings/night-sequence")
      .then(r => setNight(r.data))
      .catch(() => setNight(null));
  }, []);

  const saveNight = async () => {
    if (!night) return;
    ssNight.saving();
    try { await api.put("/api/settings/night-sequence", { start_time: night.start_time }); ssNight.saved(); }
    catch { ssNight.error(); }
  };

  const saveSchedule = async () => {
    ss.saving();
    // Only this card's fields — the shared GET also returns read-only/meeting fields the PUT rejects
    try { await apiPutReflectionSettings({ enabled: cfg.enabled, interval_seconds: cfg.interval_seconds }); ss.saved(); }
    catch { ss.error(); }
  };

  const saveRefMtg = async () => {
    if (!refMtg) return;
    ssRef.saving();
    try {
      await apiPutReflectionMeetingSettings({
        max_rounds: refMtg.max_rounds,
        kare_max_tokens: refMtg.kare_max_tokens,
        miss_kare_max_tokens: refMtg.miss_kare_max_tokens,
        leder_preset: refMtg.leder_preset,
        leder_preset_custom: refMtg.leder_preset_custom,
      });
      ssRef.saved();
    }
    catch { ssRef.error(); }
  };

  const saveDevMtg = async () => {
    if (!devMtg) return;
    ssDev.saving();
    try {
      await apiPutDevMeetingSettings({
        enabled: devMtg.enabled,
        time_budget_minutes: devMtg.time_budget_minutes,
        topics_per_meeting: devMtg.topics_per_meeting,
        max_user_traces: devMtg.max_user_traces,
        round_cap_per_topic: devMtg.round_cap_per_topic,
      });
      ssDev.saved();
    }
    catch { ssDev.error(); }
  };

  const intervalMinutes = Math.round(cfg.interval_seconds / 60);

  const REF_PRESETS = ["standard", "analytisk", "utfordrende", "egendefinert"];

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

      {/* ── Night sequence start time (N17) ── */}
      {night && (
        <Card>
          <CardHeader>
            <CardTitle>{t("reflections.night_title")}</CardTitle>
            <CardDescription>{t("reflections.night_desc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="divide-y divide-border">
              <FieldRow label={t("reflections.night_start_label")}
                hint={t("reflections.night_start_hint", { tz: night.timezone })}>
                <Input type="time" step={60} className="w-32" required
                  value={night.start_time}
                  onChange={e => setNight({ ...night, start_time: e.target.value || night.default })} />
              </FieldRow>
            </div>
            {night.last_slot && (
              <div className="text-xs text-muted-foreground mt-3">
                {t("reflections.night_last_run", { date: night.last_slot })}
              </div>
            )}
            <div className="flex items-center gap-3 mt-4">
              <Button onClick={saveNight} disabled={ssNight.state === "saving"}>
                {ssNight.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {t("common.save")}
              </Button>
              <SaveFeedback state={ssNight.state} />
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── Reflection meeting ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("reflections.tab_reflection")}</CardTitle>
          <CardDescription>{t("reflections.settings_title")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ExpertOnly><>
          {loadingRef || !refMtg
            ? <div className="text-muted-foreground text-sm">{t("common.loading")}</div>
            : (
            <div className="divide-y divide-border">
              <FieldRow label={t("reflections.settings_max_rounds")}>
                <Input type="number" min={2} max={12} className="w-24"
                  value={refMtg.max_rounds}
                  onChange={e => setRefMtg({ ...refMtg, max_rounds: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_kare_tokens")} hint={t("reflections.settings_kare_tokens_hint")}>
                <Input type="number" min={1} step={1} className="w-28"
                  value={refMtg.kare_max_tokens}
                  onChange={e => setRefMtg({ ...refMtg, kare_max_tokens: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_miss_kare_tokens")} hint={t("reflections.settings_miss_kare_tokens_hint")}>
                <Input type="number" min={1} step={1} className="w-28"
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
          </></ExpertOnly>
        </CardContent>
      </Card>

      {/* ── Dev slot (P118) — replaces the old persona dev meeting; its round/token/leader
          settings are still in settings.yaml and the API but no longer shown here ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("reflections.tab_dev")}</CardTitle>
          <CardDescription>{t("reflections.settings_dev_slot_desc")}</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingDev || !devMtg
            ? <div className="text-muted-foreground text-sm">{t("common.loading")}</div>
            : (
            <div className="divide-y divide-border">
              <FieldRow label={t("reflections.settings_dev_enabled")}>
                <Switch checked={devMtg.enabled}
                  onCheckedChange={(v: boolean) => setDevMtg({ ...devMtg, enabled: v })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_topics_per_meeting")} hint={t("reflections.settings_topics_per_meeting_hint")}>
                <Input type="number" min={1} max={50} className="w-24"
                  value={devMtg.topics_per_meeting}
                  onChange={e => setDevMtg({ ...devMtg, topics_per_meeting: Number(e.target.value) })} />
              </FieldRow>
              <ExpertOnly><>
              <FieldRow label={t("reflections.settings_time_budget")} hint={t("reflections.settings_time_budget_hint")}>
                <Input type="number" min={1} max={240} className="w-24"
                  value={devMtg.time_budget_minutes}
                  onChange={e => setDevMtg({ ...devMtg, time_budget_minutes: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_max_traces")} hint={t("reflections.settings_max_traces_hint")}>
                <Input type="number" min={0} max={200} className="w-24"
                  value={devMtg.max_user_traces}
                  onChange={e => setDevMtg({ ...devMtg, max_user_traces: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_round_cap")} hint={t("reflections.settings_round_cap_hint")}>
                <Input type="number" min={1} max={12} className="w-24"
                  value={devMtg.round_cap_per_topic}
                  onChange={e => setDevMtg({ ...devMtg, round_cap_per_topic: Number(e.target.value) })} />
              </FieldRow>
              </></ExpertOnly>
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
