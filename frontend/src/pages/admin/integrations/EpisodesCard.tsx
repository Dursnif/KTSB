// The Frigate user's episodes (P144-s11): what the cameras saw, one situation across cameras per row — the main view of
// Integrations → Frigate now; «Siste deteksjoner» stays below as secondary. Read only, admin API. List left / detail
// right on desktop, detail full screen on mobile (same pattern as the detections, s01). Frigate's name claims are shown
// as claims with KTSB's check word, never as fact (P144 «Frigate is a sensor»). Basic: list + detail; Expert adds the
// claim checks, the suppressed «unusual» and the dev topic (VISION thread 7). Self-contained so it moves whole (I21).
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useIsMobile } from "@/hooks/useIsMobile";
import {
  apiAnalyseFrigateEpisode, apiGetEpisodeAnalysis, apiGetFrigateEpisode, apiGetFrigateEpisodeAfterFrame,
  apiGetFrigateEpisodeFrame,
  apiGetFrigateEpisodes, type EpisodeAnalysisJob, type EpisodeDetail, type EpisodeItem, type EpisodeSensors,
  type FrigateStoreStatus,
} from "@/services/api";
import { ExpertOnly } from "../settings/shared";
import { WARN, badge, chip, sectionLabel } from "../rooms/shared";
import StillsList from "./StillsList";

const URGENT = "#e17055";
const CODE = "#5dade2";
const MODEL = "#a29bfe";
const MUTED = "#888";

const text: CSSProperties = { color: "#ddd", fontSize: 13, lineHeight: 1.55, margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere" };
const block: CSSProperties = { display: "flex", flexDirection: "column", gap: 4, minWidth: 0 };

function shortDate(date: string): string {
  const [, m, d] = date.split("-");
  return d && m ? `${d}.${m}` : date;
}

function useDuration() {
  const { t } = useTranslation();
  return (s: number) => {
    if (s < 60) return t("integrations.frigate_episodes.duration_s", { s });
    return t("integrations.frigate_episodes.duration_m", { m: Math.floor(s / 60), s: s % 60 });
  };
}

function Badges({ ep }: { ep: EpisodeItem }) {
  const { t } = useTranslation();
  const k = (key: string) => t(`integrations.frigate_episodes.${key}`);
  return (
    <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4 }}>
      {ep.urgent && <span style={badge(URGENT)}>{k("badge_urgent")}</span>}
      {ep.unusual?.value && <span style={badge(WARN)}>{k("badge_unusual")}</span>}
      {ep.false_detection && <span style={badge(MUTED)}>{k("badge_false_detection")}</span>}
      {ep.by === "code" && <span style={badge(CODE)}>{k("badge_code")}</span>}
      {ep.by === "model" && <span style={badge(MODEL)}>{k("badge_model")}</span>}
      {ep.stuck && <span style={badge(WARN)}>{k("badge_stuck")}</span>}
      {ep.status === "open" && !ep.stuck && <span style={badge(MUTED)}>{k("badge_open")}</span>}
      {/* P144-s14: «waiting» only when the conclusion really is not done (old done ones had no `by`) */}
      {ep.status === "closed" && !ep.by && ep.conclusion_status !== "done" && (
        <span style={badge(ep.conclusion_status === "waiting" ? WARN : MUTED)}>
          {ep.conclusion_status === "failed" ? k("badge_failed")
            : ep.conclusion_status === "waiting" ? k("badge_waiting") : k("badge_pending")}
        </span>
      )}
    </span>
  );
}

