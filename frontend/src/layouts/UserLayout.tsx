import { useState, useEffect } from "react";
import { usePupilAnimation } from "../hooks/usePupilAnimation";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  MessageSquare, BookOpen, Settings, LogOut,
  ChevronLeft, ChevronRight, ShieldCheck, Users,
} from "lucide-react";
import { KTSBLogo } from "../components/KTSBLogo";
import "../components/ktsb-logo.css";

const HEADER_H = 140; // px — fixed logo header height
import { useAuth } from "../auth/AuthContext";
import { useAssistantName } from "../contexts/AssistantNameContext";
import { apiUpdatePin, apiPing } from "../services/api";
import { useTheme } from "../theme";
import type { Role } from "../services/api";

const SIDEBAR_KEY = "user-sidebar-collapsed";

const REFLECTION_ROLES: Role[] = ["teen", "young_adult", "adult", "admin"];

function ChangePinModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [p1, setP1] = useState("");
  const [p2, setP2] = useState("");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState(false);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    if (p1.length < 4) { setErr(t("user.error_pin_short")); return; }
    if (p1 !== p2) { setErr(t("user.error_pin_mismatch")); return; }
    setLoading(true);
    try {
      await apiUpdatePin(user!.username, p1);
      setOk(true);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? t("user.error"));
    } finally {
      setLoading(false);
    }
  };

  const inp: React.CSSProperties = {
    width: "100%", padding: "10px 14px", borderRadius: 8, border: "1px solid #333",
    background: "#111", color: "#fff", fontSize: 15, boxSizing: "border-box",
    outline: "none", marginBottom: 14,
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200,
    }} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={{ background: "#1a1a1a", borderRadius: 14, padding: "32px 28px", width: "90%", maxWidth: 320 }}>
        <div style={{ color: "#fff", fontSize: 18, fontWeight: 700, marginBottom: 20 }}>{t("user.change_pin")}</div>
        {ok ? (
          <>
            <div style={{ color: "#4caf50", marginBottom: 20 }}>{t("user.pin_changed")}</div>
            <button onClick={onClose} style={{ padding: "10px 20px", borderRadius: 8, border: "none", background: "#646cff", color: "#fff", cursor: "pointer" }}>{t("user.close")}</button>
          </>
        ) : (
          <form onSubmit={submit}>
            <label style={{ color: "#aaa", fontSize: 13, display: "block", marginBottom: 6 }}>{t("user.new_pin")}</label>
            <input style={inp} type="password" inputMode="numeric" value={p1}
              onChange={e => setP1(e.target.value)} autoFocus required />
            <label style={{ color: "#aaa", fontSize: 13, display: "block", marginBottom: 6 }}>{t("user.repeat_pin")}</label>
            <input style={inp} type="password" inputMode="numeric" value={p2}
              onChange={e => setP2(e.target.value)} required />
            {err && <div style={{ color: "#ff6b6b", fontSize: 13, marginBottom: 12 }}>{err}</div>}
            <div style={{ display: "flex", gap: 10 }}>
              <button type="submit" disabled={loading} style={{ flex: 1, padding: "10px", borderRadius: 8, border: "none", background: "#646cff", color: "#fff", fontWeight: 600, cursor: "pointer" }}>
                {loading ? t("user.saving") : t("user.save")}
              </button>
              <button type="button" onClick={onClose} style={{ flex: 1, padding: "10px", borderRadius: 8, border: "1px solid #333", background: "transparent", color: "#aaa", cursor: "pointer" }}>
                {t("user.cancel")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

export default function UserLayout() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const theme = useTheme();
  const assistantName = useAssistantName();
  usePupilAnimation({ loginFocus: true });

  const [showPin, setShowPin] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() =>
    localStorage.getItem(SIDEBAR_KEY) === "true"
  );

  const toggle = () => setCollapsed(prev => {
    const next = !prev;
    localStorage.setItem(SIDEBAR_KEY, String(next));
    return next;
  });

  const handleLogout = () => { logout(); navigate("/login"); };

  useEffect(() => {
    apiPing().catch(() => {});
    const id = setInterval(() => apiPing().catch(() => {}), 4 * 60 * 1000);
    return () => clearInterval(id);
  }, []);

  const showReflections = user && REFLECTION_ROLES.includes(user.role);
  const showChildren = user?.is_parent === true;

  const navLinkStyle = (isActive: boolean) => ({
    display: "flex",
    alignItems: "center",
    justifyContent: collapsed ? "center" as const : "flex-start" as const,
    gap: 10,
    padding: collapsed ? "10px 0" : "9px 9px 9px 9px",
    borderRadius: 7,
    borderLeft: isActive ? `3px solid ${theme.primary}` : "3px solid transparent",
    color: isActive ? theme.primary : "#888",
    fontWeight: isActive ? 600 : 400,
    background: isActive ? theme.primary + "18" : "transparent",
    textDecoration: "none",
    fontSize: 14,
    transition: "background 0.15s, color 0.15s, border-color 0.15s",
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
  });

  const footerBtnBase: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: collapsed ? "center" : "flex-start",
    gap: 8,
    width: "100%",
    padding: collapsed ? "9px 0" : "8px 12px",
    borderRadius: 7,
    border: "none",
    background: "transparent",
    color: "#555",
    fontSize: 13,
    cursor: "pointer",
    whiteSpace: "nowrap",
    overflow: "hidden",
    transition: "background 0.15s, color 0.15s",
  };

  return (
    <div style={{ display: "flex", height: "100vh", background: "#0d0d0d" }}>

      {/* ── Fixed logo header — always visible, floats over content when nav is collapsed ── */}
      <div style={{
        position: "fixed",
        top: 0,
        left: 0,
        width: 220,
        height: HEADER_H,
        zIndex: 100,
        background: "#111",
        borderBottom: "1px solid #1e1e1e",
        borderRight: collapsed ? "none" : "1px solid #1e1e1e",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}>
        <KTSBLogo size={120} />
        <button
          onClick={toggle}
          title={collapsed ? t("nav.sidebar_expand") : t("nav.sidebar_collapse")}
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 24,
            height: 24,
            borderRadius: 6,
            border: "1px solid #2a2a2a",
            background: "transparent",
            color: "#555",
            cursor: "pointer",
            transition: "background 0.15s, color 0.15s",
          }}
          onMouseEnter={e => {
            (e.currentTarget as HTMLButtonElement).style.background = "#1e1e1e";
            (e.currentTarget as HTMLButtonElement).style.color = "#aaa";
          }}
          onMouseLeave={e => {
            (e.currentTarget as HTMLButtonElement).style.background = "transparent";
            (e.currentTarget as HTMLButtonElement).style.color = "#555";
          }}
        >
          {collapsed ? <ChevronRight size={13} /> : <ChevronLeft size={13} />}
        </button>
      </div>

      {/* ── Collapsible sidebar — nav + footer only ── */}
      <aside style={{
        width: collapsed ? 0 : 220,
        paddingTop: HEADER_H,
        background: "#111",
        borderRight: "1px solid #1e1e1e",
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        transition: "width 0.22s ease",
        overflow: "hidden",
      }}>

        {/* Nav links */}
        <nav style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, padding: "12px 8px" }}>
          <NavLink
            to="/"
            end
            style={({ isActive }) => navLinkStyle(isActive)}
          >
            <MessageSquare size={17} style={{ flexShrink: 0 }} />
            <span>{t("user_sidebar.home", { name: assistantName })}</span>
          </NavLink>

          {showReflections && (
            <NavLink
              to="/reflections"
              style={({ isActive }) => navLinkStyle(isActive)}
            >
              <BookOpen size={17} style={{ flexShrink: 0 }} />
              <span>{t("user_sidebar.reflections")}</span>
            </NavLink>
          )}

          {showChildren && (
            <NavLink
              to="/children"
              style={({ isActive }) => navLinkStyle(isActive)}
            >
              <Users size={17} style={{ flexShrink: 0 }} />
              <span>{t("user_sidebar.mine_barn")}</span>
            </NavLink>
          )}

          <NavLink
            to="/settings"
            style={({ isActive }) => navLinkStyle(isActive)}
          >
            <Settings size={17} style={{ flexShrink: 0 }} />
            <span>{t("user_sidebar.settings")}</span>
          </NavLink>
        </nav>

        {/* Footer: user + actions */}
        <div style={{
          borderTop: "1px solid #1e1e1e",
          padding: "10px 8px",
          display: "flex", flexDirection: "column", gap: 2,
        }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            background: "#161616", border: "1px solid #252525",
            borderRadius: 20, padding: "5px 10px 5px 8px",
            marginBottom: 4, overflow: "hidden",
          }}>
            <span style={{ fontSize: 16, flexShrink: 0 }}>{user?.avatar}</span>
            <span style={{
              color: "#bbb", fontSize: 12, fontWeight: 500,
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            }}>
              {user?.display_name}
            </span>
          </div>

          <button
            onClick={() => setShowPin(true)}
            style={footerBtnBase}
            onMouseEnter={e => {
              (e.currentTarget as HTMLButtonElement).style.color = "#ccc";
              (e.currentTarget as HTMLButtonElement).style.background = "#1a1a1a";
            }}
            onMouseLeave={e => {
              (e.currentTarget as HTMLButtonElement).style.color = "#555";
              (e.currentTarget as HTMLButtonElement).style.background = "transparent";
            }}
          >
            <ShieldCheck size={14} style={{ flexShrink: 0 }} />
            <span>{t("user_sidebar.change_pin")}</span>
          </button>

          {user?.role === "admin" && (
            <button
              onClick={() => navigate("/admin")}
              style={footerBtnBase}
              onMouseEnter={e => {
                (e.currentTarget as HTMLButtonElement).style.color = "#ccc";
                (e.currentTarget as HTMLButtonElement).style.background = "#1a1a1a";
              }}
              onMouseLeave={e => {
                (e.currentTarget as HTMLButtonElement).style.color = "#555";
                (e.currentTarget as HTMLButtonElement).style.background = "transparent";
              }}
            >
              <ShieldCheck size={14} style={{ flexShrink: 0 }} />
              <span>{t("user_sidebar.admin_panel")}</span>
            </button>
          )}

          <button
            onClick={handleLogout}
            style={footerBtnBase}
            onMouseEnter={e => {
              (e.currentTarget as HTMLButtonElement).style.color = "#c084fc";
              (e.currentTarget as HTMLButtonElement).style.background = "#1a1530";
            }}
            onMouseLeave={e => {
              (e.currentTarget as HTMLButtonElement).style.color = "#555";
              (e.currentTarget as HTMLButtonElement).style.background = "transparent";
            }}
          >
            <LogOut size={14} style={{ flexShrink: 0 }} />
            <span>{t("user_sidebar.logout")}</span>
          </button>
        </div>
      </aside>

      {/* ── Main content ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
        <Outlet />
      </div>

      {showPin && <ChangePinModal onClose={() => setShowPin(false)} />}
    </div>
  );
}
