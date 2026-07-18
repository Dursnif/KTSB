import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../auth/AuthContext";
import {
  apiGetUserSummary,
  apiReflectionDates,
  apiReflectionContent,
  apiGetReflectionComment,
  apiSetReflectionComment,
  type UserSummary,
} from "../../services/api";
import { useTheme } from "../../theme";

const AGENT_LABELS: Record<string, string> = {
  "**[Kåre]**":      "KÅRE",
  "**[Miss Kåre]**": "MISS KÅRE",
  "**[Mechanic]**":  "MECHANIC",
  "**[Online]**":    "ONLINE",
  "**[Møteleder]**": "MØTELEDER",
};

const AGENT_COLORS: Record<string, string> = {
  "KÅRE":        "#646cff",
  "MISS KÅRE":   "#f06292",
  "MECHANIC":    "#ff9800",
  "ONLINE":      "#4caf50",
  "MØTELEDER":   "#00bcd4",
};

function pinKey(username: string) {
  return `kaare_child_pin_${username}`;
}

function renderContent(md: string) {
  return md.split("\n").map((line, i) => {
    if (line.startsWith("# "))
      return <h1 key={i} style={{ color: "#fff", fontSize: 20, fontWeight: 700, margin: "0 0 16px" }}>{line.slice(2)}</h1>;
    if (line.startsWith("## "))
      return <h2 key={i} style={{ color: "#aaa", fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: 1, margin: "20px 0 8px" }}>{line.slice(3)}</h2>;
    for (const [tag, label] of Object.entries(AGENT_LABELS)) {
      if (line.startsWith(tag)) {
        const color = AGENT_COLORS[label] ?? "#888";
        return <div key={i} style={{ color, fontSize: 12, fontWeight: 700, margin: "16px 0 4px" }}>{label}</div>;
      }
    }
    if (line.startsWith("- "))
      return <div key={i} style={{ color: "#888", fontSize: 13, padding: "2px 0 2px 12px" }}>· {line.slice(2)}</div>;
    if (line.trim() === "") return <div key={i} style={{ height: 6 }} />;
    return <div key={i} style={{ color: "#ccc", fontSize: 14, lineHeight: 1.6 }}>{line}</div>;
  });
}

function PinModal({
  childName,
  onConfirm,
  onCancel,
  error,
}: {
  childName: string;
  onConfirm: (pin: string) => void;
  onCancel: () => void;
  error: boolean;
}) {
  const { t } = useTranslation();
  const [pin, setPin] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200,
    }}>
      <div style={{
        background: "#1a1a1a", border: "1px solid #333", borderRadius: 14,
        padding: "32px 36px", width: 300, textAlign: "center",
      }}>
        <div style={{ fontSize: 28, marginBottom: 8 }}>🔒</div>
        <div style={{ color: "#fff", fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
          {t("children_page.pin_title")}
        </div>
        <div style={{ color: "#555", fontSize: 12, marginBottom: 20 }}>
          {t("children_page.pin_hint", { name: childName })}
        </div>
        <input
          ref={inputRef}
          type="password"
          inputMode="numeric"
          maxLength={8}
          value={pin}
          onChange={e => setPin(e.target.value)}
          onKeyDown={e => e.key === "Enter" && pin.length >= 4 && onConfirm(pin)}
          placeholder="PIN"
          style={{
            width: "100%", padding: "10px 14px", borderRadius: 8,
            border: error ? "1px solid #f06292" : "1px solid #333",
            background: "#111", color: "#fff", fontSize: 20,
            textAlign: "center", letterSpacing: 6, boxSizing: "border-box", outline: "none",
          }}
        />
        {error && (
          <div style={{ color: "#f06292", fontSize: 12, marginTop: 6 }}>
            {t("children_page.pin_error")}
          </div>
        )}
        <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
          <button onClick={onCancel} style={{
            flex: 1, padding: "9px 0", borderRadius: 8,
            border: "1px solid #333", background: "transparent",
            color: "#666", fontSize: 13, cursor: "pointer",
          }}>
            {t("children_page.pin_cancel")}
          </button>
          <button
            onClick={() => pin.length >= 4 && onConfirm(pin)}
            disabled={pin.length < 4}
            style={{
              flex: 1, padding: "9px 0", borderRadius: 8,
              border: "1px solid #f0629255", background: "#f0629218",
              color: "#f06292", fontSize: 13, fontWeight: 600,
              cursor: pin.length < 4 ? "not-allowed" : "pointer",
              opacity: pin.length < 4 ? 0.5 : 1,
            }}
          >
            {t("children_page.pin_open")}
          </button>
        </div>
      </div>
    </div>
  );
}

