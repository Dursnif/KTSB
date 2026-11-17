// P144 #9 / P149 phase 2 (GUI list G17): «Var det deg?» — the logged-in person's own camera questions as a checklist.
// J9: also the night meeting's themes about this person that were set aside as a question (same budget, same ticks).
// Code picks and sorts the questions (backend); each row is camera + time only — no image, no name, no detail, so a
// wrong guess reveals nobody. One tick answers it: ja / nei / privat / vet ikke / hopp over. Private to this person (admin never
// sees them). J18b: always shown — open questions on top (or «ingen åpne spørsmål»), a short history of this
// person's own answers below (camera + time + answer, never an image). Collapsed by default, button top right (G1).
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  apiAnswerCameraQuestion, apiGetMyCameraHistory, apiGetMyCameraQuestions, type CameraAnswer, type CameraHistoryItem,
  type CameraQuestion,
} from "../services/api";
import { apiErrorDetail } from "@/lib/apiError";
import EventSnapshot from "@/components/EventSnapshot";

// N39 c: «vet ikke» (unsure) closes a camera question without teaching anything; theme questions do not take it.
const ANSWERS: CameraAnswer[] = ["yes", "no", "private", "unsure", "skip"];

export default function CameraQuestionsCard({ bare = false, onCount }: { bare?: boolean; onCount?: (n: number) => void } = {}) {
  const { t, i18n } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`camera_questions.${key}`, o);
  const [items, setItems] = useState<CameraQuestion[]>([]);
  const [done, setDone] = useState<CameraHistoryItem[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiGetMyCameraQuestions().then(d => setItems(d.questions)).catch(() => setItems([]));
    apiGetMyCameraHistory().then(d => setDone(d.answered)).catch(() => setDone([]));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { onCount?.(items.length); }, [items.length, onCount]);

  const when = (ts: number) => new Date(ts * 1000).toLocaleString(i18n.language, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });

  const key = (q: CameraQuestion) => q.id ?? q.event_id ?? "";

  const answer = async (q: CameraQuestion, a: CameraAnswer) => {
    if (busy) return;
    setBusy(key(q));
    setError(null);
    try {
      await apiAnswerCameraQuestion(key(q), a, q.kind === "theme" ? "theme" : "camera");
      setItems(prev => prev.filter(x => key(x) !== key(q)));
      load();
    } catch (err) {
      setError(apiErrorDetail(err) ?? k("error"));
      load();
    } finally {
      setBusy(null);
    }
  };

  const body = (
        <div style={{ marginTop: 8 }}>
          <div style={{ color: "#777", fontSize: 12, lineHeight: 1.5, marginBottom: 8 }}>{k("intro")}</div>
          {items.map(q => (
            <div key={key(q)} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8,
              padding: "6px 0", borderTop: "1px solid #1a1a1a" }}>
              <div style={{ color: "#bbb", fontSize: 13, flex: "1 1 220px",
                cursor: q.kind !== "theme" && q.event_id ? "pointer" : "default" }}
                title={q.kind !== "theme" && q.event_id ? t("questions_card.click_for_image") : undefined}
                onClick={() => { if (q.kind !== "theme" && q.event_id) setShown(o => (o === key(q) ? null : key(q))); }}>
                {q.kind === "theme" ? k("ask_theme", { text: q.text ?? "" }) : k("ask", { camera: q.camera, time: when(q.ts ?? 0) })}
              </div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                {ANSWERS.filter(a => q.kind !== "theme" || a !== "unsure").map(a => (
                  <label key={a} style={{ display: "flex", alignItems: "center", gap: 4, color: "#999", fontSize: 12,
                    cursor: busy ? "wait" : "pointer" }}>
                    <input type="checkbox" checked={false} disabled={busy !== null}
                      onChange={() => answer(q, a)} />
                    {k(`a_${a}`)}
                  </label>
                ))}
              </div>
              {shown === key(q) && q.event_id && <div style={{ flexBasis: "100%" }}><EventSnapshot eventId={q.event_id} /></div>}
            </div>
          ))}
          {items.length === 0 && <div style={{ color: "#777", fontSize: 12 }}>{k("none_open")}</div>}
          {error && <div style={{ color: "#e57373", fontSize: 12, marginTop: 6 }}>{error}</div>}
          <div style={{ color: "#999", fontSize: 12, marginTop: 14, paddingTop: 8, borderTop: "1px solid #1a1a1a" }}>{k("history")}</div>
          {done.length === 0 && <div style={{ color: "#777", fontSize: 12, marginTop: 4 }}>{k("history_empty")}</div>}
          {done.map(d => (
            <div key={d.event_id} style={{ color: "#bbb", fontSize: 12, marginTop: 4 }}>
              {k("h_answer", { camera: d.camera, time: d.ts ? when(d.ts) : "—",
                answer: d.answer === "skip" ? k("h_skip") : k(`a_${d.answer}`) })}
            </div>
          ))}
        </div>
  );
  if (bare) return body;

  return (
    <div style={{ background: "#111", border: "1px solid #1e1e1e", borderRadius: 8, padding: "14px 16px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ color: "#ccc", fontSize: 14, fontWeight: 500 }}>
          {k("title")} <span style={{ color: "#888", fontWeight: 400 }}>({items.length})</span>
        </div>
        <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
          aria-label={open ? k("close") : k("open")}
          style={{ background: "none", border: "1px solid #333", borderRadius: 6, color: "#aaa", fontSize: 12,
            padding: "2px 8px", cursor: "pointer" }}>
          {open ? "▾" : "▸"}
        </button>
      </div>
      {open && body}
    </div>
  );
}
