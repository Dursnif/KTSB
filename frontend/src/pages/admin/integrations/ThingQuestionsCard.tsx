// P144 R139 / run-queue J12: «Er dette det samme?» — code proposes that a stationary thing seen by two cameras (same
// label, still for days, moved at the same time) is one thing. A house question, so admin only (#9). One image from
// each camera; «ja» links them, «nei» keeps them apart, «hopp over» hides the pair. Code's own evidence never makes it
// «known» — only this answer does. J18b: the card always shows — open questions on top (or «ingen åpne
// spørsmål»), a short history of the answers below (small images of the things, same admin snapshot route). Collapsed
// by default, button top right (G1).
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  api, apiAnswerThingQuestion, apiGetThingHistory, apiGetThingQuestions, type ThingHistoryItem, type ThingQuestion,
} from "@/services/api";

function Snap({ eventId, width = 240 }: { eventId: string | null; width?: number }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let u: string | null = null;
    if (!eventId) return;
    api.get(`/api/normalcy/snapshot/${eventId}`, { responseType: "blob" })
      .then(r => { u = URL.createObjectURL(r.data); setUrl(u); }).catch(() => setUrl(null));
    return () => { if (u) URL.revokeObjectURL(u); };
  }, [eventId]);
  return url ? <img src={url} alt="" style={{ width: "100%", maxWidth: width, borderRadius: 6 }} />
    : <div style={{ width, height: Math.round(width * 0.56), background: "#1a1a1a", borderRadius: 6 }} />;
}

export default function ThingQuestionsCard({ bare = false, onCount }: { bare?: boolean; onCount?: (n: number) => void } = {}) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.thing_questions.${key}`, o);
  const [items, setItems] = useState<ThingQuestion[]>([]);
  const [done, setDone] = useState<ThingHistoryItem[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    apiGetThingQuestions().then(d => setItems(d.questions)).catch(() => setItems([]));
    apiGetThingHistory().then(d => setDone(d.answered)).catch(() => setDone([]));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { onCount?.(items.length); }, [items.length, onCount]);
  const answer = async (q: ThingQuestion, a: "yes" | "no" | "skip") => {
    if (busy) return;
    setBusy(true);
    try { await apiAnswerThingQuestion(q.id, a); } catch { /* reload shows the truth */ }
    finally { setBusy(false); load(); }
  };
  const body = (
        <div style={{ marginTop: 8 }}>
          {items.length === 0 && <div style={{ color: "#777", fontSize: 12 }}>{k("none_open")}</div>}
          {items.map(q => (
            <div key={q.id} style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #1a1a1a" }}>
              <div style={{ color: "#bbb", fontSize: 13, marginBottom: 6 }}>
                {k("ask", { label: q.label, a: q.cameras[0], b: q.cameras[1] })}
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Snap eventId={q.events[0]} /><Snap eventId={q.events[1]} />
              </div>
              <div style={{ display: "flex", gap: 12, marginTop: 6 }}>
                {(["yes", "no", "skip"] as const).map(a => (
                  <label key={a} style={{ display: "flex", alignItems: "center", gap: 4, color: "#999", fontSize: 12 }}>
                    <input type="checkbox" checked={false} disabled={busy} onChange={() => answer(q, a)} />{k(`a_${a}`)}
                  </label>
                ))}
              </div>
            </div>
          ))}
          <div style={{ color: "#999", fontSize: 12, marginTop: 14, paddingTop: 8, borderTop: "1px solid #1a1a1a" }}>{k("history")}</div>
          {done.length === 0 && <div style={{ color: "#777", fontSize: 12, marginTop: 4 }}>{k("history_empty")}</div>}
          {done.map(d => (
            <div key={d.id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 6 }}>
              <div style={{ width: 96 }}><Snap eventId={d.events[0]} width={96} /></div>
              <div style={{ width: 96 }}><Snap eventId={d.events[1]} width={96} /></div>
              <div style={{ color: "#bbb", fontSize: 12 }}>
                {k("h_answer", { label: d.label, a: d.cameras[0], b: d.cameras[1], answer: k(`h_${d.answer}`) })}
              </div>
            </div>
          ))}
        </div>
  );
  if (bare) return body;

  return (
    <div style={{ background: "#111", border: "1px solid #1e1e1e", borderRadius: 8, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ color: "#ccc", fontSize: 14, fontWeight: 500 }}>
          {k("title")} <span style={{ color: "#888", fontWeight: 400 }}>({items.length})</span>
        </div>
        <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
          style={{ background: "none", border: "1px solid #333", borderRadius: 6, color: "#aaa", fontSize: 12,
            padding: "2px 8px", cursor: "pointer" }}>{open ? "▾" : "▸"}</button>
      </div>
      {open && body}
    </div>
  );
}
