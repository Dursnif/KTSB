import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { apiSystemStatus, apiListUsers, apiSystemOverview, apiGetOnboardingStatus } from "../../services/api";
import type { KaareUser, ServiceStatus, ModelStatus, OnboardingStatus } from "../../services/api";

function lastSeenLabel(lastSeen: string | null, nowLabel: string): string {
  if (!lastSeen) return "";
  const diffMin = Math.floor((Date.now() - new Date(lastSeen).getTime()) / 60000);
  if (diffMin < 1) return nowLabel;
  if (diffMin < 60) return `${diffMin}m`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}t`;
  return "";
}

const S = {
  h1: { color: "#fff", fontSize: 22, fontWeight: 700, margin: "0 0 28px" } as React.CSSProperties,
  grid3: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 20, marginBottom: 28 } as React.CSSProperties,
  grid2: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 28 } as React.CSSProperties,
  card: { background: "#1a1a1a", borderRadius: 12, padding: "20px 24px" } as React.CSSProperties,
  cardTitle: { color: "#888", fontSize: 12, fontWeight: 600, textTransform: "uppercase" as const, letterSpacing: 1, marginBottom: 14 },
  stat: { color: "#fff", fontSize: 32, fontWeight: 700 },
  statSub: { color: "#666", fontSize: 13, marginTop: 4 },
};

function StatusDot({ online, color }: { online: boolean; color: string }) {
  return (
    <div style={{
      width: 10, height: 10, borderRadius: "50%", flexShrink: 0,
      background: online ? color : "#333",
      boxShadow: online ? `0 0 6px ${color}88` : "none",
    }} />
  );
}

function ServiceGrid({ items }: { items: ServiceStatus[] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 8, marginTop: 4 }}>
      {items.map(s => (
        <div key={s.key} style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "10px 14px", borderRadius: 8,
          background: s.online ? `${s.color}0d` : "#0d0d0d",
          border: `1px solid ${s.online ? s.color + "33" : "#1a1a1a"}`,
        }}>
          <StatusDot online={s.online} color={s.color} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: s.online ? "#fff" : "#555", fontSize: 13, fontWeight: 600 }}>
              {s.name}
            </div>
            <div style={{ color: "#444", fontSize: 11, marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {s.description}
            </div>
          </div>
          <div style={{
            fontSize: 9, fontWeight: 700, letterSpacing: 0.5,
            padding: "2px 6px", borderRadius: 4,
            background: s.online ? `${s.color}22` : "#1a1a1a",
            color: s.online ? s.color : "#333",
            border: `1px solid ${s.online ? s.color + "44" : "#222"}`,
          }}>
            {s.online ? "ON" : "OFF"}
          </div>
        </div>
      ))}
    </div>
  );
}

function ModelGrid({ items }: { items: ModelStatus[] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8, marginTop: 4 }}>
      {items.map(m => (
        <div key={m.key} style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "10px 14px", borderRadius: 8,
          background: m.online ? `${m.color}0d` : "#0d0d0d",
          border: `1px solid ${m.online ? m.color + "33" : "#1a1a1a"}`,
        }}>
          <StatusDot online={m.online} color={m.color} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: m.online ? "#fff" : "#555", fontSize: 13, fontWeight: 600 }}>
              {m.name}
            </div>
            <div style={{ color: "#555", fontSize: 11, marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {m.model}
            </div>
            <div style={{ color: "#3a3a3a", fontSize: 10, marginTop: 1 }}>
              {m.platform}
            </div>
          </div>
          <div style={{
            fontSize: 9, fontWeight: 700, letterSpacing: 0.5,
            padding: "2px 6px", borderRadius: 4,
            background: m.online ? `${m.color}22` : "#1a1a1a",
            color: m.online ? m.color : "#333",
            border: `1px solid ${m.online ? m.color + "44" : "#222"}`,
          }}>
            {m.online ? "ON" : "OFF"}
          </div>
        </div>
      ))}
    </div>
  );
}

function OnboardingBanner({ status }: { status: OnboardingStatus }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const incomplete = status.steps.filter(s => !s.complete);
  if (incomplete.length === 0) return null;

  return (
    <div style={{
      background: "#2a2000",
      border: "1px solid #7a5c00",
      borderRadius: 10,
      padding: "14px 20px",
      marginBottom: 24,
      display: "flex",
      alignItems: "flex-start",
      gap: 16,
    }}>
      <span style={{ fontSize: 20, marginTop: 2 }}>⚠️</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <p style={{ color: "#ffd166", fontWeight: 600, fontSize: 14, margin: "0 0 4px" }}>
          {t("dashboard.not_configured")}
        </p>
        <p style={{ color: "#a89060", fontSize: 13, margin: "0 0 8px" }}>
          {t("dashboard.missing", { items: incomplete.map(s => s.label).join(", ") })}
        </p>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" as const }}>
          <button
            onClick={() => navigate("/onboarding")}
            style={{ background: "#7a5c00", color: "#ffd166", border: "none", borderRadius: 6, padding: "6px 14px", fontSize: 13, cursor: "pointer", fontWeight: 500 }}
          >
            {t("dashboard.finish_setup")}
          </button>
          {status.optional_hints.filter(h => !h.set).map(hint => (
            <span key={hint.id} style={{ background: "#1a1800", color: "#888", fontSize: 12, borderRadius: 6, padding: "6px 12px", border: "1px solid #333" }}>
              {t("dashboard.optional", { label: hint.label })}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { t } = useTranslation();
  const [modules,     setModules]     = useState<{ name: string; enabled: boolean }[]>([]);
  const [users,       setUsers]       = useState<KaareUser[]>([]);
  const [services,    setServices]    = useState<ServiceStatus[]>([]);
  const [models,      setModels]      = useState<ModelStatus[]>([]);
  const [onboarding,  setOnboarding]  = useState<OnboardingStatus | null>(null);
  const [isChecking,  setIsChecking]  = useState(false);
  const [isDone,      setIsDone]      = useState(false);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const refreshUsers = useCallback(() => { apiListUsers().then(setUsers).catch(() => {}); }, []);
  const refreshOverview = useCallback(() => {
    apiSystemOverview()
      .then(d => { setServices(d.services); setModels(d.models); setLastChecked(new Date()); })
      .catch(() => {});
  }, []);
  const manualRefresh = useCallback(() => {
    if (isChecking) return;
    setIsChecking(true);
    setIsDone(false);
    Promise.all([
      apiSystemOverview(),
      new Promise<void>(r => setTimeout(r, 1500)),
    ])
      .then(([d]) => { setServices(d.services); setModels(d.models); setLastChecked(new Date()); })
      .catch(() => {})
      .finally(() => {
        setIsChecking(false);
        setIsDone(true);
        setTimeout(() => setIsDone(false), 1500);
      });
  }, [isChecking]);

  useEffect(() => {
    apiSystemStatus().then(d => setModules(d.modules)).catch(() => {});
    refreshUsers();
    refreshOverview();
    apiGetOnboardingStatus().then(setOnboarding).catch(() => {});
    const id1 = setInterval(refreshUsers,    30_000);
    const id2 = setInterval(refreshOverview, 30_000);
    return () => { clearInterval(id1); clearInterval(id2); };
  }, [refreshUsers, refreshOverview]);

  const onlineUsers    = users.filter(u => u.is_online && u.is_active).length;
  const activeUsers    = users.filter(u => u.is_active).length;
  const onlineServices = services.filter(s => s.online).length;
  const onlineModels   = models.filter(m => m.online).length;
  const nowLabel       = t("dashboard.online_now");

  return (
    <div>
      <h1 style={S.h1}>{t("dashboard.title")}</h1>

      {onboarding && !onboarding.complete && <OnboardingBanner status={onboarding} />}

      {/* Stat cards */}
      <div style={S.grid3}>
        <div className="admin-card" style={{ ...S.card, borderTop: "3px solid #6c8ebf", padding: "18px 24px" }}>
          <div style={S.cardTitle}>{t("dashboard.users_card")}</div>
          <div style={S.stat}>{onlineUsers}</div>
          <div style={S.statSub}>{t("dashboard.users_stat", { active: activeUsers, total: users.length })}</div>
        </div>
        <div className="admin-card" style={{ ...S.card, borderTop: "3px solid #82b366", padding: "18px 24px" }}>
          <div style={S.cardTitle}>{t("dashboard.services_card")}</div>
          <div style={S.stat}>{onlineServices}</div>
          <div style={S.statSub}>{t("dashboard.services_stat", { total: services.length })}</div>
        </div>
        <div className="admin-card" style={{ ...S.card, borderTop: "3px solid #c084fc", padding: "18px 24px" }}>
          <div style={S.cardTitle}>{t("dashboard.models_card")}</div>
          <div style={S.stat}>{onlineModels}</div>
          <div style={S.statSub}>{t("dashboard.models_stat", { total: models.length })}</div>
        </div>
      </div>

      {/* Services + Models — shared refresh bar */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <div style={{ color: "#888", fontSize: 11, letterSpacing: 0.5 }}>
          {lastChecked
            ? `${t("dashboard.last_checked")} ${Math.round((Date.now() - lastChecked.getTime()) / 1000)}s ${t("dashboard.ago")}`
            : ""}
        </div>
        <button
          onClick={manualRefresh}
          style={{
            background:  isDone ? "#1a2e1a" : "#222",
            color:       isDone ? "#4caf50" : isChecking ? "#666" : "#aaa",
            border:      `1px solid ${isDone ? "#4caf5066" : "#333"}`,
            borderRadius: 6,
            padding: "5px 12px",
            fontSize: 12,
            cursor: "pointer",
            fontWeight: 500,
            display: "flex",
            alignItems: "center",
            gap: 6,
            opacity: isChecking ? 0.7 : 1,
            transition: "background 0.3s, color 0.3s, border-color 0.3s, opacity 0.2s",
          }}
        >
          <span
            style={{ display: "inline-block", transformOrigin: "center" }}
            className={isChecking ? "dashboard-spin" : ""}
          >{isDone ? "✓" : "↻"}</span>
          {isChecking ? t("dashboard.checking") : isDone ? t("dashboard.ok") : t("dashboard.refresh")}
        </button>
      </div>

      <div className="admin-card" style={{ ...S.card, marginBottom: 20 }}>
        <div style={S.cardTitle}>{t("dashboard.services_card")}</div>
        <ServiceGrid items={services} />
      </div>

      <div className="admin-card" style={{ ...S.card, marginBottom: 20 }}>
        <div style={S.cardTitle}>{t("dashboard.models_card")}</div>
        <ModelGrid items={models} />
      </div>

      {/* Modules + Users */}
      <div style={S.grid2}>
        <div className="admin-card" style={S.card}>
          <div style={S.cardTitle}>{t("dashboard.modules_card")}</div>
          {modules.map(m => (
            <div key={m.name} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid #222" }}>
              <span style={{ color: "#ccc", fontSize: 14 }}>{m.name}</span>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ color: m.enabled ? "#4caf50" : "#555", fontSize: 12 }}>
                  {m.enabled ? t("dashboard.active") : t("dashboard.inactive")}
                </span>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: m.enabled ? "#4caf50" : "#555" }} />
              </div>
            </div>
          ))}
        </div>

        <div className="admin-card" style={S.card}>
          <div style={S.cardTitle}>{t("dashboard.users_card")}</div>
          {users.slice(0, 8).map(u => {
            const online = u.is_online && u.is_active;
            const label  = lastSeenLabel(u.last_seen, nowLabel);
            return (
              <div key={u.username} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderBottom: "1px solid #222" }}>
                <span style={{ fontSize: 20 }}>{u.avatar}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ color: "#ddd", fontSize: 14 }}>{u.display_name}</div>
                  <div style={{ color: "#666", fontSize: 12 }}>{t(`dashboard.roles.${u.role}`, u.role)}</div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  {label && <span style={{ fontSize: 11, color: online ? "#4caf50" : "#555" }}>{label}</span>}
                  <div style={{
                    width: 8, height: 8, borderRadius: "50%",
                    background: online ? "#4caf50" : "#444",
                    boxShadow: online ? "0 0 5px #4caf5088" : "none",
                  }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
