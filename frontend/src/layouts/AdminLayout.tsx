import { useState } from "react";
import { usePupilAnimation } from "../hooks/usePupilAnimation";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Users, Server, Bot, BookOpen,
  Wrench, Settings, Home, ChevronLeft, ChevronRight, LogOut, Map, Speaker, ShieldAlert,
} from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { useTranslation } from "react-i18next";
import { useAssistantName } from "../contexts/AssistantNameContext";
import { KTSBLogo, KTSBEye } from "../components/KTSBLogo";
import "../components/ktsb-logo.css";

type NavItemDef = {
  to: string;
  labelKey: string;
  label?: string;
  icon: React.ElementType;
  end?: boolean;
  separator?: boolean;
};

export default function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const assistantName = useAssistantName();
  usePupilAnimation();

  const navDefs: NavItemDef[] = [
    { to: "/admin",                labelKey: "nav.dashboard",   icon: LayoutDashboard, end: true },
    { to: "/admin/users",          labelKey: "nav.users",        icon: Users },
    { to: "/admin/system",         labelKey: "nav.system",       icon: Server },
    { to: "/admin/agent-messages", labelKey: "nav.agents",       icon: Bot },
    { to: "/admin/reflections",    labelKey: "nav.reflections",  icon: BookOpen },
    { to: "/admin/security",       labelKey: "nav.security",     icon: ShieldAlert },
    { to: "/admin/tools",          labelKey: "nav.tools",        icon: Wrench },
    { to: "/admin/aliases",        labelKey: "nav.aliases",      icon: Map },
    { to: "/admin/nodes",          labelKey: "nav.nodes",        icon: Speaker },
    { to: "/admin/settings",       labelKey: "nav.settings",     icon: Settings },
    { to: "/", labelKey: "nav.back_to", label: t("nav.back_to", { name: assistantName }), icon: Home, separator: true },
  ];

  const [collapsed, setCollapsed] = useState<boolean>(() => {
    return localStorage.getItem("admin-sidebar-collapsed") === "true";
  });

  const handleLogout = () => { logout(); navigate("/login"); };

  const toggle = () => {
    setCollapsed(prev => {
      const next = !prev;
      localStorage.setItem("admin-sidebar-collapsed", String(next));
      return next;
    });
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#0d0d0d" }}>
      <aside style={{
        width: collapsed ? 56 : 220,
        background: "#111",
        borderRight: "1px solid #1e1e1e",
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        transition: "width 0.22s ease",
        overflow: "hidden",
      }}>

        {/* ── Header: logo + collapse toggle ── */}
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: collapsed ? "10px 0" : "10px 12px",
          borderBottom: "1px solid #1e1e1e",
          flexShrink: 0,
          position: "relative",
          minHeight: collapsed ? 56 : 140,
          transition: "min-height 0.22s ease",
        }}>
          {collapsed ? (
            <KTSBEye size={34} variant="glyph" />
          ) : (
            <KTSBLogo size={120} />
          )}
          <button
            onClick={toggle}
            title={collapsed ? t("nav.sidebar_expand") : t("nav.sidebar_collapse")}
            style={{
              position: collapsed ? "static" : "absolute",
              top: collapsed ? undefined : 8,
              right: collapsed ? undefined : 8,
              marginTop: collapsed ? 6 : 0,
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
              flexShrink: 0,
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

        {/* ── Nav links ── */}
        <nav style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, padding: "12px 8px" }}>
          {navDefs.map(({ to, labelKey, label, icon: Icon, end, separator }) => {
            const displayLabel = label ?? t(labelKey);
            return (
              <div key={to}>
                {separator && (
                  <div style={{ margin: "8px 4px", borderTop: "1px solid #252525" }} />
                )}
                <NavLink
                  to={to}
                  end={end}
                  title={collapsed ? displayLabel : undefined}
                  style={({ isActive }) => ({
                    display: "flex",
                    alignItems: "center",
                    justifyContent: collapsed ? "center" : "flex-start",
                    gap: 10,
                    padding: collapsed ? "10px 0" : "9px 9px",
                    borderRadius: 7,
                    borderLeft: isActive ? "3px solid #7c6aff" : "3px solid transparent",
                    color: isActive ? "#a78bfa" : "#888",
                    fontWeight: isActive ? 600 : 400,
                    background: isActive ? "#1e1b3a" : "transparent",
                    textDecoration: "none",
                    fontSize: 14,
                    transition: "background 0.15s, color 0.15s, border-color 0.15s",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                  })}
                >
                  <Icon size={17} style={{ flexShrink: 0 }} />
                  {!collapsed && <span>{displayLabel}</span>}
                </NavLink>
              </div>
            );
          })}
        </nav>

        {/* ── Footer: user + logout ── */}
        <div style={{
          borderTop: "1px solid #1e1e1e",
          padding: "10px 8px",
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}>
          {!collapsed && (
            <div style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              background: "#161616",
              border: "1px solid #252525",
              borderRadius: 20,
              padding: "5px 10px 5px 8px",
              marginBottom: 2,
              overflow: "hidden",
            }}>
              <span style={{ fontSize: 16, flexShrink: 0 }}>{user?.avatar}</span>
              <span style={{
                color: "#bbb",
                fontSize: 12,
                fontWeight: 500,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}>
                {user?.display_name}
              </span>
            </div>
          )}

          <button
            onClick={handleLogout}
            title={collapsed ? t("nav.logout") : undefined}
            style={{
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
            }}
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
            {!collapsed && <span>{t("nav.logout")}</span>}
          </button>
        </div>
      </aside>

      <main className="admin-main" style={{ flex: 1, overflowY: "auto" }}>
        <Outlet />
      </main>
    </div>
  );
}
