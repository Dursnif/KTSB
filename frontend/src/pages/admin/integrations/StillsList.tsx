// P144 6e (2026-11-13: no own still-image analysis): «Siste deteksjoner» shows the single-image reads the image model
// ALREADY makes in the new pipe — L3 per event and each frame of module 4's frame-by-frame run — newest first, each with its
// picture, the model's own words labelled «bildemodellens ord» (G7, in the language the call used), which step it came from,
// time, camera and a link to its episode; item (3): the reply as the model gave it, folded («Bildemodellens ord (rått)»). Display
// only: no model call. Data for writing the image prompts later.
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, apiGetFrigateEpisodeFrame, apiGetFrigateModelReads, type FrigateModelRead } from "@/services/api";

function Thumb({ read }: { read: FrigateModelRead }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let u: string | null = null;
    let alive = true;
    const p = read.kind === "frame" && read.episode_id != null && read.index != null
      ? apiGetFrigateEpisodeFrame(read.episode_id, read.index)
      : api.get(`/api/frigate_snapshot/${encodeURIComponent(read.event_id ?? "")}`, { responseType: "blob" })
        .then(r => URL.createObjectURL(r.data));
    p.then(x => { u = x; if (alive) setUrl(x); }).catch(() => { if (alive) setUrl(null); });
    return () => { alive = false; if (u) URL.revokeObjectURL(u); };
  }, [read.kind, read.episode_id, read.index, read.event_id]);
  return url ? <img src={url} alt="" style={{ width: 128, height: 72, objectFit: "cover", borderRadius: 4, flexShrink: 0 }} />
    : <div style={{ width: 128, height: 72, background: "#1a1a1a", borderRadius: 4, flexShrink: 0 }} />;
}

// The model's own fields, shown as they are (key: value) — no translation, no summary by code
function Words({ words }: { words: Record<string, unknown> }) {
  const parts: string[] = [];
  const walk = (v: unknown, prefix: string) => {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      Object.entries(v as Record<string, unknown>).forEach(([k, x]) => walk(x, prefix ? `${prefix} ${k}` : k));
    } else if (v !== "" && v != null) {
      parts.push(prefix ? `${prefix}: ${String(v)}` : String(v));
    }
  };
  Object.entries(words).forEach(([k, v]) => (k === "description" || k === "analysis") ? parts.push(String(v)) : walk(v, ""));
  return <span>{parts.length ? parts.join(" · ") : "—"}</span>;
}

export default function StillsList({ onOpenEpisode }: { onOpenEpisode: (id: number) => void }) {
  const { t, i18n } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.frigate_episodes.${key}`, o);
  const [items, setItems] = useState<FrigateModelRead[]>([]);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const load = useCallback((before?: number) => {
    apiGetFrigateModelReads(before)
      .then(d => { setItems(p => (before == null ? d.reads : [...p, ...d.reads])); setMore(d.has_more); })
      .catch(() => { if (before == null) setItems([]); setMore(false); })
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);
  const when = (ts: number) => new Date(ts * 1000).toLocaleString(i18n.language, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  if (!loading && items.length === 0) return <div style={{ color: "#777", fontSize: 13 }}>{k("reads_empty")}</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 620, overflowY: "auto" }}>
      {items.map(r => (
        <div key={`${r.kind}-${r.event_id ?? r.episode_id}-${r.frame ?? ""}-${r.ts}`} style={{ display: "flex", gap: 10, padding: 8,
          border: "1px solid #1e1e1e", borderRadius: 8, background: "#121212", minWidth: 0, flexWrap: "wrap" }}>
          <Thumb read={r} />
          <div style={{ minWidth: 0, flex: "1 1 220px" }}>
            <div style={{ color: "#999", fontSize: 11 }}>
              {r.camera_name || r.camera} · {when(r.ts)} · {r.kind === "l3"
                ? k("reads_step_l3", { label: r.label })
                : k("reads_step_frame", { n: r.frame, episode: r.episode_id })}
            </div>
            <div style={{ color: "#777", fontSize: 11, marginTop: 4 }}>{k("reads_model_words")}</div>
            <div style={{ color: "#ccc", fontSize: 13, overflowWrap: "anywhere" }}><Words words={r.words} /></div>
            {r.raw && (
              <details style={{ marginTop: 4 }}>
                <summary style={{ color: "#777", fontSize: 11, cursor: "pointer" }}>{k("reads_model_raw")}</summary>
                <pre style={{ color: "#aaa", fontSize: 11, whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: "4px 0 0",
                  background: "#0b0b0b", border: "1px solid #222", borderRadius: 4, padding: 6 }}>{r.raw}</pre>
              </details>
            )}
            {r.episode_id != null && (
              <button type="button" onClick={() => onOpenEpisode(r.episode_id as number)}
                style={{ marginTop: 4, background: "none", border: "none", padding: 0, color: "#8ab4f8", fontSize: 12,
                  cursor: "pointer" }}>{k("stills_open_episode")} #{r.episode_id}</button>
            )}
          </div>
        </div>
      ))}
      {more && (
        <button type="button" disabled={loading} onClick={() => { setLoading(true); load(items[items.length - 1].ts); }}
          style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid #2a2a2a", background: "#1a1a1a", color: "#aaa",
            cursor: "pointer", fontSize: 12 }}>{loading ? "…" : k("load_more")}</button>
      )}
    </div>
  );
}