function Who({ ep }: { ep: EpisodeItem }) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.frigate_episodes.${key}`, o);
  const parts = ep.persons.map(p => k("frigate_claim", { name: p.name, status: k(`claim_${p.claim_status}`) }));
  if (ep.unnamed_persons) parts.push(k("unnamed", { count: ep.unnamed_persons }));
  const other = ep.labels.filter(l => l !== "person");
  return (
    <div style={{ fontSize: 12, color: "#bbb", overflowWrap: "anywhere" }}>
      {parts.join(" · ")}
      {other.length > 0 && <span style={{ color: MUTED }}>{parts.length ? " · " : ""}{other.join(", ")}</span>}
    </div>
  );
}

function Row({ ep, selected, onOpen }: { ep: EpisodeItem; selected: boolean; onOpen: () => void }) {
  const dur = useDuration();
  return (
    <button
      type="button" onClick={onOpen}
      style={{
        textAlign: "left", width: "100%", display: "flex", flexDirection: "column", gap: 5, padding: "9px 12px",
        borderRadius: 8, cursor: "pointer", background: selected ? "#1b1a2e" : "#141414",
        border: `1px solid ${selected ? "#6366f1" : "#222"}`, color: "inherit", minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ color: "#eee", fontSize: 13, fontWeight: 600 }}>
          {shortDate(ep.date)} {ep.start.slice(0, 5)}–{ep.end.slice(0, 5)}
        </span>
        <span style={{ color: MUTED, fontSize: 12 }}>{dur(ep.duration_s)}</span>
        <span style={{ marginLeft: "auto" }}><Badges ep={ep} /></span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
        {ep.cameras.map((c, i) => (
          <span key={c.camera} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            {i > 0 && <span style={{ color: "#555", fontSize: 11 }}>→</span>}
            <span style={chip}>{c.name}</span>
          </span>
        ))}
      </div>
      <Who ep={ep} />
      {ep.summary && (
        <div style={{
          fontSize: 12, color: "#999", overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical", overflowWrap: "anywhere",
        }}>{ep.summary}</div>
      )}
    </button>
  );
}

// One frame, loaded as a blob (header auth). 404 = older than Frigate's recordings. Callers key it by episode + index,
// so a new frame starts from a fresh state.
function FrameImg({ id, index, style }: { id: number; index: number; style: CSSProperties }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const [err, setErr] = useState<"" | "gone" | "error">("");
  useEffect(() => {
    let alive = true;
    let made = "";
    apiGetFrigateEpisodeFrame(id, index)
      .then(u => { made = u; if (alive) setUrl(u); else URL.revokeObjectURL(u); })
      .catch((e: { response?: { status?: number } }) => { if (alive) setErr(e?.response?.status === 404 ? "gone" : "error"); });
    return () => { alive = false; if (made) URL.revokeObjectURL(made); };
  }, [id, index]);
  if (url) return <img src={url} alt="" draggable={false} style={{ ...style, objectFit: "contain", background: "#000" }} />;
  return (
    <div style={{ ...style, display: "flex", alignItems: "center", justifyContent: "center", background: "#0d0d0d",
      color: "#555", fontSize: 11, textAlign: "center", padding: 6 }}>
      {err === "gone" ? t("integrations.frigate_episodes.frame_gone")
        : err === "error" ? t("integrations.frigate_episodes.frame_error") : "…"}
    </div>
  );
}

function Frames({ d }: { d: EpisodeDetail }) {
  const { t } = useTranslation();
  const [sel, setSel] = useState(0);  // the Detail keys this component by episode, so a new episode starts at 0
  if (!d.frames.length) {
    const key = d.scene?.reason === "vehicles_only" ? "no_frames_vehicles" : "no_frames";
    return <div style={{ color: "#555", fontSize: 12 }}>{t(`integrations.frigate_episodes.${key}`)}</div>;
  }
  const cur = d.frames[Math.min(sel, d.frames.length - 1)];
  return (
    <div style={block}>
      <FrameImg key={`${d.id}-${cur.index}`} id={d.id} index={cur.index} style={{ width: "100%", aspectRatio: "16 / 9", borderRadius: 8 }} />
      <div style={{ color: MUTED, fontSize: 12 }}>{cur.index + 1}. {cur.camera_name} · {cur.clock}</div>
      <div style={{ display: "flex", gap: 6, overflowX: "auto", paddingBottom: 4, minWidth: 0 }}>
        {d.frames.map(f => (
          <button
            key={f.index} type="button" onClick={() => setSel(f.index)}
            style={{
              flex: "0 0 auto", width: 92, padding: 0, borderRadius: 6, overflow: "hidden", cursor: "pointer",
              border: `2px solid ${f.index === cur.index ? "#6366f1" : "#1e1e1e"}`, background: "#0d0d0d", color: "inherit",
            }}
          >
            <FrameImg key={`${d.id}-${f.index}`} id={d.id} index={f.index} style={{ width: "100%", height: 52, display: "block" }} />
            <div style={{ fontSize: 10, color: "#aaa", padding: "2px 4px", textAlign: "left", whiteSpace: "nowrap",
              overflow: "hidden", textOverflow: "ellipsis" }}>
              {f.index + 1}. {f.camera_name} {f.clock}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

// P144-s19: both face sensors on one event — «Frigate: X · Double Take: Y → uncertain» (s29: per detector); a disagreement in the warning
// colour (it goes on the review list), a lean named.
function SensorsLine({ s, word }: { s: EpisodeSensors; word: string | null }) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.frigate_episodes.${key}`, o);
  const name = (n: string | null, kind: string | null) =>
    n ?? k(kind === "untracked" ? "sensor_untracked" : kind === "unknown" ? "sensor_unknown" : "sensor_none");
  const frigate = s.frigate_rejected ? k("sensor_rejected")
    : s.relation === "double_take_only" ? k("sensor_none") : name(s.frigate, s.frigate_kind);
  const second = s.relation === "split" ? k("sensor_split") : name(s.double_take, s.double_take_kind);
  const disagree = s.relation === "disagree" || s.relation === "split";
  // P144-s29: one vote per detector — «Frigate: X · InsightFace: Y · CPAI: Z → word»; a discarded torso-sized box
  // reads «not a face», a weight-0 detector «does not count»
  const dets = s.detectors ?? [];
  const detVal = (d: NonNullable<EpisodeSensors["detectors"]>[number]) =>
    (d.split ? k("sensor_split") : d.name ?? (d.discarded ? k("sensor_not_face") : name(null, d.kind)))
    + (d.weight === 0 ? ` (${k("sensor_weight_zero")})` : "");
  return (
    <div style={{ color: disagree ? WARN : MUTED }}>
      {dets.length
        ? k("sensors_detectors", { frigate, list: dets.map(d => `${d.label}: ${detVal(d)}`).join(" · "),
          word: k(`cert_${word ?? "none"}`) })
        : k("sensors", { frigate, provider: s.provider_label || s.provider || "?", second,
          word: k(`cert_${word ?? "none"}`) })}
      {!dets.length && s.confidence != null ? ` (${Math.round(s.confidence)} %)` : ""}
      {s.lean ? ` · ${k("lean", { name: s.lean })}` : ""}
      {s.review ? ` · ${k("review")}` : ""}
    </div>
  );
}

