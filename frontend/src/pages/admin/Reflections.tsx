import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import axios from "axios";
import {
  apiListUsers,
  apiReflectionDates,
  apiReflectionContent,
  apiGetMeetingTopic,
  apiSetMeetingTopic,
  apiGetMeetingComment,
  apiSetMeetingComment,
  type KaareUser,
} from "../../services/api";

const BASE = `http://${window.location.hostname}:8000`;
const authHeaders = () => ({ Authorization: `Bearer ${sessionStorage.getItem("kaare_token")}` });
const apiGet  = (path: string) => axios.get(BASE + path,  { headers: authHeaders() });
const apiPost = (path: string) => axios.post(BASE + path, {}, { headers: authHeaders() });

type MeetingType = "reflection" | "dev";

const PRIVATE_REFLECTION_ROLES = new Set(["young_adult", "adult"]);

interface MeetingStatus {
  running: boolean;
  progress: number;
  round: number;
  max_rounds: number;
  step: string;
  log: string[];
  started_at: string | null;
  source: "manual" | "timer" | null;
}

const MEETING_COLORS: Record<string, string> = {
  "KÅRE":        "#646cff",
  "MISS KÅRE":   "#f06292",
  "MECHANIC": "#ff9800",
  "ONLINE":      "#4caf50",
  "MØTELEDER":   "#00bcd4",
};

const AGENT_LABELS: Record<string, string> = {
  "**[Kåre]**":        "KÅRE",
  "**[Miss Kåre]**":   "MISS KÅRE",
  "**[Mechanic]**": "MECHANIC",
  "**[Online]**":      "ONLINE",
  "**[Møteleder]**":   "MØTELEDER",
};

const ACCENT: Record<MeetingType, string> = {
  reflection: "#f06292",
  dev:        "#ff9800",
};

