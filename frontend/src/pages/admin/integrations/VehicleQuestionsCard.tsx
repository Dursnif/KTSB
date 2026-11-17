// P150 6b (2026-11-14: «menneske og frigate er bygget.. du skal replikere» · «få med gui delen hvem/hva/hvor i frigate
// fanen»): the vehicle part of the «Hvem / Hva / Hvor» card. Same shape as the place and face parts: open questions on
// top — an unknown vehicle seen on several days, shown by its picture and its pseudonymous id (never the plate text) —
// answered with a household name + class or «hopp over»; below, the vehicles already named or classified, and the ones
// Frigate knows by name. Admin only, like Hva and Hvor.
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import EventSnapshot from "@/components/EventSnapshot";
import {
  apiGetVehicleQuestions, apiGetVehicles, apiPutVehicle, apiPutVehicleSettings, apiSkipVehicleQuestion,
  type VehicleList, type VehicleRow,
} from "@/services/api";

const btn = { background: "none", border: "1px solid #333", borderRadius: 6, color: "#aaa", fontSize: 12,
  padding: "2px 8px", cursor: "pointer" } as const;
const field = { background: "#0b0b0b", border: "1px solid #333", borderRadius: 6, color: "#ddd", fontSize: 13,
  padding: "4px 8px" } as const;

export default function VehicleQuestionsCard({ onCount }: { onCount?: (n: number) => void }) {
  const { t, i18n } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.vehicle_questions.${key}`, o);
  const [items, setItems] = useState<VehicleRow[]>([]);
  const [list, setList] = useState<VehicleList | null>(null);
  const [busy, setBusy] = useState(false);
  const [names, setNames] = useState<Record<string, string>>({});
  const [classes, setClasses] = useState<Record<string, string>>({});
  const [shown, setShown] = useState<string | null>(null);
  const [days, setDays] = useState("");
  const load = useCallback(() => {
    apiGetVehicleQuestions().then(d => setItems(d.questions)).catch(() => setItems([]));
    apiGetVehicles().then(d => { setList(d); setDays(String(d.question_days)); }).catch(() => setList(null));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { onCount?.(items.length); }, [items.length, onCount]);
  const save = async (v: VehicleRow) => {
    const name = (names[v.vehicle_id] ?? "").trim();
    const cls = classes[v.vehicle_id] ?? "unclassified";
    if (busy || (!name && cls === "unclassified")) return;
    setBusy(true);
    try { await apiPutVehicle(v.vehicle_id, cls, name); } catch { /* reload shows the truth */ }
    finally { setBusy(false); load(); }
  };
  const skip = async (v: VehicleRow) => {
    if (busy) return;
    setBusy(true);
    try { await apiSkipVehicleQuestion(v.vehicle_id); } catch { /* reload */ } finally { setBusy(false); load(); }
  };
  const saveDays = async () => {
    const n = Number(days);
    if (!Number.isInteger(n) || n < 1 || n > 365) return;
    try { await apiPutVehicleSettings(n); } catch { /* reload */ } finally { load(); }
  };
  const when = (ts: number) => new Date(ts * 1000).toLocaleDateString(i18n.language);
  const short = (id: string) => id.replace("vehicle-", "V-").toUpperCase();
  const done = (list?.vehicles ?? []).filter(v => v.name || v.class !== "unclassified");
  const clsOptions = ["unclassified", ...(list?.classes ?? [])];

  return (
    <div style={{ marginTop: 8 }}>
      {items.length === 0 && <div style={{ color: "#777", fontSize: 12 }}>{k("none_open")}</div>}
      {items.map(v => (
        <div key={v.vehicle_id} style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #1a1a1a" }}>
          <div style={{ color: "#bbb", fontSize: 13, marginBottom: 6, cursor: "pointer" }}
            onClick={() => setShown(s => (s === v.vehicle_id ? null : v.vehicle_id))}>
            {k("ask", { id: short(v.vehicle_id), days: v.days_seen, first: when(v.first_seen), last: when(v.last_seen) })}
          </div>
          {shown === v.vehicle_id && <EventSnapshot eventId={v.newest_event_id} />}
          <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
            <input type="text" maxLength={100} value={names[v.vehicle_id] ?? ""} placeholder={k("placeholder")}
              disabled={busy} style={{ ...field, minWidth: 180 }}
              onChange={e => setNames(p => ({ ...p, [v.vehicle_id]: e.target.value }))} />
            <select value={classes[v.vehicle_id] ?? "unclassified"} disabled={busy} style={field}
              onChange={e => setClasses(p => ({ ...p, [v.vehicle_id]: e.target.value }))}>
              {clsOptions.map(c => <option key={c} value={c}>{t(`integrations.frigate_faces.class.${c}`)}</option>)}
            </select>
            <button type="button" style={btn} disabled={busy} onClick={() => save(v)}>{k("a_save")}</button>
            <button type="button" style={btn} disabled={busy} onClick={() => skip(v)}>{k("a_skip")}</button>
          </div>
        </div>
      ))}
      <div style={{ color: "#999", fontSize: 12, marginTop: 14, paddingTop: 8, borderTop: "1px solid #1a1a1a" }}>{k("history")}</div>
      {done.length === 0 && (list?.known ?? []).length === 0 &&
        <div style={{ color: "#777", fontSize: 12, marginTop: 4 }}>{k("history_empty")}</div>}
      {done.map(v => (
        <div key={v.vehicle_id} style={{ color: "#bbb", fontSize: 12, marginTop: 4 }}>
          {v.name || short(v.vehicle_id)} · {t(`integrations.frigate_faces.class.${v.class}`)} · {k("days_seen", { n: v.days_seen })}
        </div>
      ))}
      {(list?.known ?? []).map(kv => (
        <div key={kv.name} style={{ color: "#888", fontSize: 12, marginTop: 4 }}>{kv.name} · {k("known_in_frigate")}</div>
      ))}
      <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ color: "#888", fontSize: 12 }}>{k("days_label")}</span>
        <input type="number" min={1} max={365} value={days} onChange={e => setDays(e.target.value)}
          style={{ ...field, width: 70 }} />
        <button type="button" style={btn} onClick={saveDays}>{t("common.save")}</button>
      </div>
      <div style={{ color: "#666", fontSize: 11, marginTop: 6 }}>{k("privacy_note")}</div>
    </div>
  );
}
