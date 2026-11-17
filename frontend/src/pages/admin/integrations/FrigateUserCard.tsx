// The Frigate user in shadow mode (P144-s06a): checks Frigate's face claims against the image and remembers what the
// cameras saw — no alerts, nothing Kåre says changes yet. Basic mode shows only the switch (VISION thread 7); gaps and
// thresholds are Expert fields. Self-contained so it drops into Integrations → Frigate with one line and moves whole (I21).
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { apiGetFrigateUserPipe, apiPutFrigateUserPipe, type FrigateUserPipeSettings } from "@/services/api";
import { ExpertOnly, SaveFeedback } from "../settings/shared";
import { useSaveState } from "../settings/useSaveState";

type NumKey = "episode_gap_s" | "episode_min_movement" | "visit_gap_s" | "visit_fold_interval_s" | "l0_min_face_px"
  | "l0_min_claim_score";
type ConcludeNumKey = "idle_gate_s" | "max_attempts" | "analyse_per_10min" | "stuck_after_min";

const FIELDS: { key: NumKey; min: number; max: number; step?: number }[] = [
  { key: "episode_gap_s", min: 10, max: 3600 },
  { key: "episode_min_movement", min: 0.01, max: 1, step: 0.01 },
  { key: "visit_gap_s", min: 60, max: 86400 },
  { key: "visit_fold_interval_s", min: 30, max: 3600 },
  { key: "l0_min_face_px", min: 0, max: 1000000 },
  { key: "l0_min_claim_score", min: 0, max: 1, step: 0.05 },
];
// Same role set as the camera cards (Cameras.tsx PREDEFINED_ROLES); a custom role already chosen is shown too.
// P150: the day tiers are gone — frigate.db has a size limit in «Lagringsgrenser» (Cameras.tsx), oldest first.
const PLATE_KEEP = ["7_days", "always"] as const;
const CONCLUDE_FIELDS: { key: ConcludeNumKey; min: number; max: number }[] = [
  { key: "idle_gate_s", min: 0, max: 3600 },
  { key: "max_attempts", min: 1, max: 10 },
  { key: "analyse_per_10min", min: 1, max: 60 },  // P144-s24: the episode view's «Analyser» button
  { key: "stuck_after_min", min: 5, max: 1440 },  // N40: when an episode «henger» (shows «Analyser på nytt»)
];

function NumberField({ label, hint, value, min, max, step, onSave }: {
  label: string; hint: string; value: number; min: number; max: number; step?: number;
  onSave: (n: number) => Promise<void>;
}) {
  const [text, setText] = useState(String(value));
  const ss = useSaveState();
  const save = async () => {
    const n = Number(text);
    if (text.trim() === "" || !Number.isFinite(n) || n < min || n > max) { ss.error(); return; }
    if (n === value) return;
    ss.saving();
    try { await onSave(n); ss.saved(); } catch { ss.error(); }
  };
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, color: "#ccc", flex: "1 1 180px" }}>{label}</span>
        <Input
          type="number" min={min} max={max} step={step ?? 1} value={text} className="w-28"
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setText(e.target.value)}
          onBlur={save}
        />
        <SaveFeedback state={ss.state} />
      </div>
      <div style={{ fontSize: 12, color: "#777", marginTop: 2 }}>{hint}</div>
    </div>
  );
}

// Two-way choice as chips.
function ChoiceField<V extends string>({ value, options, label, hint, optionName, onSave }: {
  value: V; options: readonly V[]; label: string; hint: string; optionName: (v: V) => string;
  onSave: (v: V) => Promise<void>;
}) {
  const ss = useSaveState();
  const pick = async (v: V) => {
    if (v === value) return;
    ss.saving();
    try { await onSave(v); ss.saved(); } catch { ss.error(); }
  };
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 13, color: "#ccc" }}>{label}</span>
        <SaveFeedback state={ss.state} />
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }} role="radiogroup" aria-label={label}>
        {options.map(v => {
          const on = v === value;
          return (
            <button
              key={v} type="button" role="radio" aria-checked={on} onClick={() => pick(v)}
              style={{
                fontSize: 12, padding: "3px 10px", borderRadius: 12, cursor: "pointer",
                border: `1px solid ${on ? "#16a34a" : "#444"}`, background: on ? "#14532d" : "transparent",
                color: on ? "#dcfce7" : "#aaa",
              }}
            >{optionName(v)}</button>
          );
        })}
      </div>
      <div style={{ fontSize: 12, color: "#777", marginTop: 2 }}>{hint}</div>
    </div>
  );
}

function SwitchField({ value, label, hint, onSave }: {
  value: boolean; label: string; hint: string; onSave: (on: boolean) => Promise<void>;
}) {
  const ss = useSaveState();
  const toggle = async (on: boolean) => {
    ss.saving();
    try { await onSave(on); ss.saved(); } catch { ss.error(); }
  };
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, color: "#ccc", flex: "1 1 180px" }}>{label}</span>
        <SaveFeedback state={ss.state} />
        <Switch checked={value} onCheckedChange={toggle} aria-label={label} />
      </div>
      <div style={{ fontSize: 12, color: "#777", marginTop: 2 }}>{hint}</div>
    </div>
  );
}