const KIND_COLOUR: Record<string, string> = { active: MODEL, calm: CODE, empty: MUTED };

// P144-s27: the episode as code sees it — chapters, each with its frames and the per-frame lines; below, where the scene
// looks different after the episode (a place, never a claim). Fold-outs keep a 24-frame episode readable on a phone.
// J15: the stored frame after the episode on a camera where code saw a change — shown here only, never sent to a model
function AfterFrame({ id, n, camera }: { id: number; n: number; camera: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let u: string | null = null;
    apiGetFrigateEpisodeAfterFrame(id, n).then(x => { u = x; setUrl(x); }).catch(() => setUrl(null));
    return () => { if (u) URL.revokeObjectURL(u); };
  }, [id, n]);
  if (!url) return null;
  return <img src={url} alt={camera} title={camera} style={{ width: "100%", maxWidth: 320, borderRadius: 6, marginTop: 4 }} />;
}

function Story({ d }: { d: EpisodeDetail }) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.frigate_episodes.story_${key}`, o);
  const story = d.story;
  if (!story || (!story.chapters.length && !story.lines.length && !story.changes.length)) return null;
  const lineStyle: CSSProperties = { fontSize: 12, color: "#bbb", overflowWrap: "anywhere", padding: "2px 0" };
  return (
    <>
      {story.chapters.length > 0 && (
        <Section label={k("chapters")}>
          {story.chapters.map(c => (
            <div key={c.n} style={{ borderLeft: `3px solid ${KIND_COLOUR[c.kind] ?? MUTED}`, padding: "4px 0 4px 10px", minWidth: 0 }}>
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 6 }}>
                <span style={{ color: "#eee", fontSize: 13, fontWeight: 600 }}>{k("chapter_n", { n: c.n })}</span>
                <span style={{ color: MUTED, fontSize: 12 }}>{c.start}–{c.end}</span>
                <span style={badge(KIND_COLOUR[c.kind] ?? MUTED)}>{k(`kind_${c.kind}`)}</span>
                {c.signal && <span style={{ color: MUTED, fontSize: 11 }}>{k(`signal_${c.signal}`)}</span>}
              </div>
              {c.cameras.length > 0 && <div style={{ fontSize: 12, color: "#999", overflowWrap: "anywhere" }}>{c.cameras.join(" · ")}</div>}
              {c.lines.length > 0 && (
                <details style={{ marginTop: 2 }}>
                  <summary style={{ cursor: "pointer", fontSize: 12, color: "#aaa" }}>{k("frame_lines", { count: c.lines.length })}</summary>
                  {c.lines.map((l, i) => <div key={i} style={lineStyle}>{l}</div>)}
                </details>
              )}
            </div>
          ))}
          <div style={{ fontSize: 11, color: "#777" }}>{k("timeline_note")}</div>
        </Section>
      )}
      {story.chapters.length === 0 && story.lines.length > 0 && (
        <Section label={k("timeline")}>
          {story.lines.map((l, i) => <div key={i} style={lineStyle}>{l}</div>)}
          <div style={{ fontSize: 11, color: "#777" }}>{k("timeline_note")}</div>
        </Section>
      )}
      {story.changes.length > 0 && (
        <Section label={k("changes")}>
          {story.changes.map((c, i) => (
            <div key={i} style={{ fontSize: 12, color: "#ddd", overflowWrap: "anywhere" }}>
              {c.fact ? c.fact : k("change_where", { camera: c.camera, where: c.where })}
              {c.words && <span style={{ color: MUTED }}> · {k("change_words", { words: c.words })}</span>}
            </div>
          ))}
          {(story.after_frames ?? []).length > 0 && (
            <div style={{ fontSize: 11, color: "#888", marginTop: 6 }}>{k("after_frames")}</div>
          )}
          {(story.after_frames ?? []).map(a => <AfterFrame key={a.n} id={d.id} n={a.n} camera={a.camera} />)}
          <div style={{ fontSize: 11, color: "#777" }}>{k("changes_note")}</div>
        </Section>
      )}
    </>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={block}>
      <span style={sectionLabel}>{label}</span>
      {children}
    </div>
  );
}

// P144-s24: «Analyser» / «Analyser på nytt» — look → scene → conclusion again on the kept frames (admin, one run at a
// time for the whole install). The previous conclusion is kept as history in the episode; the card polls the job.
function AnalyseButton({ d, onDone }: { d: EpisodeDetail; onDone: () => void }) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.frigate_episodes.${key}`, o);
  const [job, setJob] = useState<EpisodeAnalysisJob | null>(null);
  const [err, setErr] = useState("");
  const mine = job?.episode_id === d.id;
  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      apiGetEpisodeAnalysis().then(j => {
        setJob(j);
        if (j.state !== "running" && j.episode_id === d.id) onDone();
      }).catch(() => {});
    }, 1500);
    return () => window.clearInterval(timer);
  }, [running, d.id, onDone]);
  const start = () => {
    setErr("");
    apiAnalyseFrigateEpisode(d.id).then(setJob).catch((e: { response?: { status?: number; data?: { detail?: string } } }) => {
      const detail = e?.response?.data?.detail;
      setErr(typeof detail === "string" ? detail : "error");
    });
  };
  // N40: a stuck open episode may be analysed too (the server closes it first)
  if (d.status !== "closed" && d.stuck?.kind !== "not_closed") return null;
  const done = d.conclusion?.status === "done";
  const failed = mine && job?.state === "failed" ? job.result?.reason || "error" : "";
  const reasonText = (r: string) => t(`integrations.frigate_episodes.analyse_err_${r}`, { defaultValue: k("analyse_err_other", { reason: r }) });
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <button
        type="button" disabled={running} onClick={start}
        style={{ padding: "6px 12px", borderRadius: 8, border: "1px solid #3b3a6b", background: running ? "#1a1a1a" : "#1b1a2e",
          color: running ? "#777" : "#c7c5ff", cursor: running ? "default" : "pointer", fontSize: 12 }}
      >{running && mine ? k("analysing") : done ? k("analyse_again") : k("analyse")}</button>
      {running && !mine && <span style={{ color: MUTED, fontSize: 12 }}>{k("analyse_busy")}</span>}
      {mine && job?.state === "done" && <span style={{ color: "#7bd88f", fontSize: 12 }}>{k("analyse_done")}</span>}
      {(failed || err) && <span style={{ color: WARN, fontSize: 12, overflowWrap: "anywhere" }}>{reasonText(failed || err)}</span>}
    </div>
  );
}

