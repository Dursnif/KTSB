import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { apiLogin, apiUpdatePin } from "../services/api";
import { useAuth } from "../auth/AuthContext";
import { loginTheme } from "../theme";

const S = {
  page: {
    minHeight: "100vh", display: "flex", alignItems: "center",
    justifyContent: "center", background: "#111",
  } as React.CSSProperties,
  card: {
    background: "#1a1a1a", borderRadius: 16, padding: "40px 32px",
    width: "90%", maxWidth: 340,
    boxShadow: `0 8px 32px rgba(0,0,0,0.5), 0 0 40px ${loginTheme.primary}18`,
    border: `1px solid ${loginTheme.primary}22`,
  } as React.CSSProperties,
  title: { fontSize: 28, fontWeight: 700, marginBottom: 8, textAlign: "center", ...loginTheme.titleCss } as React.CSSProperties,
  subtitle: { color: "#888", fontSize: 14, textAlign: "center", marginBottom: 32 } as React.CSSProperties,
  label: { color: "#aaa", fontSize: 13, marginBottom: 6, display: "block" } as React.CSSProperties,
  input: {
    width: "100%", padding: "10px 14px", borderRadius: 8, border: "1px solid #333",
    background: "#111", color: "#fff", fontSize: 15, boxSizing: "border-box",
    outline: "none", marginBottom: 16,
  } as React.CSSProperties,
  btn: {
    width: "100%", padding: "12px", borderRadius: 8, border: "none",
    background: loginTheme.btnBg, color: "#fff", fontSize: 16, fontWeight: 600,
    cursor: "pointer", marginTop: 8,
  } as React.CSSProperties,
  error: { color: "#ff6b6b", fontSize: 13, textAlign: "center", marginTop: 12 } as React.CSSProperties,
  overlay: {
    position: "fixed" as const, inset: 0, background: "rgba(0,0,0,0.85)",
    display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200,
  },
  modal: {
    background: "#1a1a1a", borderRadius: 16, padding: "40px 32px",
    width: "90%", maxWidth: 340, boxShadow: "0 8px 32px rgba(0,0,0,0.7)",
  } as React.CSSProperties,
};

export default function Login() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const isReauth = searchParams.get("reauth") === "1";
  const [username, setUsername] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const [mustChange, setMustChange] = useState(false);
  const [pendingToken, setPendingToken] = useState("");
  const [pendingUser, setPendingUser] = useState<any>(null);
  const [newPin, setNewPin] = useState("");
  const [newPin2, setNewPin2] = useState("");
  const [pinError, setPinError] = useState("");
  const [pinLoading, setPinLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await apiLogin(username.trim(), pin);
      if (result.must_change_pin) {
        setPendingToken(result.token);
        setPendingUser(result.user);
        setMustChange(true);
      } else {
        login(result.user, result.token);
        navigate(result.user.role === "admin" ? "/admin" : "/");
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? t("login.error_login"));
    } finally {
      setLoading(false);
    }
  };

  const handleChangePin = async (e: React.FormEvent) => {
    e.preventDefault();
    setPinError("");
    if (newPin.length < 6) { setPinError(t("login.error_pin_short")); return; }
    if (newPin !== newPin2) { setPinError(t("login.error_pin_mismatch")); return; }
    setPinLoading(true);
    try {
      sessionStorage.setItem("kaare_token", pendingToken);
      await apiUpdatePin(pendingUser.username, newPin);
      const updated = { ...pendingUser, must_change_pin: false };
      login(updated, pendingToken);
      navigate(updated.role === "admin" ? "/admin" : "/");
    } catch (err: any) {
      sessionStorage.removeItem("kaare_token");
      setPinError(err?.response?.data?.detail ?? t("login.error_generic"));
    } finally {
      setPinLoading(false);
    }
  };

  return (
    <div style={S.page}>
      <div style={S.card}>
        <div style={S.title}>{t("login.title")}</div>
        <div style={S.subtitle}>{isReauth ? t("login.reauth_subtitle") : t("login.subtitle")}</div>
        <form onSubmit={handleSubmit}>
          <label style={S.label}>{t("login.username")}</label>
          <input
            style={S.input}
            value={username}
            onChange={e => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
          />
          <label style={S.label}>{t("login.pin")}</label>
          <input
            style={S.input}
            type="password"
            inputMode="numeric"
            value={pin}
            onChange={e => setPin(e.target.value)}
            autoComplete="current-password"
          />
          <button style={S.btn} type="submit" disabled={loading}>
            {loading ? t("login.logging_in") : t("login.login")}
          </button>
        </form>
        {error && <div style={S.error}>{error}</div>}
        <div style={{ textAlign: "center", marginTop: 14 }}>
          <button onClick={() => navigate("/recover")}
            style={{ background: "none", border: "none", color: "#555", fontSize: 13, cursor: "pointer", textDecoration: "underline" }}>
            {t("privacy.forgot_pin")}
          </button>
        </div>
      </div>

      {mustChange && (
        <div style={S.overlay}>
          <div style={S.modal}>
            <div style={{ color: "#fff", fontSize: 20, fontWeight: 700, marginBottom: 8 }}>
              {t("login.choose_pin")}
            </div>
            <div style={{ color: "#aaa", fontSize: 13, marginBottom: 24 }}>
              {t("login.choose_pin_sub", { name: pendingUser?.display_name })}
            </div>
            <form onSubmit={handleChangePin}>
              <label style={S.label}>{t("login.new_pin")}</label>
              <input
                style={S.input}
                type="password"
                inputMode="numeric"
                value={newPin}
                onChange={e => setNewPin(e.target.value)}
                autoFocus
                required
              />
              <label style={S.label}>{t("login.repeat_pin")}</label>
              <input
                style={S.input}
                type="password"
                inputMode="numeric"
                value={newPin2}
                onChange={e => setNewPin2(e.target.value)}
                required
              />
              {pinError && <div style={S.error}>{pinError}</div>}
              <button style={S.btn} type="submit" disabled={pinLoading}>
                {pinLoading ? t("login.saving") : t("login.set_pin")}
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
