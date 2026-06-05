import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useUserPrefs } from "../../hooks/useUserPrefs";
import { useTheme } from "../../theme";

const ACCENT_PRESETS = [
  { value: "#646cff", label: "Blå-lilla" },
  { value: "#8b5cf6", label: "Lilla" },
  { value: "#4f9cf9", label: "Blå" },
  { value: "#c084fc", label: "Lys lilla" },
  { value: "#4caf50", label: "Grønn" },
  { value: "#ff9800", label: "Oransje" },
  { value: "#f06292", label: "Rose" },
  { value: "#00bcd4", label: "Teal" },
  { value: "#ff6b9d", label: "Rosa" },
];

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      color: "#888", fontSize: 11, fontWeight: 600,
      textTransform: "uppercase", letterSpacing: 1,
      marginBottom: 14, paddingBottom: 8,
      borderBottom: "1px solid #1e1e1e",
    }}>
      {children}
    </div>
  );
}

function SettingRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "flex-start",
      gap: 16, marginBottom: 20,
    }}>
      <div style={{ flex: 1 }}>
        <div style={{ color: "#ccc", fontSize: 14, fontWeight: 500 }}>{label}</div>
        {hint && <div style={{ color: "#555", fontSize: 12, marginTop: 2, lineHeight: 1.4 }}>{hint}</div>}
      </div>
      <div style={{ flexShrink: 0 }}>
        {children}
      </div>
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      style={{
        width: 44, height: 24, borderRadius: 12, border: "none",
        background: checked ? "#646cff" : "#333",
        cursor: "pointer", position: "relative", transition: "background 0.2s",
        flexShrink: 0,
      }}
    >
      <span style={{
        position: "absolute", top: 3,
        left: checked ? 23 : 3,
        width: 18, height: 18, borderRadius: "50%",
        background: "#fff", transition: "left 0.2s",
        display: "block",
      }} />
    </button>
  );
}

function FontSizePicker({
  value,
  onChange,
}: {
  value: "small" | "normal" | "large";
  onChange: (v: "small" | "normal" | "large") => void;
}) {
  const { t } = useTranslation();
  const opts: { v: "small" | "normal" | "large"; label: string; px: string }[] = [
    { v: "small", label: t("user_settings.font_small"), px: "A" },
    { v: "normal", label: t("user_settings.font_normal"), px: "A" },
    { v: "large", label: t("user_settings.font_large"), px: "A" },
  ];
  const SIZES = { small: 12, normal: 16, large: 21 };

  return (
    <div style={{ display: "flex", gap: 6 }}>
      {opts.map(o => (
        <button
          key={o.v}
          onClick={() => onChange(o.v)}
          title={o.label}
          style={{
            width: 44, height: 36, borderRadius: 8, border: "none",
            background: value === o.v ? "#646cff22" : "#1e1e1e",
            outline: value === o.v ? "1px solid #646cff88" : "1px solid #2a2a2a",
            color: value === o.v ? "#a78bfa" : "#666",
            fontSize: SIZES[o.v], fontWeight: 700,
            cursor: "pointer", transition: "all 0.15s",
          }}
        >
          {o.px}
        </button>
      ))}
    </div>
  );
}

function AnimationPicker({
  value,
  onChange,
}: {
  value: "standard" | "minimal";
  onChange: (v: "standard" | "minimal") => void;
}) {
  const { t } = useTranslation();
  return (
    <div style={{ display: "flex", gap: 6 }}>
      {(["standard", "minimal"] as const).map(v => (
        <button
          key={v}
          onClick={() => onChange(v)}
          style={{
            padding: "6px 14px", borderRadius: 8, border: "none",
            background: value === v ? "#646cff22" : "#1e1e1e",
            outline: value === v ? "1px solid #646cff88" : "1px solid #2a2a2a",
            color: value === v ? "#a78bfa" : "#666",
            fontSize: 12, fontWeight: 500, cursor: "pointer",
            transition: "all 0.15s",
          }}
        >
          {v === "standard" ? t("user_settings.animations_standard") : t("user_settings.animations_minimal")}
        </button>
      ))}
    </div>
  );
}

