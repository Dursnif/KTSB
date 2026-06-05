import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { apiRecover } from "../services/api";
import { useAuth } from "../auth/AuthContext";

export default function Recovery() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { login } = useAuth();
  const [f, setF] = useState({ username: "", seed_phrase: "", new_pin: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await apiRecover(f.username.trim(), f.seed_phrase.trim().toLowerCase(), f.new_pin);
      login(result.user as any, result.token);
      navigate("/");
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? "";
      if (detail.includes("Invalid")) {
        setError(t("recover.error_invalid"));
      } else {
        setError(t("recover.error_generic"));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
      background: "#0a0a0a", padding: 24,
    }}>
      <div style={{ width: "100%", maxWidth: 400 }}>
        <h1 style={{ color: "#fff", fontSize: 22, fontWeight: 700, margin: "0 0 6px" }}>{t("recover.title")}</h1>
        <p style={{ color: "#555", fontSize: 13, margin: "0 0 28px" }}>{t("recover.subtitle")}</p>

        <form onSubmit={submit}>
          <label style={S.label}>{t("recover.username_label")}</label>
          <input
            style={S.input}
            value={f.username}
            onChange={e => setF({ ...f, username: e.target.value })}
            autoComplete="username"
            required
          />

          <label style={S.label}>{t("recover.seed_label")}</label>
          <textarea
            style={{ ...S.input, height: 80, resize: "vertical", fontFamily: "monospace", fontSize: 13 }}
            placeholder={t("recover.seed_placeholder")}
            value={f.seed_phrase}
            onChange={e => setF({ ...f, seed_phrase: e.target.value })}
            required
          />

          <label style={S.label}>{t("recover.new_pin_label")}</label>
          <input
            style={S.input}
            type="password"
            inputMode="numeric"
            value={f.new_pin}
            onChange={e => setF({ ...f, new_pin: e.target.value })}
            required
          />

          {error && <div style={S.err}>{error}</div>}

          <button type="submit" disabled={loading} style={S.btn}>
            {loading ? "…" : t("recover.submit")}
          </button>
        </form>

        <button onClick={() => navigate("/login")} style={S.back}>
          {t("recover.back_to_login")}
        </button>
      </div>
    </div>
  );
}

const S = {
  label: { display: "block" as const, color: "#888", fontSize: 12, fontWeight: 600, marginBottom: 6, textTransform: "uppercase" as const, letterSpacing: 0.5 },
  input: { display: "block" as const, width: "100%", background: "#111", border: "1px solid #222", borderRadius: 8, color: "#fff", fontSize: 15, padding: "10px 12px", marginBottom: 16, boxSizing: "border-box" as const, outline: "none" },
  err: { color: "#f87171", fontSize: 13, marginBottom: 14 },
  btn: { width: "100%", padding: "11px", borderRadius: 8, border: "none", background: "#646cff", color: "#fff", fontSize: 15, fontWeight: 600, cursor: "pointer", marginBottom: 14 },
  back: { display: "block" as const, width: "100%", padding: "9px", borderRadius: 8, border: "1px solid #222", background: "transparent", color: "#555", fontSize: 13, cursor: "pointer", textAlign: "center" as const },
};
