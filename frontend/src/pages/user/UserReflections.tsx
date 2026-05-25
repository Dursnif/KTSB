import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../auth/AuthContext";
import { apiReflectionDates, apiReflectionContent } from "../../services/api";
import { useTheme } from "../../theme";

const AGENT_LABELS: Record<string, string> = {
  "**[Kåre]**":        "KÅRE",
  "**[Miss Kåre]**":   "MISS KÅRE",
  "**[Pettersmart]**": "PETTERSMART",
  "**[Online]**":      "ONLINE",
  "**[Møteleder]**":   "MØTELEDER",
};

const AGENT_COLORS: Record<string, string> = {
  "KÅRE":        "#646cff",
  "MISS KÅRE":   "#f06292",
  "PETTERSMART": "#ff9800",
  "ONLINE":      "#4caf50",
  "MØTELEDER":   "#00bcd4",
};

const PIN_SESSION_KEY = "kaare_ref_pin";

function PinModal({
  onConfirm,
  onCancel,
  error,
}: {
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
          {t("user_reflections.pin_title")}
        </div>
        <div style={{ color: "#555", fontSize: 12, marginBottom: 20 }}>
          {t("user_reflections.pin_hint")}
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
            {t("user_reflections.pin_error")}
          </div>
        )}
        <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
          <button onClick={onCancel} style={{
            flex: 1, padding: "9px 0", borderRadius: 8,
            border: "1px solid #333", background: "transparent",
            color: "#666", fontSize: 13, cursor: "pointer",
          }}>
            {t("user_reflections.pin_cancel")}
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
            {t("user_reflections.pin_open")}
          </button>
        </div>
      </div>
    </div>
  );
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
    if (line.startsWith("- ")) {
      return <div key={i} style={{ color: "#888", fontSize: 13, padding: "2px 0 2px 12px" }}>· {line.slice(2)}</div>;
    }
    if (line.trim() === "") return <div key={i} style={{ height: 6 }} />;
    return <div key={i} style={{ color: "#ccc", fontSize: 14, lineHeight: 1.6 }}>{line}</div>;
  });
}

export default function UserReflections() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const theme = useTheme();

  const [dates, setDates] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [pinTarget, setPinTarget] = useState<string | null>(null);
  const [pinError, setPinError] = useState(false);
  const [cachedPin, setCachedPin] = useState<string | null>(() =>
    sessionStorage.getItem(PIN_SESSION_KEY)
  );

  useEffect(() => {
    if (!user) return;
    apiReflectionDates(user.username).then(setDates).catch(() => {});
  }, [user]);

  const loadDate = async (date: string, pin: string) => {
    if (!user) return;
    setLoading(true);
    setPinError(false);
    try {
      const result = await apiReflectionContent(user.username, date, pin);
      setContent(result.content);
      setSelected(date);
      setPinTarget(null);
      setCachedPin(pin);
      sessionStorage.setItem(PIN_SESSION_KEY, pin);
    } catch {
      setPinError(true);
    } finally {
      setLoading(false);
    }
  };

  const handleDateClick = (date: string) => {
    if (cachedPin) {
      loadDate(date, cachedPin);
    } else {
      setPinTarget(date);
      setPinError(false);
    }
  };

  const accent = theme.primary;

  return (
    <>
      {pinTarget && (
        <PinModal
          onConfirm={pin => loadDate(pinTarget, pin)}
          onCancel={() => { setPinTarget(null); setPinError(false); }}
          error={pinError}
        />
      )}

      <div style={{
        display: "flex", flexDirection: "column", height: "100%",
        padding: "28px 28px 20px", overflow: "hidden",
      }}>
        <div style={{ marginBottom: 24, flexShrink: 0 }}>
          <h1 style={{ color: "#fff", fontSize: 20, fontWeight: 700, margin: 0 }}>
            {t("user_reflections.title")}
          </h1>
          <p style={{ color: "#555", fontSize: 13, margin: "6px 0 0" }}>
            {t("user_reflections.subtitle")}
          </p>
        </div>

        <div style={{ display: "flex", gap: 20, flex: 1, minHeight: 0 }}>
          {/* Date list */}
          <div style={{
            width: 150, flexShrink: 0, overflowY: "auto",
            paddingRight: 8,
          }}>
            <div style={{ color: "#555", fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>
              {t("user_reflections.meetings_label")}
            </div>

            {dates.length === 0 && (
              <div style={{ color: "#444", fontSize: 13, lineHeight: 1.5 }}>
                {t("user_reflections.no_reflections")}
              </div>
            )}

            {dates.map(d => (
              <button
                key={d}
                onClick={() => handleDateClick(d)}
                style={{
                  display: "block", width: "100%", padding: "8px 10px", marginBottom: 4,
                  borderRadius: 8, border: "none", textAlign: "left", cursor: "pointer",
                  fontSize: 13,
                  background: selected === d ? accent + "22" : "transparent",
                  color: selected === d ? "#fff" : "#666",
                  outline: selected === d ? `1px solid ${accent}44` : "none",
                  transition: "background 0.15s",
                }}
              >
                {d}
                <span style={{ float: "right", opacity: 0.35, fontSize: 11 }}>🔒</span>
              </button>
            ))}

            {cachedPin && dates.length > 0 && (
              <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ color: "#3a3", fontSize: 11 }}>✓</span>
                <span style={{ color: "#444", fontSize: 11 }}>{t("user_reflections.pin_cached")}</span>
                <button
                  onClick={() => { setCachedPin(null); sessionStorage.removeItem(PIN_SESSION_KEY); }}
                  style={{ background: "none", border: "none", color: "#555", fontSize: 11, cursor: "pointer", padding: 0 }}
                >×</button>
              </div>
            )}
          </div>

          {/* Content */}
          <div style={{
            flex: 1, overflowY: "auto", background: "#111",
            borderRadius: 12, padding: "24px 24px 28px", minWidth: 0,
          }}>
            {loading && <div style={{ color: "#555", fontSize: 14 }}>{t("user_reflections.loading")}</div>}

            {!loading && !content && (
              <div style={{ color: "#444", fontSize: 14, lineHeight: 1.7 }}>
                {dates.length === 0
                  ? t("user_reflections.no_reflections")
                  : t("user_reflections.select_reflection")}
              </div>
            )}

            {!loading && content && (
              <div>{renderContent(content)}</div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
