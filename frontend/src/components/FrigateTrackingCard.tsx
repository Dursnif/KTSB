// P144-s02: «Frigate may track me» — one card for the user's own settings, a guardian's child view
// and the admin Users page. Whoever may decide gets a switch; everyone else sees who decides.
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiGetFrigateTracking, apiPutFrigateExpressions, apiPutFrigateTracking, type FrigateTracking } from "../services/api";
import { apiErrorDetail } from "@/lib/apiError";

export default function FrigateTrackingCard({ username, compact = false }: { username: string; compact?: boolean }) {
  const { t } = useTranslation();
  const [state, setState] = useState<FrigateTracking | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<{ user: string; msg: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiGetFrigateTracking(username)
      .then(d => { if (!cancelled) setState(d); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [username]);

  // state/error from a previous username (guardian switching child) are not shown
  if (!state || state.username !== username || state.decided_by === "none") return null;

  const toggle = async () => {
    if (!state.can_edit || saving) return;
    setSaving(true);
    setError(null);
    try {
      setState(await apiPutFrigateTracking(username, !state.tracking));
    } catch (err) {
      setError({ user: username, msg: apiErrorDetail(err) ?? t("users.frigate_save_error") });
    } finally {
      setSaving(false);
    }
  };

  // P144-s27b: «Uttrykk og gester» — the same person decides, on by default; hidden on an old API
  const toggleLooks = async () => {
    if (!state.can_edit || saving) return;
    setSaving(true);
    setError(null);
    try {
      setState(await apiPutFrigateExpressions(username, !(state.expressions ?? true)));
    } catch (err) {
      setError({ user: username, msg: apiErrorDetail(err) ?? t("users.frigate_save_error") });
    } finally {
      setSaving(false);
    }
  };

  const on = state.tracking;
  const looks = state.expressions ?? true;
  return (
    <div style={{
      background: "#111", border: "1px solid #1e1e1e", borderRadius: 8,
      padding: compact ? "10px 12px" : "14px 16px", marginBottom: compact ? 14 : 0,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ color: "#ccc", fontSize: 14, fontWeight: 500 }}>{t("users.frigate_title")}</div>
        {state.can_edit ? (
          <button
            type="button"
            role="switch"
            aria-checked={on}
            aria-label={t("users.frigate_title")}
            onClick={toggle}
            disabled={saving}
            style={{
              width: 44, height: 24, borderRadius: 12, border: "none", flexShrink: 0,
              background: on ? "#16a34a" : "#333", position: "relative",
              cursor: saving ? "wait" : "pointer", transition: "background 0.2s",
            }}
          >
            <span style={{
              position: "absolute", top: 3, left: on ? 23 : 3, width: 18, height: 18,
              borderRadius: "50%", background: "#fff", transition: "left 0.2s", display: "block",
            }} />
          </button>
        ) : (
          <span style={{ color: on ? "#16a34a" : "#888", fontSize: 13, flexShrink: 0 }}>
            {on ? t("users.frigate_state_on") : t("users.frigate_state_off")}
          </span>
        )}
      </div>
      <div style={{ color: "#666", fontSize: 12, lineHeight: 1.5, marginTop: 6 }}>
        {on ? t("users.frigate_desc_on") : t("users.frigate_desc_off")}
      </div>
      <div style={{ color: "#555", fontSize: 12, lineHeight: 1.5, marginTop: 6 }}>{t("users.frigate_scope")}</div>
      <div style={{ color: "#555", fontSize: 12, lineHeight: 1.5, marginTop: 6 }}>
        {state.faces.length > 0
          ? t("users.frigate_linked", { faces: state.faces.join(", ") })
          : t("users.frigate_no_faces")}
      </div>
      {state.expressions !== undefined && (
        <div style={{ borderTop: "1px solid #1e1e1e", marginTop: 10, paddingTop: 10 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <div style={{ color: "#ccc", fontSize: 14, fontWeight: 500 }}>{t("users.frigate_looks_title")}</div>
            {state.can_edit ? (
              <button
                type="button" role="switch" aria-checked={looks} aria-label={t("users.frigate_looks_title")}
                onClick={toggleLooks} disabled={saving}
                style={{
                  width: 44, height: 24, borderRadius: 12, border: "none", flexShrink: 0,
                  background: looks ? "#16a34a" : "#333", position: "relative",
                  cursor: saving ? "wait" : "pointer", transition: "background 0.2s",
                }}
              >
                <span style={{
                  position: "absolute", top: 3, left: looks ? 23 : 3, width: 18, height: 18,
                  borderRadius: "50%", background: "#fff", transition: "left 0.2s", display: "block",
                }} />
              </button>
            ) : (
              <span style={{ color: looks ? "#16a34a" : "#888", fontSize: 13, flexShrink: 0 }}>
                {looks ? t("users.frigate_state_on") : t("users.frigate_state_off")}
              </span>
            )}
          </div>
          <div style={{ color: "#666", fontSize: 12, lineHeight: 1.5, marginTop: 6 }}>
            {looks ? t("users.frigate_looks_desc_on") : t("users.frigate_looks_desc_off")}
          </div>
        </div>
      )}
      {!state.can_edit && (
        <div style={{ color: "#888", fontSize: 12, marginTop: 6 }}>{t(`users.frigate_decided_${state.decided_by}`)}</div>
      )}
      {error?.user === username && <div style={{ color: "#e57373", fontSize: 12, marginTop: 6 }}>{error.msg}</div>}
    </div>
  );
}
