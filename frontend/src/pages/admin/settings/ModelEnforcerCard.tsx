import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import {
  apiGetModelEnforcer, apiPutModelEnforcer,
  type EnforcerMode, type ModelEnforcerStatus,
} from "@/services/api";
import { SaveFeedback } from "./shared";
import { useSaveState } from "./useSaveState";

// Model guard (P142 D7): status view + per-detector mode for the enforcer box between KTSB and
// the LLM. Lives in the Expert-only Agent settings tab.

const MODE_COLORS: Record<EnforcerMode, string> = { off: "#636e72", shadow: "#fdcb6e", on: "#00b894" };

function ModeSwitch({ value, onChange, labels }: {
  value: EnforcerMode;
  onChange: (m: EnforcerMode) => void;
  labels: Record<EnforcerMode, string>;
}) {
  return (
    <div className="inline-flex rounded-md border border-[#333] overflow-hidden flex-shrink-0">
      {(["off", "shadow", "on"] as EnforcerMode[]).map(m => {
        const active = m === value;
        return (
          <button
            key={m}
            type="button"
            onClick={() => onChange(m)}
            className="px-2.5 py-1 text-xs transition-colors"
            style={active
              ? { background: MODE_COLORS[m], color: "#111", fontWeight: 600 }
              : { background: "transparent", color: "#aaa" }}
          >
            {labels[m]}
          </button>
        );
      })}
    </div>
  );
}

export default function ModelEnforcerCard() {
  const { t, i18n } = useTranslation();
  const ss = useSaveState();
  const [open, setOpen] = useState(true);
  const [status, setStatus] = useState<ModelEnforcerStatus | null>(null);
  const [modes, setModes] = useState<Record<string, EnforcerMode>>({});
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    apiGetModelEnforcer()
      .then(s => {
        setStatus(s);
        setModes(Object.fromEntries(
          s.detectors.flatMap(d => d.profiles.map(p => [`${p.profile}:${d.name}`, p.mode])),
        ));
      })
      .catch(() => setFailed(true));
  }, []);

  const k = (key: string, opts?: Record<string, unknown>) => t(`settings.agenter.enforcer.${key}`, opts);
  const modeLabels: Record<EnforcerMode, string> = { off: k("modes.off"), shadow: k("modes.shadow"), on: k("modes.on") };
  const when = (ts: string) => {
    try { return new Date(ts).toLocaleString(i18n.language, { dateStyle: "short", timeStyle: "short" }); }
    catch { return ts; }
  };

  const save = async () => {
    ss.saving();
    try { await apiPutModelEnforcer(modes); ss.saved(); }
    catch { ss.error(); }
  };

  return (
    <Card className="bg-[#1a1a1a] border-[#333]">
      <CardHeader className="cursor-pointer select-none pb-3" onClick={() => setOpen(o => !o)}>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-sm font-semibold text-white">{k("label")}</CardTitle>
            <CardDescription className="text-xs text-muted-foreground">{k("description")}</CardDescription>
          </div>
          {open ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
        </div>
      </CardHeader>
      {open && (
        <CardContent className="space-y-4 pt-0">
          <p className="text-xs text-muted-foreground">{k("intro")}</p>
          {failed && <p className="text-xs text-destructive">{t("common.error")}</p>}
          {!status && !failed && <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />}
          {status && (
            <>
              <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{k("period", { days: status.stats.days })}</p>
              <div className="divide-y divide-[#2a2a2a]">
                {status.detectors.map(d => {
                  const st = status.stats.per_detector[d.name];
                  const counts = st
                    ? ([["retry", st.retry], ["shadow", st.shadow], ["tool_correction", st.tool_correction]] as [string, number][])
                        .filter(([, n]) => n > 0)
                    : [];
                  const outcomes = st ? Object.entries(st.outcomes) : [];
                  return (
                    <div key={d.name} className="py-3 flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0 flex-1" style={{ minWidth: 220 }}>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium text-white">{k(`detectors.${d.name}.label`)}</span>
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#2a2a2a] text-muted-foreground">{k(`kinds.${d.kind}`)}</span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">{k(`detectors.${d.name}.desc`)}</p>
                        {(counts.length > 0 || outcomes.length > 0) && (
                          <p className="text-xs mt-1" style={{ color: "#b2bec3" }}>
                            {[...counts.map(([c, n]) => `${n} ${k(`counts.${c}`)}`),
                              ...outcomes.map(([o, n]) => `${n} ${k(`outcomes.${o}`)}`)].join(" · ")}
                          </p>
                        )}
                      </div>
                      <div className="flex flex-col items-end gap-1.5">
                        {d.profiles.map(p => {
                          const key = `${p.profile}:${d.name}`;
                          const value = modes[key] ?? p.mode;
                          return (
                            <div key={key} className="flex items-center gap-2">
                              <span className="text-[11px] text-muted-foreground text-right" style={{ minWidth: 84 }}>
                                {k(`profiles.${p.profile}`)}
                                {value === p.default_mode && <span className="block text-[10px] opacity-70">{k("default_badge")}</span>}
                              </span>
                              <ModeSwitch value={value} labels={modeLabels}
                                onChange={m => setModes(prev => ({ ...prev, [key]: m }))} />
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button size="sm" onClick={save} disabled={ss.state === "saving"}>
                  {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {k("save")}
                </Button>
                <SaveFeedback state={ss.state} />
                <span className="text-xs text-muted-foreground">{k("save_hint")}</span>
              </div>
              <Separator className="bg-[#333]" />
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">{k("recent_title")}</p>
                {status.stats.recent.length === 0 && <p className="text-xs text-muted-foreground">{k("recent_empty")}</p>}
                <div className="space-y-1">
                  {status.stats.recent.map((h, i) => (
                    <div key={`${h.ts}-${i}`} className="text-xs flex flex-wrap gap-x-2 gap-y-0.5">
                      <span className="text-muted-foreground font-mono">{when(h.ts)}</span>
                      <span className="text-white">{k(`detectors.${h.detector}.label`, { defaultValue: h.detector })}</span>
                      <span style={{ color: h.event === "shadow" ? MODE_COLORS.shadow : "#b2bec3" }}>
                        {k(`events.${h.event}`, { defaultValue: h.event })}
                        {h.outcome ? ` → ${k(`outcomes.${h.outcome}`, { defaultValue: h.outcome })}` : ""}
                      </span>
                      {h.phrase && <span className="text-muted-foreground break-all">«{h.phrase}»</span>}
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}