// N40: «henger» — says only what code knows (not closed / queued / waiting for the image model / failed / running since)
function StuckLine({ d }: { d: EpisodeDetail }) {
  const { t, i18n } = useTranslation();
  const s = d.stuck;
  if (!s) return null;
  const clock = (ts?: number | null) => ts
    ? new Date(ts * 1000).toLocaleTimeString(i18n.language, { hour: "2-digit", minute: "2-digit" }) : "—";
  const text = t(`integrations.frigate_episodes.stuck_${s.kind}`, {
    minutes: s.minutes, attempts: s.attempts ?? 0, next: clock(s.next_try_ts), started: clock(s.started_ts),
  });
  return <div style={{ color: WARN, fontSize: 12 }}>{text}</div>;
}

function History({ d }: { d: EpisodeDetail }) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.frigate_episodes.${key}`, o);
  const items = d.conclusion?.history || [];
  if (!items.length) return null;
  return (
    <details style={{ fontSize: 12, color: "#999" }}>
      <summary style={{ cursor: "pointer" }}>{k("history", { count: items.length })}</summary>
      {[...items].reverse().map((h, i) => (
        <div key={i} style={{ padding: "5px 0", borderTop: "1px solid #1c1c1c", overflowWrap: "anywhere" }}>
          <span style={{ color: MUTED }}>{k("history_replaced", { when: new Date(h.replaced_ts * 1000).toLocaleString() })}</span>
          <div style={{ color: "#bbb" }}>{h.conclusion?.summary || k(`history_status_${h.conclusion?.status === "failed" ? "failed" : "none"}`)}</div>
        </div>
      ))}
    </details>
  );
}

function Detail({ d, onChanged }: { d: EpisodeDetail; onChanged: () => void }) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.frigate_episodes.${key}`, o);
  const dur = useDuration();
  const c = d.conclusion;
  const done = c?.status === "done";
  const scene = d.scene;
  const dev = c?.raise_at_dev_meeting;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ color: "#eee", fontSize: 15, fontWeight: 600 }}>
          {shortDate(d.date)} {d.start}–{d.end}
        </span>
        <span style={{ color: MUTED, fontSize: 12 }}>{dur(d.duration_s)}</span>
        <Badges ep={d} />
      </div>
      <Who ep={d} />
      <StuckLine d={d} />
      <AnalyseButton d={d} onDone={onChanged} />

      <Frames key={d.id} d={d} />

      {d.route && (
        <Section label={k("route")}>
          <p style={{ ...text, color: "#bbb", fontSize: 12 }}>{d.route}</p>
        </Section>
      )}

      <Story d={d} />

      <Section label={k("scene")}>
        {scene?.scene ? (
          <>
            <p style={text}>{scene.scene}</p>
            {(scene.people || []).map((p, i) => (
              <div key={i} style={{ fontSize: 12, color: "#bbb", overflowWrap: "anywhere" }}>
                {k("person_n", { n: i + 1 })} ({p.age}, {p.gender}){p.clothing ? ` — ${p.clothing}` : ""}
                {p.carried ? ` · ${k("carried")}: ${p.carried}` : ""}
              </div>
            ))}
            {scene.at_door && <div style={{ fontSize: 12, color: "#bbb" }}>{k("at_door")}: {scene.at_door}</div>}
            {scene.vehicles && <div style={{ fontSize: 12, color: "#bbb" }}>{k("vehicles")}: {scene.vehicles}</div>}
          </>
        ) : (
          <span style={{ color: "#555", fontSize: 12 }}>
            {scene?.status === "skipped"
              ? k(scene.reason === "likely_false_detection" ? "scene_skipped_look" : "scene_skipped")
              : scene?.status === "waiting" ? k("scene_waiting")
              : scene?.status === "failed" ? k("scene_failed", { reason: scene.reason || "?" }) : k("scene_none")}
          </span>
        )}
      </Section>

      <Section label={k("conclusion")}>
        {done ? (
          <>
            <p style={text}>{c?.summary}</p>
            {c?.images_checked === false && <div style={{ fontSize: 12, color: WARN }}>{k("not_checked")}</div>}
            {d.unusual && (
              <div style={{ fontSize: 12, color: d.unusual.value ? WARN : "#999" }}>
                {d.unusual.value ? k("unusual_yes") : k("unusual_no")}{d.unusual.reason ? `: ${d.unusual.reason}` : ""}
              </div>
            )}
            {c?.confidence && (
              <div style={{ fontSize: 12, color: "#999" }}>
                {k("confidence")}: {k(`confidence_${c.confidence.level}`)}
                {c.confidence.reasons?.length ? ` — ${c.confidence.reasons.join("; ")}` : ""}
              </div>
            )}
          </>
        ) : (
          <span style={{ color: "#555", fontSize: 12 }}>
            {d.status === "open" ? k("conclusion_open")
              : c?.status === "waiting" ? k("conclusion_waiting")
              : c?.status === "failed" ? k("conclusion_failed", { reason: c.reason || "?" }) : k("conclusion_pending")}
          </span>
        )}
        <History d={d} />
      </Section>

      {done && (
        <Section label={k("notify")}>
          <span style={{ fontSize: 12, color: c?.notify_suggestion ? "#ddd" : "#555", overflowWrap: "anywhere" }}>
            {c?.notify_suggestion
              ? [c.notify_suggestion.who, c.notify_suggestion.why].filter(Boolean).join(" — ")
              : k("notify_none")}
          </span>
        </Section>
      )}

      <ExpertOnly>
        <div style={{ ...block, gap: 10, paddingTop: 10, borderTop: "1px solid #222" }}>
          <span style={{ fontSize: 13, color: "#aaa" }}>{k("expert_heading")}</span>
          {d.unusual_suppressed && (
            <div style={{ fontSize: 12, color: WARN, overflowWrap: "anywhere" }}>
              {k("suppressed", { days: d.unusual_suppressed.baseline_days ?? "?", reason: d.unusual_suppressed.reason || "—" })}
            </div>
          )}
          {dev && (
            <div style={{ fontSize: 12, color: "#bbb", overflowWrap: "anywhere" }}>
              {k("dev_topic", { topic: dev.topic, text: dev.text, count: dev.count ?? 1 })}
            </div>
          )}
          <Section label={k("events")}>
            {d.events.map(e => (
              <div key={e.event_id} style={{ fontSize: 12, color: "#bbb", padding: "5px 0", borderTop: "1px solid #1c1c1c", overflowWrap: "anywhere" }}>
                <div>
                  {e.clock} · {e.camera_name} · {e.label}
                  {e.label === "person" && (
                    <> · {e.person ? k("frigate_claim", { name: e.person, status: k(`claim_${e.claim_status}`) })
                      : k(`claim_${e.claim_status}_unnamed`)}</>
                  )}
                </div>
                {e.check.decision && (
                  <div style={{ color: MUTED }}>
                    {k("check")}: {k(`claim_${e.check.decision}`)}
                    {e.check.layers.map(l => ` · ${l.layer} ${l.verdict}${l.strong ? "!" : ""}${l.reason ? ` (${l.reason})` : ""}`).join("")}
                  </div>
                )}
                {e.check.sensors && <SensorsLine s={e.check.sensors} word={e.certainty ?? null} />}
                {e.check.doubt?.reason && <div style={{ color: WARN }}>{k("doubt")}: {e.check.doubt.reason}</div>}
                {e.check.storage_gate && <div style={{ color: MUTED }}>{k("storage_gate")}: {e.check.storage_gate}</div>}
              </div>
            ))}
          </Section>
        </div>
      </ExpertOnly>
    </div>
  );
}

