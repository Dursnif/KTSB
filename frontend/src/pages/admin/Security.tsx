import { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw, ChevronDown, ChevronRight, Undo2, ArrowUp, ArrowDown } from "lucide-react";
import { api, apiGetNormalcyEvents, apiPostNormalcyCorrect, apiDeleteNormalcyCorrect, type NormalcyEvent } from "../../services/api";

const UNDO_TIMEOUT_MS = 8000;

type UndoItem = { key: string; ev: NormalcyEvent; prev: NormalcyEvent["correction"] };
type LastAction = { items: UndoItem[]; verdict: string };

type SortColumn = "camera" | "label" | "deviation" | "time" | "confidence" | "status";
type SortDir = "asc" | "desc";

function compareEvents(
  a: NormalcyEvent, b: NormalcyEvent, column: SortColumn, dir: SortDir,
  localCorrections: Record<string, NormalcyEvent["correction"]>,
): number {
  let cmp = 0;
  switch (column) {
    case "camera": cmp = a.camera_friendly.localeCompare(b.camera_friendly); break;
    case "label": cmp = a.label.localeCompare(b.label); break;
    case "deviation": cmp = a.deviation_score - b.deviation_score; break;
    case "time": cmp = new Date(a.ts).getTime() - new Date(b.ts).getTime(); break;
    case "confidence": cmp = a.baseline_confidence - b.baseline_confidence; break;
    case "status": {
      const va = (localCorrections[eventKey(a)] ?? a.correction)?.verdict ?? "";
      const vb = (localCorrections[eventKey(b)] ?? b.correction)?.verdict ?? "";
      cmp = va.localeCompare(vb);
      break;
    }
  }
  return dir === "asc" ? cmp : -cmp;
}

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

type Tr = (key: string, opts?: Record<string, unknown>) => string;

function confLabel(pct: number, t: Tr) {
  if (pct < 20) return t("security.conf_low");
  if (pct < 50) return t("security.conf_partial");
  return t("security.conf_good");
}

function confTooltip(pct: number, t: Tr) {
  const prefix = t("security.confidence", { pct });
  if (pct < 20) return `${prefix} — ${t("security.conf_reason_low")}`;
  if (pct < 50) return `${prefix} — ${t("security.conf_reason_partial")}`;
  return `${prefix} — ${t("security.conf_reason_good")}`;
}

