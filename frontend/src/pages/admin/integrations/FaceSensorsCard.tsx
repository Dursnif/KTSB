// Face sensors (P144-s29b, 2026-11-05): every face sensor — Frigate's own face recognition and each detector a
// provider (Double Take) has reported — as one row admin can set: «When this sensor alone says a name, it is …» →
// Auto (KTSB learns) or a word from KTSB's certainty ladder, or «does not count». Words, never percentages (the model
// and the user see words; code turns them into numbers). Auto shows honestly that KTSB is not learning yet, with room
// for «lærer: N av ~5 bekreftelser» once the weight system exists. Collapsed by default (P144 GUI list G1); the 7-day
// count line per sensor is folded too. Basic (admin pages are admin-only). Detector names come from the provider —
// none is fixed here (rule 24).
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ChevronDown, Loader2 } from "lucide-react";
import {
  apiGetFaceSensors, apiPutFrigateUserPipe, type FaceSensorRow, type FaceSensorsSettings, type SensorTrust,
} from "@/services/api";
import { SaveFeedback } from "../settings/shared";
import { useSaveState } from "../settings/useSaveState";

const WORDS: SensorTrust[] = ["very_likely", "likely", "uncertain", "unlikely", "off"];
const NOT_SEEN_DAYS = 30;
type Row = { label: string; choice: "auto" | SensorTrust; learn: boolean };