export default function EpisodesCard() {
  const { t } = useTranslation();
  const k = (key: string) => t(`integrations.frigate_episodes.${key}`);
  const isMobile = useIsMobile();
  const [items, setItems] = useState<EpisodeItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [storeStatus, setStoreStatus] = useState<FrigateStoreStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selId, setSelId] = useState<number | null>(null);
  const [detail, setDetail] = useState<EpisodeDetail | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);

  const load = useCallback((before?: number) => {
    return apiGetFrigateEpisodes(before)
      .then(r => {
        setItems(prev => (before == null ? r.episodes : [...prev, ...r.episodes]));
        setHasMore(r.has_more);
        setEnabled(r.enabled);
        setStoreStatus(r.store ?? null);
        setError(false);
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);
  const loadMore = () => { setLoading(true); load(items[items.length - 1].start_ts); };

  // Desktop shows the newest episode until one is picked; mobile waits for a tap.
  const shownId = selId ?? (!isMobile && items.length ? items[0].id : null);
  const [rev, setRev] = useState(0);  // P144-s24: bumped after «Analyser» finished → the detail and the list reload
  useEffect(() => {
    if (shownId == null) return;
    let alive = true;
    apiGetFrigateEpisode(shownId).then(d => { if (alive) setDetail(d); }).catch(() => {});
    return () => { alive = false; };
  }, [shownId, rev]);
  const changed = useCallback(() => { setRev(r => r + 1); load(); }, [load]);
  const shown = detail && detail.id === shownId ? detail : null;

  // The phone's back button closes the full-screen view (same as the detections).
  useEffect(() => {
    if (!mobileOpen) return;
    window.history.pushState({ ktsbEpisode: true }, "");
    const onPop = () => setMobileOpen(false);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [mobileOpen]);
  const closeMobile = () => { if (window.history.state?.ktsbEpisode) window.history.back(); else setMobileOpen(false); };

  const open = (id: number) => { setSelId(id); if (isMobile) setMobileOpen(true); };
  // J21 5d: «Siste deteksjoner» as a second view of this list (module 1's still-image results); provisional
  const [view, setView] = useState<"episodes" | "stills">("episodes");
  const openFromStill = (id: number) => { setView("episodes"); open(id); };
  const tab = (v: "episodes" | "stills") => ({
    padding: "3px 10px", borderRadius: 6, fontSize: 12, cursor: "pointer",
    border: `1px solid ${view === v ? "#4a4a4a" : "#2a2a2a"}`, background: view === v ? "#222" : "none",
    color: view === v ? "#ddd" : "#888",
  } as CSSProperties);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{k("title")}</CardTitle>
        <CardDescription>{k("description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
          <button type="button" style={tab("episodes")} onClick={() => setView("episodes")}>{k("view_episodes")}</button>
          <button type="button" style={tab("stills")} onClick={() => setView("stills")}>{k("view_stills")}</button>
        </div>
        {view === "stills" && <StillsList onOpenEpisode={openFromStill} />}
        {view === "episodes" && <>
        {error && <div style={{ color: "#f87171", fontSize: 12, marginBottom: 8 }}>{k("error")}</div>}
        {storeStatus && storeStatus.ok === false && (
          <div role="alert" style={{ color: "#f87171", fontSize: 12, marginBottom: 8, overflowWrap: "anywhere" }}>
            {t("integrations.frigate_episodes.store_cannot_write", {
              cause: t(`integrations.frigate_episodes.store_cause_${storeStatus.cause || "error"}`),
              count: storeStatus.failures ?? 0,
            })}
          </div>
        )}
        {!loading && !error && items.length === 0 && (
          <div style={{ color: "#777", fontSize: 13 }}>{enabled ? k("empty") : k("empty_off")}</div>
        )}
        {items.length > 0 && (
          <div style={{ display: "flex", gap: 16, minWidth: 0 }}>
            <div style={{
              display: "flex", flexDirection: "column", gap: 6, minWidth: 0,
              ...(isMobile ? { flex: 1 } : { width: 360, flexShrink: 0, maxHeight: 620, overflowY: "auto", paddingRight: 4 }),
            }}>
              {items.map(ep => (
                <Row key={ep.id} ep={ep} selected={!isMobile && ep.id === shownId} onOpen={() => open(ep.id)} />
              ))}
              {hasMore && (
                <button
                  type="button" disabled={loading} onClick={loadMore}
                  style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid #2a2a2a", background: "#1a1a1a",
                    color: "#aaa", cursor: "pointer", fontSize: 12 }}
                >{loading ? "…" : k("load_more")}</button>
              )}
            </div>
            {!isMobile && (
              <div style={{ flex: 1, minWidth: 0 }}>
                {shown ? <Detail d={shown} onChanged={changed} /> : <div style={{ color: "#555", fontSize: 12 }}>{k("select_hint")}</div>}
              </div>
            )}
          </div>
        )}
        {loading && items.length === 0 && <div style={{ color: "#555", fontSize: 12 }}>…</div>}
        </>}
      </CardContent>

      {isMobile && mobileOpen && (
        <div style={{ position: "fixed", inset: 0, zIndex: 9999, background: "#0b0b0b", display: "flex",
          flexDirection: "column", height: "100dvh" }}>
          <div style={{ display: "flex", justifyContent: "flex-end", padding: "10px 12px", borderBottom: "1px solid #1e1e1e" }}>
            <button
              onClick={closeMobile} aria-label={t("common.close")}
              style={{ background: "rgba(30,30,30,0.9)", border: "1px solid #444", borderRadius: 6, color: "#ddd",
                padding: "6px 14px", fontSize: 16 }}
            >✕</button>
          </div>
          <div style={{ flex: 1, overflowY: "auto", overflowX: "hidden", padding: "12px 16px 24px" }}>
            {shown ? <Detail d={shown} onChanged={changed} /> : <div style={{ color: "#555", fontSize: 12 }}>…</div>}
          </div>
        </div>
      )}
    </Card>
  );
}