function CommentBox({
  child, date, pin, accent,
}: { child: UserSummary; date: string; pin: string; accent: string }) {
  const { t } = useTranslation();
  const [comment, setComment] = useState("");
  const [saved, setSaved]     = useState(false);
  const [saving, setSaving]   = useState(false);
  const [err, setErr]         = useState(false);

  useEffect(() => {
    setSaved(false); setErr(false); setComment("");
    apiGetReflectionComment(child.username, date, pin)
      .then(r => setComment(r.comment))
      .catch(() => {});
  }, [child.username, date, pin]);

  const save = async () => {
    setSaving(true); setErr(false);
    try {
      await apiSetReflectionComment(child.username, date, pin, comment);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch {
      setErr(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ marginTop: 24, borderTop: "1px solid #222", paddingTop: 16 }}>
      <div style={{ color: "#555", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
        {t("children_page.comment_label")}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
        <textarea
          value={comment}
          onChange={e => { setComment(e.target.value); setSaved(false); }}
          onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) save(); }}
          placeholder={t("children_page.comment_placeholder")}
          rows={3}
          style={{
            flex: 1, background: "#0d0d0d", border: `1px solid ${accent}33`, borderRadius: 8,
            color: "#ccc", fontSize: 13, padding: "8px 10px", resize: "none",
            outline: "none", fontFamily: "inherit", lineHeight: 1.5,
          }}
        />
        <button
          onClick={save}
          disabled={saving}
          style={{
            padding: "8px 14px", borderRadius: 8, border: `1px solid ${accent}55`,
            background: saved ? `${accent}22` : "#1a1a1a",
            color: saved ? accent : err ? "#f06292" : "#666",
            fontSize: 13, fontWeight: 600, cursor: "pointer",
            whiteSpace: "nowrap", transition: "all 0.2s", minWidth: 64,
          }}
        >
          {saved ? "✓ OK" : err ? t("children_page.comment_error") : t("children_page.comment_save")}
        </button>
      </div>
    </div>
  );
}