export default function Security() {
  const { t, i18n } = useTranslation();
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
  const [lastAction, setLastAction] = useState<LastAction | null>(null);
  const [undoing, setUndoing] = useState(false);
  const undoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [sortColumn, setSortColumn] = useState<SortColumn | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const handleSort = (col: SortColumn) => {
    if (sortColumn === col) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSortColumn(col);
      setSortDir("asc");
    }
  };

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

  useEffect(() => () => { if (undoTimerRef.current) clearTimeout(undoTimerRef.current); }, []);

  const armUndo = useCallback((items: UndoItem[], verdict: string) => {
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    setLastAction({ items, verdict });
    undoTimerRef.current = setTimeout(() => setLastAction(null), UNDO_TIMEOUT_MS);
  }, []);

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
    const entries = Object.entries(groups).sort((a, b) => b[1].length - a[1].length);
    if (sortColumn) {
      for (const [, evs] of entries) {
        evs.sort((a, b) => compareEvents(a, b, sortColumn, sortDir, localCorrections));
      }
    }
    return entries;
  }, [filteredEvents, groupBy, sortColumn, sortDir, localCorrections]);

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
    const prev = localCorrections[k] ?? ev.correction;
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
      armUndo([{ key: k, ev, prev }], verdict);
    } finally {
      setSubmittingKeys(s => { const n = new Set(s); n.delete(k); return n; });
    }
  };

  const submitBatch = async (verdict: string) => {
    if (batchSubmitting) return;
    setBatchSubmitting(true);
    const done: UndoItem[] = [];
    for (const k of [...selectedKeys]) {
      const ev = events.find(e => eventKey(e) === k);
      if (!ev) continue;
      const prev = localCorrections[k] ?? ev.correction;
      try {
        await apiPostNormalcyCorrect({
          source_key: ev.source_key,
          hour_bucket: ev.hour_bucket,
          weekday: ev.weekday,
          verdict,
          comment: commentState[k] ?? "",
        });
        applyCorrection(k, verdict, commentState[k] ?? "");
        done.push({ key: k, ev, prev });
      } catch { /* continue with others */ }
    }
    setSelectedKeys(new Set());
    setBatchSubmitting(false);
    if (done.length > 0) armUndo(done, verdict);
  };

  const handleUndo = async () => {
    if (!lastAction || undoing) return;
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    setUndoing(true);
    try {
      for (const item of lastAction.items) {
        if (item.prev) {
          await apiPostNormalcyCorrect({
            source_key: item.ev.source_key,
            hour_bucket: item.ev.hour_bucket,
            weekday: item.ev.weekday,
            verdict: item.prev.verdict,
            comment: item.prev.comment ?? "",
          });
        } else {
          await apiDeleteNormalcyCorrect({
            source_key: item.ev.source_key,
            hour_bucket: item.ev.hour_bucket,
            weekday: item.ev.weekday,
          });
        }
      }
      setLocalCorrections(c => {
        const n = { ...c };
        for (const item of lastAction.items) {
          if (item.prev) n[item.key] = item.prev;
          else delete n[item.key];
        }
        return n;
      });
    } finally {
      setUndoing(false);
      setLastAction(null);
    }
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
        <span style={{ color: "#444", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, marginRight: 2 }}>{t("security.period")}</span>
        {DAY_OPTIONS.map(d => (
          <button key={d} style={tab(days === d)} onClick={() => setDays(d)}>{d}d</button>
        ))}

        <div style={{ width: 1, height: 18, background: "#2a2a2a", margin: "0 6px" }} />
        <span style={{ color: "#444", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, marginRight: 2 }}>{t("security.group_by")}</span>
        <button style={tab(groupBy === "label")} onClick={() => setGroupBy("label")}>{t("security.group_object")}</button>
        <button style={tab(groupBy === "camera")} onClick={() => setGroupBy("camera")}>{t("security.group_camera")}</button>

        <div style={{ width: 1, height: 18, background: "#2a2a2a", margin: "0 6px" }} />
        <span style={{ color: "#444", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, marginRight: 2 }}>{t("common.show")}</span>
        <button style={tab(filterStatus === "pending")} onClick={() => setFilterStatus("pending")}>{t("security.status_pending")}</button>
        <button style={tab(filterStatus === "all")} onClick={() => setFilterStatus("all")}>{t("security.status_all")}</button>
        <button style={tab(filterStatus === "reviewed")} onClick={() => setFilterStatus("reviewed")}>{t("security.status_reviewed")}</button>

        {knownLabels.length > 2 && (
          <>
            <div style={{ width: 1, height: 18, background: "#2a2a2a", margin: "0 6px" }} />
            <span style={{ color: "#444", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, marginRight: 2 }}>{t("security.group_object")}</span>
            {knownLabels.map(l => (
              <button key={l || "alle"} style={tab(labelFilter === l)} onClick={() => setLabelFilter(l)}>
                {l || t("security.status_all")}
              </button>
            ))}
          </>
        )}

        <button
          style={{ display: "flex", alignItems: "center", gap: 5, padding: "5px 12px", borderRadius: 6, border: "1px solid #252525", background: "transparent", color: "#555", fontSize: 12, cursor: "pointer", marginLeft: "auto" }}
          onClick={load}
        >
          <RefreshCw size={12} /> {t("common.update")}
        </button>
      </div>

      {/* ── Summary line ── */}
      {!loading && (
        <p style={{ color: "#555", fontSize: 12, marginBottom: 16 }}>
          <span style={{ color: "#a78bfa", fontWeight: 600 }}>{filteredEvents.length}</span> {t("security.events_suffix")}
          {filterStatus !== "all" && (
            <> · <span style={{ color: "#666" }}>{totalPending} {t("security.pending_total_suffix")}</span></>
          )}
          {" "}· {t("security.last_days_prefix")} <span style={{ color: "#666" }}>{days}</span> {t("security.days_suffix")}
        </p>
      )}

      {/* ── Loading / empty ── */}
      {loading ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "#444", fontSize: 13, paddingTop: 24 }}>
          <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} />
          {t("security.loading")}
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
                  <span style={groupBadge(scoreColor(avgScore))}>{t("security.deviation", { score: avgScore })}</span>
                  {pendingCount > 0 && (
                    <span style={groupBadge("#a78bfa")}>{pendingCount} {t("security.status_pending")}</span>
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
                      {([
                        { key: null, label: "" },
                        { key: "camera", label: t("security.group_camera") },
                        { key: "label", label: t("security.group_object") },
                        { key: "deviation", label: t("security.col_deviation") },
                        { key: "time", label: t("security.col_time") },
                        { key: "confidence", label: t("security.col_confidence") },
                        { key: "status", label: t("security.col_status") },
                      ] as { key: SortColumn | null; label: string }[]).map((col, i) => (
                        <div
                          key={i}
                          onClick={col.key ? () => handleSort(col.key as SortColumn) : undefined}
                          style={{
                            display: "flex", alignItems: "center", gap: 3,
                            color: sortColumn === col.key && col.key ? "#a78bfa" : "#383838",
                            fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 600,
                            cursor: col.key ? "pointer" : "default", userSelect: "none",
                          }}
                        >
                          {col.label}
                          {sortColumn === col.key && col.key && (
                            sortDir === "asc" ? <ArrowUp size={10} /> : <ArrowDown size={10} />
                          )}
                        </div>
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
                                {new Date(ev.ts).toLocaleString(i18n.language === "nb" ? "nb-NO" : i18n.language, { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" })}
                              </span>
                            </div>

                            {/* Confidence */}
                            <div title={confTooltip(conf, t)}>
                              <span style={{ display: "inline-flex", alignItems: "center", gap: 3, padding: "2px 7px", borderRadius: 9, fontSize: 10, fontWeight: 600, background: confColor(conf) + "1a", color: confColor(conf), border: `1px solid ${confColor(conf)}30` }}>
                                <span style={{ width: 5, height: 5, borderRadius: "50%", background: confColor(conf), flexShrink: 0 }} />
                                {confLabel(conf, t)}
                              </span>
                            </div>

                            {/* Status */}
                            <div>
                              {correction ? (
                                <span style={verdictBadge(correction.verdict)}>
                                  {correction.verdict === "normal" ? t("security.verdict_normal") : t("security.verdict_suspicious")}
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
                                      {t("security.judged_as")}{" "}
                                      <span style={{ color: correction.verdict === "normal" ? "#4caf50" : "#ff6b6b", fontWeight: 600 }}>
                                        {correction.verdict === "normal" ? t("security.judged_normal") : t("security.judged_suspicious")}
                                      </span>
                                      {correction.by && <> {t("security.by", { by: correction.by })}</>}
                                      {correction.comment && <> · <em style={{ color: "#555" }}>«{correction.comment}»</em></>}
                                    </div>
                                  ) : (
                                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                                      <textarea
                                        style={{ width: "100%", padding: "7px 10px", borderRadius: 6, border: "1px solid #252525", background: "#111", color: "#bbb", fontSize: 12, resize: "vertical", boxSizing: "border-box", fontFamily: "inherit" }}
                                        rows={2}
                                        placeholder={t("security.comment_placeholder")}
                                        value={commentState[k] ?? ""}
                                        onChange={e => setCommentState(s => ({ ...s, [k]: e.target.value }))}
                                        onClick={e => e.stopPropagation()}
                                      />
                                      <div style={{ display: "flex", gap: 8 }}>
                                        <button style={{ ...rowBtn("#4caf50", "#1a4a2a"), opacity: isSub ? 0.5 : 1 }} disabled={isSub} onClick={e => submitSingle(ev, "normal", e)}>
                                          {isSub ? t("common.saving") : t("security.mark_normal")}
                                        </button>
                                        <button style={{ ...rowBtn("#ff6b6b", "#4a1a1a"), opacity: isSub ? 0.5 : 1 }} disabled={isSub} onClick={e => submitSingle(ev, "suspicious", e)}>
                                          {t("security.mark_suspicious")}
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
            <span style={{ color: "#fff", fontWeight: 700 }}>{selectedKeys.size}</span> {t("security.selected_count_suffix")}
          </span>
          <button style={actionBtn("#4caf50", "#1a4a2a")} disabled={batchSubmitting} onClick={() => submitBatch("normal")}>
            {batchSubmitting ? t("common.saving") : t("security.mark_normal")}
          </button>
          <button style={actionBtn("#ff6b6b", "#4a1a1a")} disabled={batchSubmitting} onClick={() => submitBatch("suspicious")}>
            {t("security.mark_suspicious_batch")}
          </button>
          <button
            style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #2a2a2a", background: "transparent", color: "#555", fontSize: 13, cursor: "pointer" }}
            onClick={() => setSelectedKeys(new Set())}
          >
            {t("common.cancel")}
          </button>
        </div>
      )}

      {/* ── Undo toast ── */}
      {lastAction && (
        <div
          style={{
            position: "fixed",
            bottom: selectedKeys.size > 0 ? 92 : 24,
            right: 24,
            zIndex: 110,
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 16px",
            borderRadius: 8,
            background: "#18181b",
            border: "1px solid #2a2a2a",
            boxShadow: "0 4px 16px rgba(0,0,0,0.4)",
          }}
        >
          <span style={{ color: "#bbb", fontSize: 13 }}>
            {lastAction.items.length > 1
              ? t("security.undo_snackbar_batch", {
                  count: lastAction.items.length,
                  verdict: lastAction.verdict === "normal" ? t("security.verdict_normal") : t("security.verdict_suspicious"),
                })
              : t("security.undo_snackbar_single", {
                  verdict: lastAction.verdict === "normal" ? t("security.verdict_normal") : t("security.verdict_suspicious"),
                })}
          </span>
          <button
            style={{ display: "flex", alignItems: "center", gap: 5, padding: "5px 12px", borderRadius: 6, border: "1px solid #a78bfa40", background: "#a78bfa1a", color: "#a78bfa", fontSize: 12, fontWeight: 600, cursor: "pointer", opacity: undoing ? 0.5 : 1 }}
            disabled={undoing}
            onClick={handleUndo}
          >
            <Undo2 size={12} /> {t("security.undo")}
          </button>
        </div>
      )}
    </div>
  );
}
