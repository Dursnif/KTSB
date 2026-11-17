// Run-queue J18 / J18b (from P144-s31): «Hva er her?» — code counts where persons appear or disappear in the middle of a
// camera's view (a door, a corner, a blind spot). A spot seen on several days becomes one house question for admin; the
// answer is a name, «ikke et sted» or «hopp over», and a name becomes code facts for the 27B («kom fra …»). Nothing is
// counted by name. J18b: the card always shows — open questions on top (or «ingen åpne spørsmål»), the registered
// places below. The image is a daytime recording frame with no active track on that camera (never a person, never an
// opted-out one); without such a frame the camera and position are shown as text. Collapsed by default, button top
// right (G1).
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  apiAnswerPlaceQuestion, apiGetPlaceHistory, apiGetPlaceQuestions, type PlaceHistoryItem, type PlaceQuestion,
} from "@/services/api";
import SpotImage from "./SpotImage";

export default function PlaceQuestionsCard({ bare = false, onCount }: { bare?: boolean; onCount?: (n: number) => void } = {}) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.place_questions.${key}`, o);
  const [items, setItems] = useState<PlaceQuestion[]>([]);
  const [done, setDone] = useState<PlaceHistoryItem[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [names, setNames] = useState<Record<string, string>>({});
  const load = useCallback(() => {
    apiGetPlaceQuestions().then(d => setItems(d.questions)).catch(() => setItems([]));
    apiGetPlaceHistory().then(d => setDone(d.answered)).catch(() => setDone([]));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { onCount?.(items.length); }, [items.length, onCount]);
  const answer = async (q: PlaceQuestion, a: "named" | "not_a_place" | "skip") => {
    const name = (names[q.id] ?? "").trim();
    if (busy || (a === "named" && !name)) return;
    setBusy(true);
    try { await apiAnswerPlaceQuestion(q.id, a, a === "named" ? name : undefined); } catch { /* reload shows the truth */ }
    finally { setBusy(false); load(); }
  };
  const btn = { background: "none", border: "1px solid #333", borderRadius: 6, color: "#aaa", fontSize: 12,
    padding: "2px 8px", cursor: "pointer" } as const;
  const label = (d: PlaceHistoryItem) => d.status === "named" ? (d.name ?? "") : k(`h_${d.status}`);
  const body = (
        <div style={{ marginTop: 8 }}>
          {items.length === 0 && <div style={{ color: "#777", fontSize: 12 }}>{k("none_open")}</div>}
          {items.map(q => (
            <div key={q.id} style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #1a1a1a" }}>
              <div style={{ color: "#bbb", fontSize: 13, marginBottom: 6 }}>
                {k("ask", { camera: q.camera_name || q.camera, n: q.starts + q.ends, days: q.days })}
              </div>
              <SpotImage s={q} width={360} k={k} />
              <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
                <input type="text" maxLength={60} value={names[q.id] ?? ""} placeholder={k("placeholder")} disabled={busy}
                  onChange={e => setNames(p => ({ ...p, [q.id]: e.target.value }))}
                  style={{ background: "#0b0b0b", border: "1px solid #333", borderRadius: 6, color: "#ddd", fontSize: 13,
                    padding: "4px 8px", minWidth: 180 }} />
                <button type="button" style={btn} disabled={busy || !(names[q.id] ?? "").trim()}
                  onClick={() => answer(q, "named")}>{k("a_named")}</button>
                <button type="button" style={btn} disabled={busy} onClick={() => answer(q, "not_a_place")}>{k("a_not_a_place")}</button>
                <button type="button" style={btn} disabled={busy} onClick={() => answer(q, "skip")}>{k("a_skip")}</button>
              </div>
            </div>
          ))}
          <div style={{ color: "#999", fontSize: 12, marginTop: 14, paddingTop: 8, borderTop: "1px solid #1a1a1a" }}>{k("history")}</div>
          {done.length === 0 && <div style={{ color: "#777", fontSize: 12, marginTop: 4 }}>{k("history_empty")}</div>}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 6 }}>
            {done.map(d => (
              <div key={d.id} style={{ width: 160 }}>
                <SpotImage s={d} width={160} k={k} />
                <div style={{ color: "#bbb", fontSize: 12, marginTop: 4 }}>{label(d)}</div>
                <div style={{ color: "#777", fontSize: 11 }}>{d.camera_name || d.camera}</div>
              </div>
            ))}
          </div>
        </div>
  );
  if (bare) return body;

  return (
    <div style={{ background: "#111", border: "1px solid #1e1e1e", borderRadius: 8, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ color: "#ccc", fontSize: 14, fontWeight: 500 }}>
          {k("title")} <span style={{ color: "#888", fontWeight: 400 }}>({items.length})</span>
        </div>
        <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open} style={btn}>{open ? "▾" : "▸"}</button>
      </div>
      {open && body}
    </div>
  );
}
