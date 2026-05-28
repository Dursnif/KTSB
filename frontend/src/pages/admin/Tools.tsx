import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "@/i18n";

interface Timer {
  id: string;
  prompt: string;
  fires_at: string;
  remaining_seconds: number;
  notify: boolean;
  repeat?: string | null;
  at_time?: string | null;
}

interface ToolCall {
  ts: string;
  source: string;
  tool?: string;
  event?: string;
  args?: Record<string, unknown>;
  result_preview?: string;
  duration_ms?: number;
  timer_id?: string;
  prompt_preview?: string;
  error?: string;
}

const SOURCE_COLOR: Record<string, string> = {
  kare:         "#6c8ebf",
  timer:        "#d79b00",
  miss_kare:    "#c084fc",
  miss_library: "#5ba8a0",
  mechanic:  "#82b366",
  jing:         "#82b366",
  jang:         "#9673a6",
};

function sourceColor(s: string) {
  return SOURCE_COLOR[s] ?? "#888";
}

function formatRemaining(secs: number, firingSoonLabel: string): string {
  if (secs <= 0) return firingSoonLabel;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}t ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatTime(ts: string): string {
  try {
    const locale = i18n.language === "nb" ? "nb-NO" : i18n.language;
    return new Date(ts).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

function eventLabel(call: ToolCall, unknownLabel: string): string {
  if (call.tool) return call.tool;
  if (call.event) return call.event;
  return unknownLabel;
}

function eventColor(call: ToolCall): string {
  const label = call.tool ?? call.event ?? "";
  if (label.includes("timer"))                               return SOURCE_COLOR.timer;
  if (label.includes("styr"))                               return "#e07070";
  if (label.includes("frøken") || label.includes("library")) return SOURCE_COLOR.miss_library;
  if (label.includes("søk"))                                 return SOURCE_COLOR.jing;
  return "#555";
}

export default function Tools() {
  const { t } = useTranslation();
  const [timers, setTimers] = useState<Timer[]>([]);
  const [calls, setCalls] = useState<ToolCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [, setTick] = useState(0);

  async function fetchData() {
    try {
      const r = await fetch(`http://${window.location.hostname}:8000/api/tools/recent?n=80`);
      const data = await r.json();
      setTimers(data.timers ?? []);
      setCalls(data.calls ?? []);
    } catch {
      // network error — retry next round
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchData();
    const id = setInterval(() => {
      fetchData();
      setTick(tk => tk + 1);
    }, 5000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const id = setInterval(() => setTick(tk => tk + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const now = Date.now() / 1000;
  const firingSoonLabel = t("tools.firing_soon");
  const unknownLabel = t("tools.unknown_event");

  return (
    <div style={{ color: "#ddd", fontFamily: "monospace" }}>
      <h2 style={{ color: "#fff", fontWeight: 700, fontSize: 22, marginBottom: 24 }}>
        {t("tools.title")}
      </h2>

      {/* Active timers */}
      <section style={{ marginBottom: 32 }}>
        <h3 style={{ color: "#aaa", fontSize: 13, textTransform: "uppercase", letterSpacing: 1, marginBottom: 12 }}>
          {t("tools.timers_title")}
        </h3>
        {timers.length === 0 ? (
          <div style={{ color: "#555", fontSize: 13 }}>{t("tools.timers_empty")}</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {timers.map(timer => {
              const firesAt = new Date(timer.fires_at).getTime() / 1000;
              const remaining = Math.max(0, Math.round(firesAt - now));
              const isRepeat = !!timer.repeat;
              const locale = i18n.language === "nb" ? "nb-NO" : i18n.language;
              return (
                <div key={timer.id} style={{
                  background: isRepeat ? "#1a2e1a" : "#1a1a2e",
                  border: `1px solid ${isRepeat ? "#2a4a2a" : "#2a2a4a"}`,
                  borderRadius: 8, padding: "12px 16px",
                  display: "flex", alignItems: "flex-start", gap: 12,
                }}>
                  <div style={{
                    background: isRepeat ? "#4a7a4a" : SOURCE_COLOR.timer,
                    color: "#000", borderRadius: 4, padding: "2px 8px",
                    fontSize: 11, fontWeight: 700, flexShrink: 0, marginTop: 2,
                  }}>
                    {formatRemaining(remaining, firingSoonLabel)}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 12, color: "#888", marginBottom: 4, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                      <span>[{timer.id}]</span>
                      <span>{t("tools.timer_fires")} {new Date(timer.fires_at).toLocaleString(locale, { dateStyle: "short", timeStyle: "short" })}</span>
                      {isRepeat && (
                        <span style={{
                          background: "#2a4a2a", color: "#7fc97f",
                          borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 700,
                        }}>
                          ↻ {t(`tools.repeat.${timer.repeat!}`, timer.repeat!)}
                        </span>
                      )}
                      {timer.notify && <span style={{ color: SOURCE_COLOR.timer }}>{t("tools.timer_notify")}</span>}
                    </div>
                    <div style={{ fontSize: 13, color: "#ccc", lineHeight: 1.4 }}>
                      «{timer.prompt}»
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Tool run log */}
      <section>
        <h3 style={{ color: "#aaa", fontSize: 13, textTransform: "uppercase", letterSpacing: 1, marginBottom: 12 }}>
          {t("tools.log_title")}
        </h3>
        {loading ? (
          <div style={{ color: "#555", fontSize: 13 }}>{t("tools.log_loading")}</div>
        ) : calls.length === 0 ? (
          <div style={{ color: "#555", fontSize: 13 }}>{t("tools.log_empty")}</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {calls.map((c, i) => (
              <div key={i} style={{
                display: "grid",
                gridTemplateColumns: "60px 60px 140px 1fr auto",
                gap: 10, alignItems: "start",
                padding: "7px 10px", borderRadius: 6,
                background: i % 2 === 0 ? "#111" : "#141414",
                fontSize: 12,
              }}>
                <div style={{ color: "#555" }}>{formatTime(c.ts)}</div>
                <div style={{ color: sourceColor(c.source), fontWeight: 600 }}>{c.source}</div>
                <div style={{
                  color: eventColor(c),
                  background: "#1a1a1a",
                  borderRadius: 4, padding: "1px 6px",
                  border: `1px solid ${eventColor(c)}44`,
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                }}>
                  {eventLabel(c, unknownLabel)}
                </div>
                <div style={{ color: "#999", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.error
                    ? <span style={{ color: "#e07070" }}>{c.error}</span>
                    : c.result_preview ?? c.prompt_preview ?? ""}
                </div>
                <div style={{ color: "#444", textAlign: "right", whiteSpace: "nowrap" }}>
                  {c.duration_ms != null && c.duration_ms > 0 ? `${c.duration_ms}ms` : ""}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