export default function UserChildren() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const theme = useTheme();

  const [childSummaries, setChildSummaries] = useState<UserSummary[]>([]);
  const [loadingChildren, setLoadingChildren] = useState(true);
  const [selectedChild, setSelectedChild] = useState<UserSummary | null>(null);

  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loadingContent, setLoadingContent] = useState(false);

  const [pinTarget, setPinTarget] = useState<string | null>(null); // date or "init"
  const [pinError, setPinError]   = useState(false);
  const [pins, setPins]           = useState<Record<string, string>>(() => {
    // restore cached pins per child from sessionStorage
    const result: Record<string, string> = {};
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i)!;
      if (k.startsWith("kaare_child_pin_")) {
        const u = k.replace("kaare_child_pin_", "");
        result[u] = sessionStorage.getItem(k)!;
      }
    }
    return result;
  });

  // Load child summaries on mount
  useEffect(() => {
    if (!user?.managed_children) { setLoadingChildren(false); return; }
    let children: string[] = [];
    try { children = JSON.parse(user.managed_children); } catch { /* empty */ }
    if (!children.length) { setLoadingChildren(false); return; }

    Promise.allSettled(children.map(u => apiGetUserSummary(u)))
      .then(results => {
        setChildSummaries(
          results.flatMap(r => r.status === "fulfilled" ? [r.value] : [])
        );
      })
      .finally(() => setLoadingChildren(false));
  }, [user]);

  // Load reflection dates when child changes
  useEffect(() => {
    setDates([]); setSelectedDate(null); setContent("");
    if (!selectedChild) return;
    apiReflectionDates(selectedChild.username)
      .then(setDates)
      .catch(() => {});
  }, [selectedChild]);

  const cachedPin = selectedChild ? pins[selectedChild.username] : null;

  const loadReflection = async (date: string, pin: string) => {
    if (!selectedChild) return;
    setLoadingContent(true); setPinError(false);
    try {
      const r = await apiReflectionContent(selectedChild.username, date, pin);
      // Cache PIN on success
      sessionStorage.setItem(pinKey(selectedChild.username), pin);
      setPins(prev => ({ ...prev, [selectedChild.username]: pin }));
      setContent(r.content);
      setSelectedDate(date);
      setPinTarget(null);
    } catch (e: any) {
      if (e?.response?.status === 403) {
        setPinError(true);
      } else {
        setContent("");
      }
    } finally {
      setLoadingContent(false);
    }
  };

  const handleDateClick = (date: string) => {
    if (cachedPin) {
      loadReflection(date, cachedPin);
    } else {
      setPinError(false);
      setPinTarget(date);
    }
  };

  const handlePinConfirm = (pin: string) => {
    if (!pinTarget || !selectedChild) return;
    loadReflection(pinTarget, pin);
  };

  const handleChildSelect = (child: UserSummary) => {
    setSelectedChild(child);
    setSelectedDate(null);
    setContent("");
    setPinTarget(null);
  };

  const accent = theme.primary;
  const noChildren = !user?.managed_children || (() => {
    try { return JSON.parse(user.managed_children!).length === 0; } catch { return true; }
  })();

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden", background: "#0d0d0d" }}>

      {/* ── Child list ── */}
      <div style={{
        width: 200, flexShrink: 0, borderRight: "1px solid #1e1e1e",
        display: "flex", flexDirection: "column", overflow: "hidden",
      }}>
        <div style={{
          padding: "20px 16px 12px",
          borderBottom: "1px solid #1e1e1e",
        }}>
          <div style={{ color: "#fff", fontSize: 15, fontWeight: 700 }}>{t("children_page.title")}</div>
          <div style={{ color: "#444", fontSize: 11, marginTop: 2 }}>{t("children_page.subtitle")}</div>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "8px 8px" }}>
          {loadingChildren ? (
            <div style={{ color: "#555", fontSize: 12, padding: "12px 8px" }}>{t("children_page.loading")}</div>
          ) : noChildren ? (
            <div style={{ color: "#444", fontSize: 12, padding: "12px 8px", lineHeight: 1.5 }}>
              {t("children_page.no_children")}
            </div>
          ) : childSummaries.map(child => (
            <button
              key={child.username}
              onClick={() => handleChildSelect(child)}
              style={{
                display: "flex", alignItems: "center", gap: 8,
                width: "100%", padding: "9px 8px", borderRadius: 8, border: "none",
                background: selectedChild?.username === child.username ? `${accent}18` : "transparent",
                borderLeft: `3px solid ${selectedChild?.username === child.username ? accent : "transparent"}`,
                color: selectedChild?.username === child.username ? accent : "#aaa",
                cursor: "pointer", textAlign: "left",
                transition: "background 0.15s, color 0.15s",
              }}
            >
              <span style={{ fontSize: 20, flexShrink: 0 }}>{child.avatar}</span>
              <div style={{ overflow: "hidden" }}>
                <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {child.display_name}
                </div>
                <div style={{ fontSize: 10, color: "#555", textTransform: "uppercase" }}>{child.role}</div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* ── Date list ── */}
      <div style={{
        width: 170, flexShrink: 0, borderRight: "1px solid #1e1e1e",
        display: "flex", flexDirection: "column", overflow: "hidden",
      }}>
        <div style={{
          padding: "20px 14px 12px",
          borderBottom: "1px solid #1e1e1e",
          color: "#555", fontSize: 11, textTransform: "uppercase", letterSpacing: 1,
        }}>
          {selectedChild ? t("children_page.reflections_label") : ""}
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "6px 6px" }}>
          {selectedChild && dates.length === 0 && (
            <div style={{ color: "#444", fontSize: 12, padding: "12px 8px" }}>
              {t("children_page.no_reflections")}
            </div>
          )}
          {dates.map(date => (
            <button
              key={date}
              onClick={() => handleDateClick(date)}
              style={{
                display: "block", width: "100%", padding: "8px 10px",
                borderRadius: 7, border: "none",
                background: selectedDate === date ? `${accent}18` : "transparent",
                borderLeft: `3px solid ${selectedDate === date ? accent : "transparent"}`,
                color: selectedDate === date ? accent : "#888",
                fontSize: 12, cursor: "pointer", textAlign: "left",
                transition: "background 0.15s, color 0.15s",
                fontFamily: "monospace",
              }}
            >
              {date}
            </button>
          ))}
        </div>
      </div>

      {/* ── Content ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {!selectedChild || !selectedDate ? (
          <div style={{
            flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
            color: "#333", fontSize: 13,
          }}>
            {selectedChild
              ? t("children_page.select_reflection")
              : ""}
          </div>
        ) : loadingContent ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555", fontSize: 13 }}>
            {t("children_page.loading")}
          </div>
        ) : (
          <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px" }}>
            <div style={{ maxWidth: 720 }}>
              {renderContent(content)}
              {selectedDate && cachedPin && (
                <CommentBox
                  child={selectedChild}
                  date={selectedDate}
                  pin={cachedPin}
                  accent={accent}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── PIN modal ── */}
      {pinTarget && selectedChild && (
        <PinModal
          childName={selectedChild.display_name}
          onConfirm={handlePinConfirm}
          onCancel={() => { setPinTarget(null); setPinError(false); }}
          error={pinError}
        />
      )}
    </div>
  );
}