// ── PIN-modal ─────────────────────────────────────────────────────────────────
function PinModal({
  username,
  displayName,
  onConfirm,
  onCancel,
}: {
  username: string;
  displayName: string;
  onConfirm: (pin: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const submit = () => {
    if (pin.length < 4) { setError(t("reflections.pin_error_short")); return; }
    setError("");
    onConfirm(pin);
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
    }}>
      <div style={{
        background: "#1a1a1a", border: "1px solid #333", borderRadius: 14,
        padding: "32px 36px", width: 320, textAlign: "center",
      }}>
        <div style={{ fontSize: 28, marginBottom: 8 }}>🔒</div>
        <div style={{ color: "#fff", fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
          {displayName}
        </div>
        <div style={{ color: "#666", fontSize: 12, marginBottom: 24 }}>
          {t("reflections.pin_subtitle", { username })}
        </div>
        <input
          ref={inputRef}
          type="password"
          inputMode="numeric"
          maxLength={8}
          value={pin}
          onChange={e => { setPin(e.target.value); setError(""); }}
          onKeyDown={e => e.key === "Enter" && submit()}
          placeholder="PIN"
          style={{
            width: "100%", padding: "10px 14px", borderRadius: 8,
            border: error ? "1px solid #f06292" : "1px solid #333",
            background: "#111", color: "#fff", fontSize: 18,
            textAlign: "center", letterSpacing: 4, boxSizing: "border-box",
            outline: "none",
          }}
        />
        {error && <div style={{ color: "#f06292", fontSize: 12, marginTop: 6 }}>{error}</div>}
        <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
          <button onClick={onCancel} style={{
            flex: 1, padding: "9px 0", borderRadius: 8,
            border: "1px solid #333", background: "transparent",
            color: "#666", fontSize: 13, cursor: "pointer",
          }}>
            {t("reflections.pin_cancel")}
          </button>
          <button onClick={submit} style={{
            flex: 1, padding: "9px 0", borderRadius: 8,
            border: "1px solid #f06292", background: "#f0629222",
            color: "#f06292", fontSize: 13, fontWeight: 600, cursor: "pointer",
          }}>
            {t("reflections.pin_open")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Progress-panel ────────────────────────────────────────────────────────────
function ProgressPanel({
  type, status, onStart,
}: {
  type: MeetingType;
  status: MeetingStatus;
  onStart: () => void;
}) {
  const { t } = useTranslation();
  const color = ACCENT[type];
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [status.log]);

  if (status.running) {
    const pct = Math.max(2, status.progress);
    const sourceLabel = status.source === "timer" ? t("reflections.source_auto") : t("reflections.source_manual");
    const startedTime = status.started_at
      ? new Date(status.started_at).toLocaleTimeString("nb-NO", { hour: "2-digit", minute: "2-digit" })
      : "";
    return (
      <div style={{ background: "#111", borderRadius: 12, padding: "16px 20px", border: `1px solid ${color}33`, marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, boxShadow: `0 0 6px ${color}`, display: "inline-block", animation: "pulse 1.5s ease-in-out infinite" }} />
            <span style={{ color, fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>{t("reflections.running")}</span>
            <span style={{ color: "#555", fontSize: 11 }}>· {sourceLabel}</span>
            {startedTime && <span style={{ color: "#555", fontSize: 11 }}>· {t("reflections.started_at", { time: startedTime })}</span>}
          </div>
          <span style={{ color: "#555", fontSize: 11 }}>
            {status.round > 0 ? t("reflections.round", { round: status.round, max: status.max_rounds }) : ""}
          </span>
        </div>
        <div style={{ background: "#1a1a1a", borderRadius: 6, height: 8, overflow: "hidden", marginBottom: 8 }}>
          <div style={{ height: "100%", width: `${pct}%`, borderRadius: 6, background: `linear-gradient(90deg, ${color}99, ${color})`, transition: "width 1.2s ease" }} />
        </div>
        <div style={{ color: "#555", fontSize: 11, marginBottom: 10, minHeight: 16 }}>{status.step || t("reflections.waiting_model")}</div>
        {status.log.length > 0 && (
          <div ref={logRef} style={{ background: "#0a0a0a", borderRadius: 8, padding: "10px 12px", maxHeight: 120, overflowY: "auto", fontFamily: "monospace" }}>
            {status.log.slice(-8).map((line, i) => (
              <div key={i} style={{ color: line.includes("ERROR") || line.includes("feilet") ? "#f06292" : line.includes("===") ? color : "#555", fontSize: 11, lineHeight: 1.6 }}>{line}</div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ marginBottom: 20 }}>
      <button onClick={onStart} style={{
        padding: "9px 20px", borderRadius: 8, border: `1px solid ${color}55`,
        background: `${color}11`, color, fontSize: 13, fontWeight: 600,
        cursor: "pointer", display: "flex", alignItems: "center", gap: 8,
      }}>
        <span style={{ fontSize: 16 }}>▶</span>
        {type === "reflection" ? t("reflections.start_reflection") : t("reflections.start_dev")}
      </button>
    </div>
  );
}

// ── Topic box ─────────────────────────────────────────────────────────────────
function TopicBox({ type }: { type: MeetingType }) {
  const { t } = useTranslation();
  const color = ACCENT[type];
  const [topic, setTopic]   = useState("");
  const [saved, setSaved]   = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setSaved(false);
    apiGetMeetingTopic(type).then(r => setTopic(r.topic)).catch(() => {});
  }, [type]);

  const save = async () => {
    setSaving(true);
    try {
      await apiSetMeetingTopic(type, topic);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ marginBottom: 16, background: "#111", borderRadius: 10, padding: "12px 16px", border: `1px solid ${color}22` }}>
      <div style={{ color: "#555", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
        {t("reflections.topic_title")}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
        <textarea
          value={topic}
          onChange={e => { setTopic(e.target.value); setSaved(false); }}
          onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) save(); }}
          placeholder={t("reflections.topic_ph")}
          rows={2}
          style={{
            flex: 1, background: "#0d0d0d", border: `1px solid ${color}33`, borderRadius: 8,
            color: "#ccc", fontSize: 13, padding: "8px 10px", resize: "none",
            outline: "none", fontFamily: "inherit", lineHeight: 1.5,
          }}
        />
        <button
          onClick={save}
          disabled={saving}
          style={{
            padding: "8px 14px", borderRadius: 8, border: `1px solid ${color}55`,
            background: saved ? `${color}22` : "#1a1a1a",
            color: saved ? color : "#666",
            fontSize: 13, fontWeight: 600, cursor: "pointer",
            whiteSpace: "nowrap", transition: "all 0.2s",
            minWidth: 60,
          }}
        >
          {saved ? "✓ OK" : t("reflections.topic_save")}
        </button>
      </div>
    </div>
  );
}

// ── Meeting settings panel ────────────────────────────────────────────────────
// ── Comment box ───────────────────────────────────────────────────────────────
function CommentBox({ type, date }: { type: MeetingType; date: string }) {
  const { t } = useTranslation();
  const color = ACCENT[type];
  const [comment, setComment] = useState("");
  const [saved, setSaved]     = useState(false);
  const [saving, setSaving]   = useState(false);

  useEffect(() => {
    setSaved(false);
    setComment("");
    apiGetMeetingComment(type, date).then(r => setComment(r.comment)).catch(() => {});
  }, [type, date]);

  const save = async () => {
    setSaving(true);
    try {
      await apiSetMeetingComment(type, date, comment);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ marginTop: 24, borderTop: "1px solid #222", paddingTop: 16 }}>
      <div style={{ color: "#555", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
        {t("reflections.comment_title")}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
        <textarea
          value={comment}
          onChange={e => { setComment(e.target.value); setSaved(false); }}
          onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) save(); }}
          placeholder={t("reflections.comment_ph")}
          rows={3}
          style={{
            flex: 1, background: "#0d0d0d", border: `1px solid ${color}33`, borderRadius: 8,
            color: "#ccc", fontSize: 13, padding: "8px 10px", resize: "none",
            outline: "none", fontFamily: "inherit", lineHeight: 1.5,
          }}
        />
        <button
          onClick={save}
          disabled={saving}
          style={{
            padding: "8px 14px", borderRadius: 8, border: `1px solid ${color}55`,
            background: saved ? `${color}22` : "#1a1a1a",
            color: saved ? color : "#666",
            fontSize: 13, fontWeight: 600, cursor: "pointer",
            whiteSpace: "nowrap", transition: "all 0.2s",
            minWidth: 60,
          }}
        >
          {saved ? "✓ OK" : t("reflections.comment_save")}
        </button>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function Reflections() {
  const { t } = useTranslation();
  const [meetingType, setMeetingType] = useState<MeetingType>("reflection");

  const [users, setUsers]               = useState<KaareUser[]>([]);
  const [selectedUser, setSelectedUser] = useState<KaareUser | null>(null);

  const [dates, setDates]       = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent]   = useState("");
  const [loading, setLoading]   = useState(false);

  const [pinTarget, setPinTarget] = useState<{ date: string } | null>(null);
  const [pinError, setPinError]   = useState(false);

  const [status, setStatus] = useState<Record<MeetingType, MeetingStatus>>({
    reflection: { running: false, progress: 0, round: 0, max_rounds: 6, step: "", log: [], started_at: null, source: null },
    dev:        { running: false, progress: 0, round: 0, max_rounds: 6, step: "", log: [], started_at: null, source: null },
  });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    apiListUsers().then(all => {
      const activeUsers = all.filter(u =>
        u.is_active &&
        u.username !== "admin" &&
        !PRIVATE_REFLECTION_ROLES.has(u.role)
      );
      setUsers(activeUsers);
      if (activeUsers.length > 0) setSelectedUser(activeUsers[0]);
    });
  }, []);

  const anyRunning = status.reflection.running || status.dev.running;
  useEffect(() => {
    const poll = async () => {
      try { const r = await apiGet("/api/meetings/status"); setStatus(r.data); } catch { /* ignore */ }
    };
    poll();
    const interval = anyRunning ? 3000 : 30000;
    pollRef.current = setInterval(poll, interval);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [anyRunning]);

  useEffect(() => {
    setDates([]); setContent(""); setSelected(null);
    if (meetingType === "reflection") {
      if (!selectedUser) return;
      apiReflectionDates(selectedUser.username).then(d => setDates(d));
    } else {
      apiGet("/api/dev-meetings").then(r => {
        setDates(r.data);
        if (r.data.length > 0) loadDevDate(r.data[0]);
      });
    }
  }, [meetingType, selectedUser]);

  const prevRunning = useRef<Record<MeetingType, boolean>>({ reflection: false, dev: false });
  useEffect(() => {
    for (const mt of ["reflection", "dev"] as MeetingType[]) {
      if (prevRunning.current[mt] && !status[mt].running) {
        if (mt === "reflection" && selectedUser) {
          apiReflectionDates(selectedUser.username).then(d => setDates(d));
        } else if (mt === "dev") {
          apiGet("/api/dev-meetings").then(r => setDates(r.data));
        }
      }
      prevRunning.current[mt] = status[mt].running;
    }
  }, [status]);

  const loadDevDate = async (date: string) => {
    setSelected(date); setLoading(true);
    try {
      const r = await apiGet(`/api/dev-meetings/${date}`);
      setContent(r.data.content);
    } finally { setLoading(false); }
  };

  const handleReflectionDateClick = (date: string) => {
    setSelected(date);
    setPinTarget({ date });
    setPinError(false);
  };

  const handlePinConfirm = async (pin: string) => {
    if (!selectedUser || !pinTarget) return;
    try {
      const result = await apiReflectionContent(selectedUser.username, pinTarget.date, pin);
      setContent(result.content);
      setPinTarget(null);
    } catch {
      setPinError(true);
    }
  };

  const startMeeting = async () => {
    const endpoint = meetingType === "reflection" ? "/api/reflections/start" : "/api/dev-meetings/start";
    try { await apiPost(endpoint); } catch { /* ignore */ }
  };

  const renderContent = (md: string) => {
    return md.split("\n").map((line, i) => {
      if (line.startsWith("# "))
        return <h1 key={i} style={{ color: "#fff", fontSize: 20, fontWeight: 700, margin: "0 0 16px" }}>{line.slice(2)}</h1>;
      if (line.startsWith("## "))
        return <h2 key={i} style={{ color: "#aaa", fontSize: 13, fontWeight: 600, textTransform: "uppercase", letterSpacing: 1, margin: "20px 0 8px" }}>{line.slice(3)}</h2>;
      for (const [tag, label] of Object.entries(AGENT_LABELS)) {
        if (line.startsWith(tag)) {
          const color = MEETING_COLORS[label] ?? "#888";
          return <div key={i} style={{ color, fontSize: 12, fontWeight: 700, margin: "16px 0 4px" }}>{label}</div>;
        }
      }
      if (line.startsWith("- ")) {
        const isForslag = line.toLowerCase().startsWith("- forslag:");
        return <div key={i} style={{ color: isForslag ? "#ff9800" : "#888", fontSize: 13, padding: "2px 0 2px 12px", fontWeight: isForslag ? 600 : 400 }}>{isForslag ? "▶ " : "· "}{line.slice(2)}</div>;
      }
      if (line.trim() === "") return <div key={i} style={{ height: 6 }} />;
      return <div key={i} style={{ color: "#ccc", fontSize: 14, lineHeight: 1.6 }}>{line}</div>;
    });
  };

  const tabStyle = (active: boolean, color: string) => ({
    padding: "8px 18px", borderRadius: 8, border: "none", cursor: "pointer",
    fontSize: 13, fontWeight: 600,
    background: active ? color + "22" : "transparent",
    color: active ? color : "#555",
    outline: active ? `1px solid ${color}44` : "none",
  } as React.CSSProperties);

  return (
    <>
      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }`}</style>

      {pinTarget && selectedUser && (
        <PinModal
          username={selectedUser.username}
          displayName={selectedUser.display_name}
          onConfirm={handlePinConfirm}
          onCancel={() => { setPinTarget(null); setSelected(null); }}
        />
      )}
      {pinError && (
        <div style={{ position: "fixed", bottom: 24, right: 24, background: "#2a1a1a", border: "1px solid #f06292", borderRadius: 10, padding: "12px 18px", color: "#f06292", fontSize: 13, zIndex: 999 }}>
          {t("reflections.pin_wrong")}
          <button onClick={() => { setPinError(false); if (pinTarget && selectedUser) setPinTarget(pinTarget); }} style={{ marginLeft: 12, background: "none", border: "none", color: "#f06292", cursor: "pointer", fontWeight: 700 }}>
            {t("reflections.pin_retry")}
          </button>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 80px)", gap: 0 }}>

        {/* Tab selector */}
        <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
          <button style={tabStyle(meetingType === "reflection", "#f06292")} onClick={() => setMeetingType("reflection")}>
            {t("reflections.reflection_tab")} <span style={{ fontSize: 11, opacity: 0.6 }}>{t("reflections.reflection_tab_sub")}</span>
          </button>
          <button style={tabStyle(meetingType === "dev", "#ff9800")} onClick={() => setMeetingType("dev")}>
            {t("reflections.dev_tab")} <span style={{ fontSize: 11, opacity: 0.6 }}>{t("reflections.dev_tab_sub")}</span>
          </button>
        </div>

        {/* User selector — reflection only */}
        {meetingType === "reflection" && users.length === 0 && (
          <div style={{ color: "#555", fontSize: 12, marginBottom: 16, fontStyle: "italic" }}>
            {t("reflections.no_eligible_reflection_users")}
          </div>
        )}
        {meetingType === "reflection" && users.length > 1 && (
          <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            {users.map(u => (
              <button key={u.username} onClick={() => setSelectedUser(u)} style={{
                padding: "6px 14px", borderRadius: 20, border: "none", cursor: "pointer",
                fontSize: 13, fontWeight: 600,
                background: selectedUser?.username === u.username ? "#f0629222" : "#1a1a1a",
                color: selectedUser?.username === u.username ? "#f06292" : "#555",
                outline: selectedUser?.username === u.username ? "1px solid #f0629244" : "none",
              }}>
                {u.avatar && <span style={{ marginRight: 5 }}>{u.avatar}</span>}
                {u.display_name}
              </button>
            ))}
          </div>
        )}

        {/* Topic for next meeting */}
        <TopicBox type={meetingType} />

        {/* Progress / Start button */}
        <ProgressPanel type={meetingType} status={status[meetingType]} onStart={startMeeting} />

        <div style={{ display: "flex", gap: 24, flex: 1, minHeight: 0 }}>
          {/* Sidebar — dates */}
          <div style={{ width: 160, flexShrink: 0, overflowY: "auto" }}>
            <div style={{ color: "#555", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 12 }}>{t("reflections.meetings_label")}</div>
            {dates.length === 0 && <div style={{ color: "#444", fontSize: 13 }}>{t("reflections.no_dates")}</div>}
            {dates.map(d => (
              <button key={d} onClick={() => meetingType === "reflection" ? handleReflectionDateClick(d) : loadDevDate(d)} style={{
                display: "block", width: "100%", padding: "8px 10px", marginBottom: 4,
                borderRadius: 8, border: "none", textAlign: "left", cursor: "pointer", fontSize: 13,
                background: selected === d ? "#1e1e3a" : "transparent",
                color: selected === d ? "#fff" : "#666",
              }}>
                {d}
                {meetingType === "reflection" && <span style={{ float: "right", opacity: 0.4, fontSize: 11 }}>🔒</span>}
              </button>
            ))}
          </div>

          {/* Content */}
          <div style={{ flex: 1, overflowY: "auto", background: "#111", borderRadius: 12, padding: "24px 28px" }}>
            {loading && <div style={{ color: "#555" }}>{t("reflections.loading")}</div>}
            {!loading && content && renderContent(content)}
            {!loading && !content && (
              <div style={{ color: "#444", fontSize: 14 }}>
                {meetingType === "reflection"
                  ? dates.length === 0
                    ? t("reflections.empty_reflection_no_meetings")
                    : t("reflections.empty_reflection_select")
                  : t("reflections.empty_dev_no_meetings")}
              </div>
            )}
            {!loading && content && selected && (
              <CommentBox type={meetingType} date={selected} />
            )}
          </div>
        </div>
      </div>
    </>
  );
}