export default function FaceSensorsCard() {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.face_sensors.${key}`, o);
  const word = (w: string) => w === "off" ? k("off") : t(`integrations.frigate_episodes.cert_${w}`);
  const [open, setOpen] = useState(false);
  const [sensors, setSensors] = useState<FaceSensorRow[] | null>(null);
  const [days, setDays] = useState(7);
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [countsOpen, setCountsOpen] = useState<Record<string, boolean>>({});
  const [nowS, setNowS] = useState(0);
  const [learnAll, setLearnAll] = useState(true);  // J2b: «Auto bruker det KTSB har lært» for all sensors
  const ss = useSaveState();

  const load = useCallback(() => {
    apiGetFaceSensors().then(d => {
      setNowS(Date.now() / 1000);
      setSensors(d.sensors);
      setDays(d.count_days);
      setLearnAll(d.auto_learning !== false);
      setRows(Object.fromEntries(d.sensors.map(s => [s.sensor, {
        label: s.label, choice: s.mode === "admin" ? s.trust : "auto", learn: s.auto_learning !== false }])));
    }).catch(() => setSensors([]));
  }, []);
  useEffect(() => { if (open && sensors === null) load(); }, [open, sensors, load]);

  const save = async () => {
    if (!sensors) return;
    ss.saving();
    const one = (s: FaceSensorRow) => {
      const r = rows[s.sensor];
      // Admin's last word is kept while the mode is auto, so switching back restores it.
      return { mode: r.choice === "auto" ? "auto" as const : "admin" as const,
        trust: r.choice === "auto" ? s.trust : r.choice, label: r.label.trim(), auto_learning: r.learn };
    };
    const payload: FaceSensorsSettings = {
      auto_learning: learnAll,
      frigate: one(sensors.find(s => s.sensor === "frigate")!),
      detectors: Object.fromEntries(sensors.filter(s => s.sensor !== "frigate").map(s => [s.sensor, one(s)])),
    };
    try {
      await apiPutFrigateUserPipe({ face_sensors: payload });
      ss.saved();
      load();
    } catch { ss.error(); }
  };

  return (
    <Card>
      <CardHeader>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, cursor: "pointer" }}
          onClick={() => setOpen(o => !o)}>
          <div style={{ minWidth: 0 }}>
            <CardTitle>{k("title")}</CardTitle>
            <CardDescription>{k("description")}</CardDescription>
          </div>
          <ChevronDown size={18} aria-label={open ? k("fold") : k("unfold")}
            style={{ color: "#888", flexShrink: 0, transition: "transform 0.15s",
              transform: open ? "rotate(180deg)" : "none" }} />
        </div>
      </CardHeader>
      {open && (
        <CardContent>
          {sensors === null && <Loader2 className="h-4 w-4 animate-spin" />}
          {sensors !== null && (
            <>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "#ccc", marginBottom: 6 }}>
                <input type="checkbox" checked={learnAll} onChange={e => setLearnAll(e.target.checked)} />
                {k("learn_all")}
              </label>
              <div style={{ fontSize: 12, color: "#777", marginBottom: 10 }}>{k("learn_all_hint", { n: 5 })}</div>
              <div style={{ fontSize: 13, color: "#ccc", marginBottom: 8 }}>{k("question")}</div>
              <div className="divide-y divide-border">
                {sensors.map(s => {
                  const r = rows[s.sensor] ?? { label: s.label, choice: "auto" as const, learn: true };
                  const isFrigate = s.sensor === "frigate";
                  const stale = !isFrigate && (!s.last_seen_ts || nowS - s.last_seen_ts > NOT_SEEN_DAYS * 86400);
                  const title = isFrigate ? k("frigate") : (r.label || s.sensor);
                  return (
                    <div key={s.sensor} style={{ padding: "10px 0", opacity: stale ? 0.6 : 1 }}>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center",
                        justifyContent: "space-between" }}>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontSize: 14, fontWeight: 600 }}>{title}</div>
                          <div style={{ fontSize: 12, color: "#888" }}>
                            {isFrigate ? k("source_frigate") : k("source_detector", { name: s.sensor })}
                            {stale ? ` · ${k("not_seen", { days: NOT_SEEN_DAYS })}` : ""}
                          </div>
                        </div>
                        <div className="flex gap-2" style={{ flexWrap: "wrap", alignItems: "center" }}>
                          {!isFrigate && (
                            <Input value={r.label} className="w-36" placeholder={k("label_ph")}
                              aria-label={k("label")}
                              onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                                setRows(p => ({ ...p, [s.sensor]: { ...r, label: e.target.value } }))} />
                          )}
                          <select value={r.choice} aria-label={k("choice", { name: title })}
                            onChange={e => setRows(p => ({ ...p, [s.sensor]: { ...r, choice: e.target.value as Row["choice"] } }))}
                            style={{ background: "#1a1a1a", border: "1px solid #333", borderRadius: 6, color: "#ccc",
                              padding: "6px 8px", fontSize: 13 }}>
                            <option value="auto">{k("auto")}</option>
                            {WORDS.map(w => <option key={w} value={w}>{word(w)}</option>)}
                          </select>
                        </div>
                      </div>
                      {/* Auto: what KTSB has learned from «Var det deg?» answers (P149, G13) — shown, not acted on yet */}
                      <div style={{ fontSize: 12, color: "#888", marginTop: 4 }}>
                        {k("learned")}: {s.learned
                          ? k("learning", { n: s.learned, goal: s.learned_goal ?? 5 })
                            + (s.learned_word ? ` · ${k("learned_word", { word: word(s.learned_word) })}` : "")
                          : k("learned_none")}
                        {r.choice === "auto"
                          ? ` · ${s.acting ? k("auto_acting", { word: word(s.word) }) : k("auto_now", { word: word("uncertain"), n: s.learned_goal ?? 5 })}`
                          : ""}
                      </div>
                      {r.choice === "auto" && (
                        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#888", marginTop: 4 }}>
                          <input type="checkbox" checked={r.learn} disabled={!learnAll}
                            onChange={e => setRows(p => ({ ...p, [s.sensor]: { ...r, learn: e.target.checked } }))} />
                          {k("learn_one")}
                        </label>
                      )}
                      <button type="button" onClick={() => setCountsOpen(p => ({ ...p, [s.sensor]: !p[s.sensor] }))}
                        style={{ fontSize: 12, color: "#888", background: "none", border: "none", padding: 0,
                          marginTop: 4, cursor: "pointer" }}>
                        {countsOpen[s.sensor] ? "▾" : "▸"} {k("counts_title", { days })}
                      </button>
                      {countsOpen[s.sensor] && (
                        <div style={{ fontSize: 12, color: "#aaa", marginTop: 2 }}>
                          {k("counts", { votes: s.counts.votes, agree: s.counts.agree, disagree: s.counts.disagree })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              {sensors.length === 1 && (
                <div style={{ fontSize: 12, color: "#888", marginTop: 6 }}>{k("no_detectors")}</div>
              )}
              <div style={{ fontSize: 12, color: "#777", marginTop: 10 }}>{k("verified_note")}</div>
              <div className="flex items-center gap-3 mt-4">
                <Button onClick={save} disabled={ss.state === "saving"}>
                  {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {t("common.save")}
                </Button>
                <SaveFeedback state={ss.state} />
              </div>
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}