export default function FrigateUserCard() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<FrigateUserPipeSettings | null>(null);
  const ssSwitch = useSaveState();

  useEffect(() => { apiGetFrigateUserPipe().then(setCfg).catch(() => {}); }, []);
  if (!cfg) return null;

  const toggle = async (on: boolean) => {
    ssSwitch.saving();
    try { setCfg(await apiPutFrigateUserPipe({ enabled: on })); ssSwitch.saved(); } catch { ssSwitch.error(); }
  };
  const k = (key: string) => t(`integrations.frigate_user.${key}`);

  return (
    <Card>
      <CardHeader>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <CardTitle>{k("title")}</CardTitle>
            <CardDescription>{k("description")}</CardDescription>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <SaveFeedback state={ssSwitch.state} />
            <Switch checked={cfg.enabled} onCheckedChange={toggle} aria-label={k("switch_label")} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div style={{ fontSize: 12, color: "#777" }}>{cfg.enabled ? k("state_on") : k("state_off")}</div>
        {/* P144-s27b: a privacy switch, so in Basic; placed provisionally (P144 v2 GUI list) */}
        {cfg.expressions !== undefined && (
          <SwitchField
            value={cfg.expressions} label={k("expressions_label")} hint={k("expressions_hint")}
            onSave={async on => setCfg(await apiPutFrigateUserPipe({ expressions: on }))}
          />
        )}
        {/* J11 (P149 D12): one question from each user's own list now and then in chat, never voice; provisional */}
        {cfg.questions !== undefined && (
          <SwitchField
            value={cfg.questions.chat} label={k("questions_chat_label")} hint={k("questions_chat_hint")}
            onSave={async on => setCfg(await apiPutFrigateUserPipe({ questions: { chat: on } }))}
          />
        )}
        {/* J20 (#28): confirmed-wrong rules from «Hva er dette?» — off = shadow; provisional (GUI list) */}
        {cfg.questions?.wrong_rules !== undefined && (
          <SwitchField
            value={cfg.questions.wrong_rules} label={k("questions_wrong_rules_label")} hint={k("questions_wrong_rules_hint")}
            onSave={async on => setCfg(await apiPutFrigateUserPipe({ questions: { wrong_rules: on } }))}
          />
        )}
        <ExpertOnly>
          <div className="space-y-3" style={{ paddingTop: 6, borderTop: "1px solid #222" }}>
            {/* «Kan lage episoder» moved onto each camera card (P144-s14); hidden until the API knows the key. */}
            {cfg.episode_weather !== undefined && (
              <SwitchField
                value={cfg.episode_weather} label={k("episode_weather_label")} hint={k("episode_weather_hint")}
                onSave={async on => setCfg(await apiPutFrigateUserPipe({ episode_weather: on }))}
              />
            )}
            {FIELDS.map(f => (
              <NumberField
                key={f.key} label={k(`${f.key}_label`)} hint={k(`${f.key}_hint`)} value={cfg[f.key]}
                min={f.min} max={f.max} step={f.step}
                onSave={async n => setCfg(await apiPutFrigateUserPipe({ [f.key]: n }))}
              />
            ))}
            {/* Hidden until the API knows the keys (an API not yet restarted after P150). */}
            {cfg.frigate_db_max_mb !== undefined && <>
            <div style={{ fontSize: 13, color: "#aaa", paddingTop: 4 }}>{k("retention_heading")}</div>
            <SwitchField
              value={cfg.retention_dry_run} label={k("retention_dry_run_label")} hint={k("retention_dry_run_hint")}
              onSave={async on => setCfg(await apiPutFrigateUserPipe({ retention_dry_run: on }))}
            />
            <ChoiceField
              value={cfg.keep_unknown_plates} options={PLATE_KEEP} label={k("retention_keep_unknown_plates_label")}
              hint={k("retention_keep_unknown_plates_hint")} optionName={v => k(`retention_plates_${v}`)}
              onSave={async v => setCfg(await apiPutFrigateUserPipe({ keep_unknown_plates: v }))}
            />
            <div style={{ fontSize: 12, color: "#777" }}>{k("retention_footer")}</div>
            </>}
            <div style={{ fontSize: 13, color: "#aaa", paddingTop: 4 }}>{k("conclude_heading")}</div>
            {CONCLUDE_FIELDS.map(f => (
              <NumberField
                key={f.key} label={k(`conclude_${f.key}_label`)} hint={k(`conclude_${f.key}_hint`)}
                value={cfg.conclude[f.key] ?? 2} min={f.min} max={f.max}
                onSave={async n => setCfg(await apiPutFrigateUserPipe({ conclude: { [f.key]: n } }))}
              />
            ))}
          </div>
        </ExpertOnly>
      </CardContent>
    </Card>
  );
}