export default function UserSettings() {
  const { t } = useTranslation();
  const { prefs, updatePrefs, resetPrefs } = useUserPrefs();
  const theme = useTheme();
  const navigate = useNavigate();

  return (
    <div style={{
      height: "100%", overflowY: "auto",
      padding: "28px 28px 40px",
    }}>
      <div style={{ maxWidth: 540 }}>
        <h1 style={{ color: "#fff", fontSize: 20, fontWeight: 700, margin: "0 0 6px" }}>
          {t("user_settings.title")}
        </h1>
        <p style={{ color: "#555", fontSize: 13, margin: "0 0 32px" }}>
          {t("user_settings.subtitle")}
        </p>

        {/* ── Utseende ── */}
        <div style={{ marginBottom: 32 }}>
          <SectionTitle>{t("user_settings.section_appearance")}</SectionTitle>

          <SettingRow
            label={t("user_settings.accent_label")}
            hint={t("user_settings.accent_hint")}
          >
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
              {/* Standard / reset */}
              <button
                onClick={() => updatePrefs({ accentColor: null })}
                title={t("user_settings.accent_default")}
                style={{
                  width: 28, height: 28, borderRadius: 8, border: "none",
                  background: "#2a2a2a",
                  outline: prefs.accentColor === null ? "2px solid #fff" : "1px solid #333",
                  cursor: "pointer", fontSize: 13,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: "#888",
                }}
              >
                ×
              </button>
              {ACCENT_PRESETS.map(p => (
                <button
                  key={p.value}
                  onClick={() => updatePrefs({ accentColor: p.value })}
                  title={p.label}
                  style={{
                    width: 28, height: 28, borderRadius: "50%", border: "none",
                    background: p.value,
                    outline: prefs.accentColor === p.value ? `3px solid ${p.value}` : "2px solid transparent",
                    outlineOffset: 2,
                    cursor: "pointer",
                    boxShadow: prefs.accentColor === p.value ? `0 0 8px ${p.value}88` : "none",
                    transition: "all 0.15s",
                  }}
                />
              ))}
            </div>
          </SettingRow>

          <SettingRow
            label={t("user_settings.font_size_label")}
            hint={t("user_settings.font_size_hint")}
          >
            <FontSizePicker value={prefs.fontSize} onChange={v => updatePrefs({ fontSize: v })} />
          </SettingRow>

          <SettingRow
            label={t("user_settings.animations_label")}
            hint={t("user_settings.animations_hint")}
          >
            <AnimationPicker value={prefs.animations} onChange={v => updatePrefs({ animations: v })} />
          </SettingRow>
        </div>

        {/* ── Chat-atferd ── */}
        <div style={{ marginBottom: 32 }}>
          <SectionTitle>{t("user_settings.section_chat")}</SectionTitle>

          <SettingRow
            label={t("user_settings.tts_autoplay_label")}
            hint={t("user_settings.tts_autoplay_hint")}
          >
            <Toggle checked={prefs.ttsAutoplay} onChange={v => updatePrefs({ ttsAutoplay: v })} />
          </SettingRow>

          <SettingRow
            label={t("user_settings.show_trace_label")}
            hint={t("user_settings.show_trace_hint")}
          >
            <Toggle checked={prefs.showTrace} onChange={v => updatePrefs({ showTrace: v })} />
          </SettingRow>
        </div>

        {/* ── Miss Kåre ── */}
        <div style={{ marginBottom: 32 }}>
          <SectionTitle>{t("user_settings.section_misskare")}</SectionTitle>

          <SettingRow
            label={t("user_settings.mk_panel_label")}
            hint={t("user_settings.mk_panel_hint")}
          >
            <Toggle checked={prefs.mkPanelEnabled} onChange={v => updatePrefs({ mkPanelEnabled: v })} />
          </SettingRow>
        </div>

        {/* ── Preview ── */}
        <div style={{ marginBottom: 32 }}>
          <SectionTitle>{t("user_settings.section_preview")}</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{
              alignSelf: "flex-end",
              background: theme.bubbleUserBg,
              color: "#fff",
              padding: "10px 14px",
              borderRadius: "14px 14px 4px 14px",
              fontSize: theme.chatFontSize,
              maxWidth: "70%",
              boxShadow: theme.bubbleKareShadow !== "none" ? theme.bubbleKareShadow : undefined,
            }}>
              {t("user_settings.preview_user")}
            </div>
            <div style={{
              alignSelf: "flex-start",
              background: "#1e1e1e",
              border: theme.bubbleKareBorder !== "none" ? theme.bubbleKareBorder : "1px solid #2a2a2a",
              color: "#fff",
              padding: "10px 14px",
              borderRadius: "14px 14px 14px 4px",
              fontSize: theme.chatFontSize,
              maxWidth: "70%",
            }}>
              {t("user_settings.preview_kare")}
            </div>
          </div>
        </div>

        {/* ── Personvern ── */}
        <div style={{ marginBottom: 32 }}>
          <SectionTitle>{t("privacy.section_title")}</SectionTitle>
          <div style={{ color: "#666", fontSize: 13, lineHeight: 1.6, marginBottom: 16 }}>
            {t("privacy.encrypted_info")}
          </div>
          <div style={{ background: "#111", border: "1px solid #1e1e1e", borderRadius: 8, padding: "14px 16px" }}>
            <div style={{ color: "#aaa", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{t("privacy.recovery_title")}</div>
            <div style={{ color: "#555", fontSize: 12, lineHeight: 1.5, marginBottom: 12 }}>{t("privacy.recovery_hint")}</div>
            <button
              onClick={() => navigate("/recover")}
              style={{ padding: "7px 16px", borderRadius: 8, border: "1px solid #333", background: "transparent", color: "#888", fontSize: 13, cursor: "pointer" }}
            >
              {t("privacy.forgot_pin")}
            </button>
          </div>
        </div>

        {/* ── Tilbakestill ── */}
        <button
          onClick={resetPrefs}
          style={{
            padding: "9px 18px", borderRadius: 8,
            border: "1px solid #333", background: "transparent",
            color: "#555", fontSize: 13, cursor: "pointer",
          }}
        >
          {t("user_settings.reset")}
        </button>
      </div>
    </div>
  );
}
