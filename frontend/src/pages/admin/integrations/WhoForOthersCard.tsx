// J22 (2026-11-07: «så lenge brukere ikke har reservert seg for tracking, så skal admin kunne verifisere alle
// brukere»): inside «Hvem» for admin — person events nobody has settled (camera + time only) and a choice of the household
// members admin may name (tracking on only; the server refuses anyone else). Saved as «det var <member>, bekreftet av
// <admin>»; the member's own answer counts over it. Rendered only inside QuestionsCard (admin).
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiAnswerWhoForOther, apiGetWhoForOthers, type WhoForOthers } from "@/services/api";
import EventSnapshot from "@/components/EventSnapshot";

export default function WhoForOthersCard({ onCount }: { onCount?: (n: number) => void }) {
  const { t, i18n } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`questions_card.others_${key}`, o);
  const [data, setData] = useState<WhoForOthers>({ questions: [], members: [] });
  const [pick, setPick] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => {
    apiGetWhoForOthers().then(setData).catch(() => setData({ questions: [], members: [] }));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { onCount?.(data.questions.length); }, [data.questions.length, onCount]);
  const when = (ts: number) => new Date(ts * 1000).toLocaleString(i18n.language, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
  const save = async (eventId: string) => {
    const member = pick[eventId];
    if (!member || busy) return;
    setBusy(true);
    setMsg(null);
    try { await apiAnswerWhoForOther(eventId, member); setMsg(k("saved")); } catch { setMsg(k("refused")); }
    finally { setBusy(false); load(); }
  };
  const btn = { background: "none", border: "1px solid #333", borderRadius: 6, color: "#aaa", fontSize: 12,
    padding: "2px 8px", cursor: "pointer" } as const;
  return (
    <div>
      <div style={{ color: "#777", fontSize: 12, lineHeight: 1.5, marginBottom: 6 }}>{k("intro")}</div>
      {data.questions.length === 0 && <div style={{ color: "#777", fontSize: 12 }}>{k("none")}</div>}
      {data.questions.map(q => (
        <div key={q.event_id} style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", padding: "6px 0",
          borderTop: "1px solid #1a1a1a" }}>
          <div style={{ color: "#bbb", fontSize: 13, flex: "1 1 200px", cursor: "pointer" }} title={t("questions_card.click_for_image")}
            onClick={() => setOpen(o => (o === q.event_id ? null : q.event_id))}>{k("ask", { camera: q.camera, time: when(q.ts) })}</div>
          <select value={pick[q.event_id] ?? ""} disabled={busy}
            onChange={e => setPick(p => ({ ...p, [q.event_id]: e.target.value }))}
            style={{ background: "#0b0b0b", border: "1px solid #333", borderRadius: 6, color: "#ddd", fontSize: 12, padding: "3px 6px" }}>
            <option value="">{k("choose")}</option>
            {data.members.map(m => <option key={m.username} value={m.username}>{m.name}</option>)}
          </select>
          <button type="button" style={btn} disabled={busy || !pick[q.event_id]} onClick={() => save(q.event_id)}>{k("save")}</button>
          {open === q.event_id && <div style={{ flexBasis: "100%" }}><EventSnapshot eventId={q.event_id} /></div>}
        </div>
      ))}
      {msg && <div style={{ color: "#999", fontSize: 12, marginTop: 6 }}>{msg}</div>}
    </div>
  );
}
