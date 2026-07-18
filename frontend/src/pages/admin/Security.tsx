import { useEffect, useState, useMemo, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw, ChevronDown, ChevronRight } from "lucide-react";
import { api, apiGetNormalcyEvents, apiPostNormalcyCorrect, type NormalcyEvent } from "../../services/api";

function SnapshotImg({ eventId }: { eventId: string }) {
  const [blobUrl, setBlobUrl] = useState<string>("");

  useEffect(() => {
    if (!eventId) return;
    let url = "";
    api.get(`/api/normalcy/snapshot/${eventId}`, { responseType: "blob" })
      .then(r => { url = URL.createObjectURL(r.data); setBlobUrl(url); })
      .catch(() => {});
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [eventId]);

  if (!blobUrl) return null;
  return (
    <img
      src={blobUrl}
      alt=""
      style={{ width: 180, borderRadius: 6, border: "1px solid #252525", display: "block" }}
    />
  );
}

const DAY_OPTIONS = [7, 14, 30];

type GroupBy = "label" | "camera";
type FilterStatus = "pending" | "all" | "reviewed";

function eventKey(ev: NormalcyEvent) {
  return ev.source_key + "||" + ev.ts;
}

function scoreColor(score: number) {
  if (score >= 80) return "#ff4444";
  if (score >= 50) return "#ff9f43";
  return "#f9ca24";
}

function confColor(pct: number) {
  if (pct < 20) return "#ff6b6b";
  if (pct < 50) return "#ff9f43";
  return "#4caf50";
}

function confLabel(pct: number) {
  if (pct < 20) return "Lite data";
  if (pct < 50) return "Delvis";
  return "God";
}

function confTooltip(pct: number) {
  if (pct < 20) return `${pct}% datadekning — for lite historikk, deteksjonen er usikker`;
  if (pct < 50) return `${pct}% datadekning — delvis kalibrert`;
  return `${pct}% datadekning — godt kalibrert`;
}

export default function Security() {
  const { t } = useTranslation();
  const [events, setEvents] = useState<NormalcyEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [groupBy, setGroupBy] = useState<GroupBy>("label");
  const [filterStatus, setFilterStatus] = useState<FilterStatus>("pending");
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [commentState, setCommentState] = useState<Record<string, string>>({});
  const [submittingKeys, setSubmittingKeys] = useState<Set<string>>(new Set());
  const [batchSubmitting, setBatchSubmitting] = useState(false);
  const [localCorrections, setLocalCorrections] = useState<Record<string, NormalcyEvent["correction"]>>({});
  const [labelFilter, setLabelFilter] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGetNormalcyEvents({ days, label: labelFilter || undefined });
      setEvents(r.events ?? []);
      setSelectedKeys(new Set());
      setExpandedKeys(new Set());
    } catch {
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, [days, labelFilter]);

  useEffect(() => { load(); }, [load]);

  const filteredEvents = useMemo(() =>
    events.filter(ev => {
      const correction = localCorrections[eventKey(ev)] ?? ev.correction;
      if (filterStatus === "pending") return !correction;
      if (filterStatus === "reviewed") return !!correction;
      return true;
    }),
    [events, localCorrections, filterStatus]
  );

  const grouped = useMemo(() => {
    const groups: Record<string, NormalcyEvent[]> = {};
    for (const ev of filteredEvents) {
      const gk = groupBy === "label" ? ev.label : ev.camera_friendly;
      if (!groups[gk]) groups[gk] = [];
      groups[gk].push(ev);
    }
    return Object.entries(groups).sort((a, b) => b[1].length - a[1].length);
  }, [filteredEvents, groupBy]);

  const totalPending = useMemo(() =>
    events.filter(ev => !(localCorrections[eventKey(ev)] ?? ev.correction)).length,
    [events, localCorrections]
  );

  const knownLabels = useMemo(() => {
    const s = new Set(events.map(e => e.label));
    return ["", ...Array.from(s).sort()];
  }, [events]);

  const applyCorrection = useCallback((k: string, verdict: string, comment: string) => {
    setLocalCorrections(c => ({
      ...c,
      [k]: { verdict, comment, by: "admin", ts: new Date().toISOString() },
    }));
  }, []);

  const submitSingle = async (ev: NormalcyEvent, verdict: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const k = eventKey(ev);
    setSubmittingKeys(s => new Set(s).add(k));
    try {
      await apiPostNormalcyCorrect({
        source_key: ev.source_key,
        hour_bucket: ev.hour_bucket,
        weekday: ev.weekday,
        verdict,
        comment: commentState[k] ?? "",
      });
      applyCorrection(k, verdict, commentState[k] ?? "");
      setExpandedKeys(s => { const n = new Set(s); n.delete(k); return n; });
    } finally {
      setSubmittingKeys(s => { const n = new Set(s); n.delete(k); return n; });
    }
  };

  const submitBatch = async (verdict: string) => {
    if (batchSubmitting) return;
    setBatchSubmitting(true);
    for (const k of [...selectedKeys]) {
      const ev = events.find(e => eventKey(e) === k);
      if (!ev) continue;
      try {
        await apiPostNormalcyCorrect({
          source_key: ev.source_key,
          hour_bucket: ev.hour_bucket,
          weekday: ev.weekday,
          verdict,
          comment: commentState[k] ?? "",
        });
        applyCorrection(k, verdict, commentState[k] ?? "");
      } catch { /* continue with others */ }
    }
    setSelectedKeys(new Set());
    setBatchSubmitting(false);
  };

  const toggleExpand = (k: string) =>
    setExpandedKeys(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });

  const toggleSelect = (k: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedKeys(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });
  };

  const toggleGroupSelect = (evs: NormalcyEvent[], e: React.ChangeEvent) => {
    e.stopPropagation();
    const keys = evs.map(eventKey);
    const allSel = keys.every(k => selectedKeys.has(k));
    setSelectedKeys(s => {
      const n = new Set(s);
      if (allSel) keys.forEach(k => n.delete(k));
      else keys.forEach(k => n.add(k));
      return n;
    });
  };

  const toggleGroupCollapse = (gk: string) =>
    setCollapsedGroups(s => { const n = new Set(s); n.has(gk) ? n.delete(gk) : n.add(gk); return n; });

  // ── Styles ──────────────────────────────────────────────────────────────
  const tab = (active: boolean): React.CSSProperties => ({
    padding: "5px 13px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 12,
    background: active ? "#646cff" : "#1a1a1a",
    color: active ? "#fff" : "#666",
    fontWeight: active ? 600 : 400,
  });

  const groupBadge = (color: string): React.CSSProperties => ({
    padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600,
    background: color + "20", color, border: `1px solid ${color}35`,
  });

  const verdictBadge = (v: string): React.CSSProperties => ({
    display: "inline-block", padding: "2px 9px", borderRadius: 10, fontSize: 11, fontWeight: 600,
    background: v === "normal" ? "#1a4a2a" : "#4a1a1a",
    color: v === "normal" ? "#4caf50" : "#ff6b6b",
  });

  const actionBtn = (color: string, bg: string): React.CSSProperties => ({
    padding: "8px 20px", borderRadius: 8, border: "none",
    background: bg, color, fontSize: 13, fontWeight: 700, cursor: "pointer",
    opacity: batchSubmitting ? 0.5 : 1,
  });

  const rowBtn = (color: string, bg: string): React.CSSProperties => ({
    padding: "6px 14px", borderRadius: 7, border: `1px solid ${color}40`,
    background: bg, color, fontSize: 12, fontWeight: 600, cursor: "pointer",
  });

  const GRID = "32px 1fr 90px 120px 130px 78px 90px";

  return (
    <div style={{ paddingBottom: selectedKeys.size > 0 ? 80 : 20 }}>
      <h1 style={{ color: "#fff", fontSize: 22, fontWeight: 700, margin: "0 0 4px" }}>
        {t("security.title")}
      </h1>
      <p style={{ color: "#555", fontSize: 13, margin: "0 0 20px" }}>
        {t("security.subtitle")}
      </p>

      {/* ── Toolbar ── */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginBottom: 16 }}>
        <span style={{ color: "#444", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, marginRight: 2 }}>Periode</span>
        {DAY_OPTIONS.map(d => (
          <button key={d} style={tab(days === d)} onClick={() => setDays(d)}>{d}d</button>
        ))}

        <div style={{ width: 1, height: 18, background: "#2a2a2a", margin: "0 6px" }} />
        <span style={{ color: "#444", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, marginRight: 2 }}>Grupper</span>
        <button style={tab(groupBy === "label")} onClick={() => setGroupBy("label")}>Objekt</button>
        <button style={tab(groupBy === "camera")} onClick={() => setGroupBy("camera")}>Kamera</button>

        <div style={{ width: 1, height: 18, background: "#2a2a2a", margin: "0 6px" }} />
        <span style={{ color: "#444", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, marginRight: 2 }}>Vis</span>
        <button style={tab(filterStatus === "pending")} onClick={() => setFilterStatus("pending")}>Ubehandlet</button>
        <button style={tab(filterStatus === "all")} onClick={() => setFilterStatus("all")}>Alle</button>
        <button style={tab(filterStatus === "reviewed")} onClick={() => setFilterStatus("reviewed")}>Behandlet</button>

        {knownLabels.length > 2 && (
          <>
            <div style={{ width: 1, height: 18, background: "#2a2a2a", margin: "0 6px" }} />
            <span style={{ color: "#444", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, marginRight: 2 }}>Objekt</span>
            {knownLabels.map(l => (
              <button key={l || "alle"} style={tab(labelFilter === l)} onClick={() => setLabelFilter(l)}>
                {l || "Alle"}
              </button>
            ))}
          </>
        )}

        <button
          style={{ display: "flex", alignItems: "center", gap: 5, padding: "5px 12px", borderRadius: 6, border: "1px solid #252525", background: "transparent", color: "#555", fontSize: 12, cursor: "pointer", marginLeft: "auto" }}
          onClick={load}
        >
          <RefreshCw size={12} /> Oppdater
        </button>
      </div>

      {/* ── Summary line ── */}
      {!loading && (
        <p style={{ color: "#555", fontSize: 12, marginBottom: 16 }}>
          <span style={{ color: "#a78bfa", fontWeight: 600 }}>{filteredEvents.length}</span> hendelser
          {filterStatus !== "all" && (
            <> · <span style={{ color: "#666" }}>{totalPending} ubehandlet totalt</span></>
          )}
          {" "}· siste <span style={{ color: "#666" }}>{days}</span> dager
        </p>
      )}

      {/* ── Loading / empty ── */}
      {loading ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "#444", fontSize: 13, paddingTop: 24 }}>
          <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} />
          Laster hendelser…
        </div>
      ) : filteredEvents.length === 0 ? (
        <div style={{ color: "#444", fontSize: 13, paddingTop: 24, textAlign: "center" }}>
          {t("security.empty")}
        </div>
      ) : (
        <>
          {grouped.map(([groupKey, evs]) => {
            const collapsed = collapsedGroups.has(groupKey);
            const allSel = evs.every(ev => selectedKeys.has(eventKey(ev)));
            const someSel = !allSel && evs.some(ev => selectedKeys.has(eventKey(ev)));
            const avgScore = Math.round(evs.reduce((s, e) => s + e.deviation_score, 0) / evs.length * 100);
            const pendingCount = evs.filter(ev => !(localCorrections[eventKey(ev)] ?? ev.correction)).length;

            return (
              <div key={groupKey} style={{ marginBottom: 10, border: "1px solid #222", borderRadius: 10, overflow: "hidden" }}>

                {/* Group header */}
                <div
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", background: "#141414", cursor: "pointer", userSelect: "none" }}
                  onClick={() => toggleGroupCollapse(groupKey)}
                >
                  <input
                    type="checkbox"
                    style={{ width: 15, height: 15, accentColor: "#646cff", cursor: "pointer", flexShrink: 0 }}
                    checked={allSel}
                    ref={el => { if (el) el.indeterminate = someSel; }}
                    onChange={e => toggleGroupSelect(evs, e)}
                    onClick={e => e.stopPropagation()}
                  />
                  <span style={{ color: "#ddd", fontSize: 13, fontWeight: 700, flex: 1 }}>{groupKey}</span>
                  <span style={groupBadge(scoreColor(avgScore))}>{avgScore}% avvik</span>
                  {pendingCount > 0 && (
                    <span style={groupBadge("#a78bfa")}>{pendingCount} ubehandlet</span>
                  )}
                  <span style={{ color: "#444", fontSize: 11 }}>{evs.length} total</span>
                  <span style={{ color: "#444", display: "flex", alignItems: "center" }}>
                    {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  </span>
                </div>

                {!collapsed && (
                  <>
                    {/* Column headers */}
                    <div style={{ display: "grid", gridTemplateColumns: GRID, padding: "5px 14px", gap: 8, background: "#0f0f0f", borderBottom: "1px solid #1e1e1e" }}>
                      {["", "Kamera", "Objekt", "Avvik", "Tidspunkt", "Dekning", "Status"].map((h, i) => (
                        <div key={i} style={{ color: "#383838", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 600 }}>{h}</div>
                      ))}
                    </div>

                    {/* Rows */}
                    {evs.map(ev => {
                      const k = eventKey(ev);
                      const correction = localCorrections[k] ?? ev.correction;
                      const expanded = expandedKeys.has(k);
                      const selected = selectedKeys.has(k);
                      const score = Math.round(ev.deviation_score * 100);
                      const conf = Math.round(ev.baseline_confidence * 100);
                      const isSub = submittingKeys.has(k);

                      return (
                        <div key={k}>
                          <div
                            style={{
                              display: "grid",
                              gridTemplateColumns: GRID,
                              alignItems: "center",
                              padding: "9px 14px",
                              gap: 8,
                              background: selected ? "#17172a" : expanded ? "#131320" : "transparent",
                              borderBottom: "1px solid #181818",
                              cursor: "pointer",
                              transition: "background 0.12s",
                            }}
                            onClick={() => toggleExpand(k)}
                          >
                            {/* Checkbox */}
                            <input
                              type="checkbox"
                              style={{ width: 14, height: 14, accentColor: "#646cff", cursor: "pointer" }}
                              checked={selected}
                              onChange={() => {}}
                              onClick={e => toggleSelect(k, e)}
                            />

                            {/* Camera name */}
                            <div style={{ color: "#ccc", fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {ev.camera_friendly}
                            </div>

                            {/* Label badge */}
                            <div>
                              <span style={{ display: "inline-block", padding: "2px 7px", borderRadius: 9, fontSize: 10, fontWeight: 700, background: "#a78bfa1a", color: "#a78bfa", border: "1px solid #a78bfa30", letterSpacing: 0.3 }}>
                                {ev.label}
                              </span>
                            </div>

                            {/* Score bar */}
                            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                              <div style={{ flex: 1, height: 4, background: "#1e1e1e", borderRadius: 2, overflow: "hidden" }}>
                                <div style={{ width: `${score}%`, height: "100%", background: scoreColor(score), borderRadius: 2, transition: "width 0.3s" }} />
                              </div>
                              <span style={{ color: scoreColor(score), fontSize: 11, fontWeight: 700, minWidth: 30, textAlign: "right" }}>
                                {score}%
                              </span>
                            </div>

                            {/* Timestamp */}
                            <div style={{ color: "#888", fontSize: 11, lineHeight: 1.4 }}>
                              <span style={{ display: "block", color: "#aaa", fontWeight: 500 }}>
                                {new Date(ev.ts).toLocaleString("nb-NO", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" })}
                              </span>
                            </div>

                            {/* Confidence */}
                            <div title={confTooltip(conf)}>
                              <span style={{ display: "inline-flex", alignItems: "center", gap: 3, padding: "2px 7px", borderRadius: 9, fontSize: 10, fontWeight: 600, background: confColor(conf) + "1a", color: confColor(conf), border: `1px solid ${confColor(conf)}30` }}>
                                <span style={{ width: 5, height: 5, borderRadius: "50%", background: confColor(conf), flexShrink: 0 }} />
                                {confLabel(conf)}
                              </span>
                            </div>

                            {/* Status */}
                            <div>
                              {correction ? (
                                <span style={verdictBadge(correction.verdict)}>
                                  {correction.verdict === "normal" ? "Normalt" : "Mistenkelig"}
                                </span>
                              ) : (
                                <span style={{ color: "#333", fontSize: 11 }}>—</span>
                              )}
                            </div>
                          </div>

                          {/* Expanded panel */}
                          {expanded && (
                            <div style={{ padding: "12px 14px 12px 54px", background: "#0d0d16", borderBottom: "1px solid #1e1e1e" }}>
                              <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
                                {/* Thumbnail */}
                                {ev.event_id && (
                                  <div style={{ flexShrink: 0 }}>
                                    <SnapshotImg eventId={ev.event_id} />
                                  </div>
                                )}

                                {/* Actions / verdict */}
                                <div style={{ flex: 1 }}>
                                  {correction ? (
                                    <div style={{ color: "#555", fontSize: 12 }}>
                                      Bedømt som{" "}
                                      <span style={{ color: correction.verdict === "normal" ? "#4caf50" : "#ff6b6b", fontWeight: 600 }}>
                                        {correction.verdict === "normal" ? "normalt" : "mistenkelig"}
                                      </span>
                                      {correction.by && <> av <span style={{ color: "#666" }}>{correction.by}</span></>}
                                      {correction.comment && <> · <em style={{ color: "#555" }}>«{correction.comment}»</em></>}
                                    </div>
                                  ) : (
                                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                                      <textarea
                                        style={{ width: "100%", padding: "7px 10px", borderRadius: 6, border: "1px solid #252525", background: "#111", color: "#bbb", fontSize: 12, resize: "vertical", boxSizing: "border-box", fontFamily: "inherit" }}
                                        rows={2}
                                        placeholder="Kommentar (valgfritt)"
                                        value={commentState[k] ?? ""}
                                        onChange={e => setCommentState(s => ({ ...s, [k]: e.target.value }))}
                                        onClick={e => e.stopPropagation()}
                                      />
                                      <div style={{ display: "flex", gap: 8 }}>
                                        <button style={{ ...rowBtn("#4caf50", "#1a4a2a"), opacity: isSub ? 0.5 : 1 }} disabled={isSub} onClick={e => submitSingle(ev, "normal", e)}>
                                          {isSub ? "Lagrer…" : "Dette er normalt"}
                                        </button>
                                        <button style={{ ...rowBtn("#ff6b6b", "#4a1a1a"), opacity: isSub ? 0.5 : 1 }} disabled={isSub} onClick={e => submitSingle(ev, "suspicious", e)}>
                                          Bekreft som mistenkelig
                                        </button>
                                      </div>
                                    </div>
                                  )}
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </>
                )}
              </div>
            );
          })}
        </>
      )}

      {/* ── Batch action bar ── */}
      {selectedKeys.size > 0 && (
        <div style={{ position: "fixed", bottom: 0, left: 220, right: 0, background: "#18181b", borderTop: "1px solid #2a2a2a", display: "flex", alignItems: "center", gap: 12, padding: "12px 28px", zIndex: 100 }}>
          <span style={{ color: "#666", fontSize: 13, flex: 1 }}>
            <span style={{ color: "#fff", fontWeight: 700 }}>{selectedKeys.size}</span> valgt
          </span>
          <button style={actionBtn("#4caf50", "#1a4a2a")} disabled={batchSubmitting} onClick={() => submitBatch("normal")}>
            {batchSubmitting ? "Lagrer…" : "Dette er normalt"}
          </button>
          <button style={actionBtn("#ff6b6b", "#4a1a1a")} disabled={batchSubmitting} onClick={() => submitBatch("suspicious")}>
            Bekreft som mistenkelige
          </button>
          <button
            style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #2a2a2a", background: "transparent", color: "#555", fontSize: 13, cursor: "pointer" }}
            onClick={() => setSelectedKeys(new Set())}
          >
            Avbryt
          </button>
        </div>
      )}
    </div>
  );
}
