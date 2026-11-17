// Run-queue J20: «Hva er dette?» — the «Hva» part of the one card «Hvem / Hva / Hvor?» (admin only for now,
// 2026-11-06). Code asks about still, non-living things it sees often (bins, cars, trailers, garden figures; J3 gate keys)
// and about still things Frigate keeps calling an animal (the swan-shaped flower pot as «bird»). The answer is an open
// name (the vocabulary grows, #27), «ikke en <label>» (optionally with a name), «feil deteksjon / ikke en ting» or «hopp
// over». «Ikke en <label>» and «feil deteksjon» become a narrow confirmed-wrong rule (that camera, label and spot only,
// #28) — held back only when the switch «Hold tilbake bekreftet feil» is on, otherwise shadow. The image is the same
// daytime frame with no person as «Hva er her?», the spot framed. Rendered only inside QuestionsCard (`bare`).
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  apiAnswerWhatQuestion, apiGetWhatHistory, apiGetWhatQuestions, type WhatAnswer, type WhatHistoryItem, type WhatQuestion,
} from "@/services/api";
import SpotImage from "./SpotImage";

// The gate's 10 × 10 cell → the 5 × 5 words the place text uses
const five = (cell: string) => cell.split(",").map(v => String(Math.floor(Number(v) / 2))).join(",");

export default function WhatQuestionsCard({ onCount }: { onCount?: (n: number) => void }) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.what_questions.${key}`, o);
  const kp = (key: string, o?: Record<string, unknown>) => t(`integrations.place_questions.${key}`, o);
  const [items, setItems] = useState<WhatQuestion[]>([]);
  const [done, setDone] = useState<WhatHistoryItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [names, setNames] = useState<Record<string, string>>({});
  const load = useCallback(() => {
    apiGetWhatQuestions().then(d => setItems(d.questions)).catch(() => setItems([]));
    apiGetWhatHistory().then(d => setDone(d.answered)).catch(() => setDone([]));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { onCount?.(items.length); }, [items.length, onCount]);
  const answer = async (q: WhatQuestion, a: WhatAnswer) => {
    const name = (names[q.id] ?? "").trim();
    if (busy || (a === "named" && !name)) return;
    setBusy(true);
    try { await apiAnswerWhatQuestion(q.id, a, a === "named" || a === "not_label" ? name || undefined : undefined); }
    catch { /* reload shows the truth */ } finally { setBusy(false); load(); }
  };
  const btn = { background: "none", border: "1px solid #333", borderRadius: 6, color: "#aaa", fontSize: 12,
    padding: "2px 8px", cursor: "pointer" } as const;
  const label = (x: { label: string; label_name?: string }) => x.label_name || x.label;
  const status = (d: WhatHistoryItem) => d.status === "named" ? (d.name ?? "")
    : d.status === "not_label" ? k("h_not_label", { label: label(d), name: d.name ? ` — ${d.name}` : "" })
      : k(`h_${d.status}`);
  return (
    <div>
      {items.length === 0 && <div style={{ color: "#777", fontSize: 12 }}>{k("none_open")}</div>}
      {items.map(q => (
        <div key={q.id} style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #1a1a1a" }}>
          <div style={{ color: "#bbb", fontSize: 13, marginBottom: 6 }}>
            {k(q.misread ? "ask_misread" : "ask", { camera: q.camera_name || q.camera, label: label(q), days: q.days })}
          </div>
          <SpotImage s={{ ...q, cell: five(q.cell) }} width={360} k={kp} />
          <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
            <input type="text" maxLength={60} value={names[q.id] ?? ""} placeholder={k("placeholder")} disabled={busy}
              onChange={e => setNames(p => ({ ...p, [q.id]: e.target.value }))}
              style={{ background: "#0b0b0b", border: "1px solid #333", borderRadius: 6, color: "#ddd", fontSize: 13,
                padding: "4px 8px", minWidth: 180 }} />
            <button type="button" style={btn} disabled={busy || !(names[q.id] ?? "").trim()}
              onClick={() => answer(q, "named")}>{k("a_named")}</button>
            <button type="button" style={btn} disabled={busy} onClick={() => answer(q, "not_label")}>
              {k("a_not_label", { label: label(q) })}</button>
            <button type="button" style={btn} disabled={busy} onClick={() => answer(q, "wrong")}>{k("a_wrong")}</button>
            <button type="button" style={btn} disabled={busy} onClick={() => answer(q, "skip")}>{k("a_skip")}</button>
          </div>
        </div>
      ))}
      <div style={{ color: "#999", fontSize: 12, marginTop: 14, paddingTop: 8, borderTop: "1px solid #1a1a1a" }}>{k("history")}</div>
      {done.length === 0 && <div style={{ color: "#777", fontSize: 12, marginTop: 4 }}>{k("history_empty")}</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 6 }}>
        {done.map(d => (
          <div key={d.id} style={{ width: 160 }}>
            <SpotImage s={{ ...d, cell: five(d.cell) }} width={160} k={kp} />
            <div style={{ color: "#bbb", fontSize: 12, marginTop: 4 }}>{status(d)}</div>
            <div style={{ color: "#777", fontSize: 11 }}>{d.camera_name || d.camera}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
