import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import QRCode from "react-qr-code";
import { useIsMobile } from "../../hooks/useIsMobile";
import {
  apiListUsers, apiCreateUser, apiUpdateUser,
  apiUpdatePin, apiDeleteUser, apiListPersonalities,
  apiVpnListClients, apiVpnCreateClient, apiVpnDeleteClient,
  apiVoiceStatus, apiVoiceEnroll, apiVoiceDelete,
  apiGetToolPermissions, apiSaveToolPermissions,
  type KaareUser, type Role, type VpnAccess, type VpnClient, type ToolPermissions,
} from "../../services/api";

const VPN_COLORS: Record<VpnAccess, string> = {
  local_only:  "#2a2a2a",
  ai_only:     "#1a3a5a",
  full_access: "#1a4a2a",
};

const ROLES: Role[] = ["child", "teen", "young_adult", "adult", "admin"];
const AVATARS = ["👤", "👦", "👧", "👨", "👩", "🧑", "👴", "👵", "🧒", "🛡️"];

const S = {
  page: { padding: "0 0 40px" } as React.CSSProperties,
  header: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 } as React.CSSProperties,
  h1: { color: "#fff", fontSize: 22, fontWeight: 700, margin: 0 } as React.CSSProperties,
  btn: (color = "#646cff") => ({
    padding: "8px 18px", borderRadius: 8, border: "none",
    background: color, color: "#fff", fontSize: 14,
    fontWeight: 600, cursor: "pointer",
  } as React.CSSProperties),
  table: { width: "100%", borderCollapse: "collapse" as const },
  th: { color: "#888", fontSize: 12, textAlign: "left" as const, padding: "8px 12px", borderBottom: "1px solid #333" },
  td: { color: "#ddd", fontSize: 14, padding: "12px 12px", borderBottom: "1px solid #222", verticalAlign: "middle" as const },
  badge: (role: Role) => {
    const colors: Record<Role, string> = {
      child: "#1a4a8a", teen: "#1a6a4a", young_adult: "#4a4a1a",
      adult: "#3a2a6a", admin: "#6a1a1a",
    };
    return { background: colors[role], color: "#ddd", padding: "3px 10px", borderRadius: 12, fontSize: 12 };
  },
  overlay: {
    position: "fixed" as const, inset: 0, background: "rgba(0,0,0,0.7)",
    display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
  },
  modal: {
    background: "#1a1a1a", borderRadius: 12, padding: 32, width: 420,
    boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
  } as React.CSSProperties,
  mTitle: { color: "#fff", fontSize: 18, fontWeight: 700, marginBottom: 20 },
  label: { color: "#aaa", fontSize: 13, marginBottom: 5, display: "block" } as React.CSSProperties,
  input: {
    width: "100%", padding: "9px 12px", borderRadius: 7, border: "1px solid #333",
    background: "#111", color: "#fff", fontSize: 14, boxSizing: "border-box" as const, marginBottom: 14,
  },
  select: {
    width: "100%", padding: "9px 12px", borderRadius: 7, border: "1px solid #333",
    background: "#111", color: "#fff", fontSize: 14, marginBottom: 14,
  },
  row: { display: "flex", gap: 10, marginTop: 8 } as React.CSSProperties,
  err: { color: "#ff6b6b", fontSize: 13, marginTop: 6 },
};

type Modal =
  | { type: "create" }
  | { type: "edit"; user: KaareUser }
  | { type: "pin"; user: KaareUser }
  | { type: "delete"; user: KaareUser }
  | { type: "vpn"; user: KaareUser }
  | { type: "voice"; user: KaareUser }
  | null;

// ── Tool permissions matrix ───────────────────────────────────────────────────

const TOOL_ROWS: { key: string; indent?: boolean }[] = [
  { key: "styr_enhet" },
  { key: "les_ha" },
  { key: "timer" },
  { key: "get_weather" },
  { key: "library" },
  { key: "library_online" },
  { key: "søk_nett" },
  { key: "kare_image" },
  { key: "se_bilder" },
  { key: "media" },
  { key: "notat" },
  { key: "announce" },
  { key: "reason_freely" },
  { key: "minne" },
  { key: "kamera" },
  { key: "søk_i_argus" },
  { key: "les_møte" },
  { key: "mechanic" },
  { key: "utforsk_kode" },
  { key: "inspiser_system" },
  { key: "ssh_kommando" },
  { key: "local_kommando" },
  { key: "restart_docker_container" },
];

const PERM_ROLES: Role[] = ["child", "teen", "young_adult", "adult", "admin"];

function ToolPermissionsMatrix() {
  const { t } = useTranslation();
  const [perms, setPerms] = useState<ToolPermissions | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const load = () => {
    apiGetToolPermissions().then(p => { setPerms(p); setDirty(false); setMsg(""); }).catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const toggle = (role: Role, toolKey: string) => {
    if (!perms || role === "admin") return;
    setPerms(prev => {
      if (!prev) return prev;
      const current = new Set(prev.roles[role] ?? []);
      if (current.has(toolKey)) current.delete(toolKey);
      else current.add(toolKey);
      return { ...prev, roles: { ...prev.roles, [role]: Array.from(current) } };
    });
    setDirty(true);
    setMsg("");
  };

  const save = async () => {
    if (!perms) return;
    setSaving(true);
    setMsg("");
    try {
      await apiSaveToolPermissions(perms);
      setDirty(false);
      setMsg(t("users.tools.saved"));
    } catch {
      setMsg(t("users.tools.error_save"));
    } finally {
      setSaving(false);
    }
  };

  const rollback = async () => {
    load();
    setMsg(t("users.tools.rolled_back"));
  };

  const isChecked = (role: Role, toolKey: string): boolean => {
    if (!perms) return false;
    if (role === "admin") return true;
    const list = perms.roles[role] ?? [];
    if (list.includes("all")) return true;
    return list.includes(toolKey);
  };

  const MS: Record<string, React.CSSProperties> = {
    section: { marginTop: 40 },
    heading: { color: "#fff", fontSize: 18, fontWeight: 700, marginBottom: 4 },
    sub: { color: "#666", fontSize: 13, marginBottom: 20 },
    table: { width: "100%", borderCollapse: "collapse" },
    th: { color: "#888", fontSize: 11, fontWeight: 600, textAlign: "center", padding: "6px 8px",
          borderBottom: "1px solid #333", whiteSpace: "nowrap" },
    thLabel: { color: "#888", fontSize: 11, fontWeight: 600, textAlign: "left", padding: "6px 12px",
               borderBottom: "1px solid #333" },
    td: { textAlign: "center", padding: "6px 8px", borderBottom: "1px solid #1a1a1a" },
    tdLabel: { color: "#ccc", fontSize: 13, padding: "6px 12px", borderBottom: "1px solid #1a1a1a" },
    actions: { display: "flex", gap: 10, marginTop: 20, alignItems: "center" },
    msgOk: { color: "#4caf50", fontSize: 13 },
    msgErr: { color: "#ff6b6b", fontSize: 13 },
    lock: { color: "#555", fontSize: 12 },
    internal: { color: "#555", fontSize: 12, padding: "10px 12px", fontStyle: "italic" },
  };

  if (!perms) return <div style={{ color: "#555", padding: 20 }}>{t("users.tools.loading")}</div>;

  return (
    <div style={MS.section}>
      <div style={MS.heading}>{t("users.tools.title")}</div>
      <div style={MS.sub}>{t("users.tools.description")}</div>

      <table style={MS.table}>
        <thead>
          <tr>
            <th style={MS.thLabel}>{t("users.tools.col_tool")}</th>
            {PERM_ROLES.map(r => (
              <th key={r} style={MS.th}>{t(`dashboard.roles.${r}`, r)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {TOOL_ROWS.map(({ key, indent }) => (
            <tr key={key} style={{ background: indent ? "#0d0d0d" : undefined }}>
              <td style={{ ...MS.tdLabel, paddingLeft: indent ? 28 : 12 }}>
                {t(`users.tools.labels.${key}`, key)}
              </td>
              {PERM_ROLES.map(role => {
                const locked = role === "admin";
                const checked = isChecked(role, key);
                return (
                  <td key={role} style={MS.td}>
                    {locked ? (
                      <span style={MS.lock}>✓</span>
                    ) : (
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(role, key)}
                        style={{ cursor: "pointer", width: 15, height: 15, accentColor: "#646cff" }}
                      />
                    )}
                  </td>
                );
              })}
            </tr>
          ))}

          <tr>
            <td colSpan={PERM_ROLES.length + 1} style={MS.internal}>
              {t("users.tools.always_included")}
            </td>
          </tr>
        </tbody>
      </table>

      <div style={MS.actions}>
        <button
          style={{ ...S.btn(), opacity: saving || !dirty ? 0.5 : 1 }}
          onClick={save}
          disabled={saving || !dirty}
        >
          {saving ? "…" : t("users.tools.save")}
        </button>
        <button style={S.btn("#333")} onClick={rollback} disabled={saving}>
          {t("users.tools.rollback")}
        </button>
        {msg && <span style={msg === t("users.tools.error_save") ? MS.msgErr : MS.msgOk}>{msg}</span>}
      </div>
    </div>
  );
}

// ── Mobile user cards ─────────────────────────────────────────────────────────

function UserCards({ users, onModal, setError }: {
  users: KaareUser[];
  onModal: (m: Modal) => void;
  setError: (e: string) => void;
}) {
  const { t } = useTranslation();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {users.map(u => (
        <div key={u.username} style={{
          background: "#1a1a1a",
          borderRadius: 10,
          border: "1px solid #333",
          overflow: "hidden",
        }}>
          <div style={{ padding: "14px 16px", borderBottom: "1px solid #222" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 22 }}>{u.avatar}</span>
                <span style={{ color: "#fff", fontSize: 15, fontWeight: 600 }}>{u.display_name}</span>
              </div>
              <span style={S.badge(u.role)}>{t(`dashboard.roles.${u.role}`, u.role)}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <code style={{ color: "#888", fontSize: 12 }}>@{u.username}</code>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{
                  background: VPN_COLORS[u.vpn_access || "local_only"],
                  color: "#ddd", padding: "2px 8px", borderRadius: 10, fontSize: 11,
                }}>
                  {t(`users.vpn_access.${u.vpn_access || "local_only"}`)}
                </span>
                <span style={{ color: u.is_active ? "#4caf50" : "#888", fontSize: 13 }}>
                  {u.is_active ? "●" : "○"}
                </span>
              </div>
            </div>
          </div>
          <div style={{ padding: "10px 12px", display: "flex", gap: 8, flexWrap: "wrap" as const }}>
            <button style={{ ...S.btn("#1a3a3a"), flex: 1 }}
              onClick={() => { setError(""); onModal({ type: "vpn", user: u }); }}>
              {t("users.actions.vpn")}
            </button>
            <button style={{ ...S.btn("#1a2a4a"), flex: 1 }}
              onClick={() => { setError(""); onModal({ type: "voice", user: u }); }}>
              {t("users.actions.voice")}
            </button>
            <button style={{ ...S.btn("#2a2a4a"), flex: 1 }}
              onClick={() => { setError(""); onModal({ type: "edit", user: u }); }}>
              {t("users.actions.edit")}
            </button>
            <button style={{ ...S.btn("#2a3a2a"), flex: "0 0 auto" as const }}
              onClick={() => { setError(""); onModal({ type: "pin", user: u }); }}>
              {t("users.actions.pin")}
            </button>
            <button style={{ ...S.btn("#3a1a1a"), flex: "0 0 auto" as const }}
              onClick={() => { setError(""); onModal({ type: "delete", user: u }); }}>
              {t("users.actions.delete")}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Main Users page ───────────────────────────────────────────────────────────

export default function Users() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [users, setUsers] = useState<KaareUser[]>([]);
  const [modal, setModal] = useState<Modal>(null);
  const [error, setError] = useState("");

  const load = () => apiListUsers().then(setUsers).catch(() => {});
  useEffect(() => { load(); }, []);

  return (
    <div style={S.page}>
      <div style={{
        ...S.header,
        ...(isMobile ? { flexDirection: "column", alignItems: "stretch", gap: 12 } : {}),
      }}>
        <h1 style={S.h1}>{t("users.title")}</h1>
        <button
          style={{ ...S.btn(), ...(isMobile ? { padding: "12px 18px", fontSize: 15 } : {}) }}
          onClick={() => { setError(""); setModal({ type: "create" }); }}
        >
          {t("users.new_user")}
        </button>
      </div>

      {isMobile ? (
        <UserCards users={users} onModal={setModal} setError={setError} />
      ) : (
        <table style={S.table}>
          <thead>
            <tr>
              <th style={S.th}>{t("users.table.user")}</th>
              <th style={S.th}>{t("users.table.username")}</th>
              <th style={S.th}>{t("users.table.role")}</th>
              <th style={S.th}>{t("users.table.personality")}</th>
              <th style={S.th}>{t("users.table.vpn_access")}</th>
              <th style={S.th}>{t("users.table.status")}</th>
              <th style={S.th}>{t("users.table.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {users.map(u => (
              <tr key={u.username}>
                <td style={S.td}>{u.avatar} {u.display_name}</td>
                <td style={S.td}><code style={{ color: "#888" }}>{u.username}</code></td>
                <td style={S.td}>
                  <span style={S.badge(u.role)}>{t(`dashboard.roles.${u.role}`, u.role)}</span>
                </td>
                <td style={S.td}><code style={{ color: "#aaa", fontSize: 12 }}>{t(`personality_variants.${u.personality || "standard"}`, u.personality || "standard")}</code></td>
                <td style={S.td}>
                  <span style={{
                    background: VPN_COLORS[u.vpn_access || "local_only"],
                    color: "#ddd", padding: "3px 10px", borderRadius: 12, fontSize: 12,
                  }}>
                    {t(`users.vpn_access.${u.vpn_access || "local_only"}`)}
                  </span>
                </td>
                <td style={S.td}>
                  <span style={{ color: u.is_active ? "#4caf50" : "#888" }}>
                    {u.is_active ? t("users.status.active") : t("users.status.inactive")}
                  </span>
                </td>
                <td style={S.td}>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button style={S.btn("#2a2a4a")} onClick={() => { setError(""); setModal({ type: "edit", user: u }); }}>{t("users.actions.edit")}</button>
                    <button style={S.btn("#2a3a2a")} onClick={() => { setError(""); setModal({ type: "pin", user: u }); }}>{t("users.actions.pin")}</button>
                    <button style={S.btn("#1a3a3a")} onClick={() => { setError(""); setModal({ type: "vpn", user: u }); }}>{t("users.actions.vpn")}</button>
                    <button style={S.btn("#1a2a4a")} onClick={() => { setError(""); setModal({ type: "voice", user: u }); }}>{t("users.actions.voice")}</button>
                    <button style={S.btn("#3a1a1a")} onClick={() => { setError(""); setModal({ type: "delete", user: u }); }}>{t("users.actions.delete")}</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!isMobile && <ToolPermissionsMatrix />}

      {modal && (
        <div
          style={{
            ...S.overlay,
            ...(isMobile ? { alignItems: "flex-start", paddingTop: 16, overflowY: "auto" } : {}),
          }}
          onClick={e => e.target === e.currentTarget && setModal(null)}
        >
          {modal.type === "create" && (
            <CreateModal onDone={() => { load(); setModal(null); }} onError={setError} error={error} />
          )}
          {modal.type === "edit" && (
            <EditModal user={modal.user} onDone={() => { load(); setModal(null); }} onError={setError} error={error} />
          )}
          {modal.type === "pin" && (
            <PinModal user={modal.user} onDone={() => setModal(null)} onError={setError} error={error} />
          )}
          {modal.type === "delete" && (
            <DeleteModal user={modal.user} onDone={() => { load(); setModal(null); }} onError={setError} error={error} />
          )}
          {modal.type === "vpn" && (
            <VpnModal user={modal.user} onDone={() => setModal(null)} onError={setError} error={error} />
          )}
          {modal.type === "voice" && (
            <VoiceModal user={modal.user} onDone={() => setModal(null)} onError={setError} error={error} />
          )}
        </div>
      )}
    </div>
  );
}

// ── Create ────────────────────────────────────────────────────────────────────

function CreateModal({ onDone, onError, error }: { onDone: () => void; onError: (e: string) => void; error: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [f, setF] = useState({ username: "", display_name: "", role: "adult" as Role, pin: "", avatar: "👤" });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiCreateUser(f);
      onDone();
    } catch (err: any) {
      onError(err?.response?.data?.detail ?? t("users.create.error_generic"));
    }
  };

  return (
    <div style={{ ...S.modal, width: isMobile ? "calc(100vw - 32px)" : 420, maxHeight: "90vh", overflowY: "auto" }}>
      <div style={S.mTitle}>{t("users.create.title")}</div>
      <form onSubmit={submit}>
        <label style={S.label}>{t("users.create.display_name")}</label>
        <input style={S.input} value={f.display_name} onChange={e => setF({ ...f, display_name: e.target.value })} required />
        <label style={S.label}>{t("users.create.username")}</label>
        <input style={S.input} value={f.username} onChange={e => setF({ ...f, username: e.target.value })} required />
        <label style={S.label}>{t("users.create.role")}</label>
        <select style={S.select} value={f.role} onChange={e => setF({ ...f, role: e.target.value as Role })}>
          {ROLES.map(r => <option key={r} value={r}>{t(`dashboard.roles.${r}`, r)}</option>)}
        </select>
        <label style={S.label}>{t("users.create.avatar")}</label>
        <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
          {AVATARS.map(a => (
            <span key={a} onClick={() => setF({ ...f, avatar: a })}
              style={{ fontSize: 24, cursor: "pointer", opacity: f.avatar === a ? 1 : 0.4 }}>{a}</span>
          ))}
        </div>
        <label style={S.label}>{t("users.create.pin")}</label>
        <input style={S.input} type="password" inputMode="numeric" value={f.pin}
          onChange={e => setF({ ...f, pin: e.target.value })} required />
        {error && <div style={S.err}>{error}</div>}
        <div style={S.row}>
          <button type="submit" style={S.btn()}>{t("users.create.submit")}</button>
          <button type="button" style={S.btn("#333")} onClick={onDone}>{t("users.create.cancel")}</button>
        </div>
      </form>
    </div>
  );
}

// ── Edit ──────────────────────────────────────────────────────────────────────

function EditModal({ user, onDone, onError, error }: { user: KaareUser; onDone: () => void; onError: (e: string) => void; error: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [f, setF] = useState({
    display_name: user.display_name,
    role: user.role,
    avatar: user.avatar,
    is_active: user.is_active,
    personality: user.personality || "standard",
    vpn_access: (user.vpn_access || "local_only") as VpnAccess,
    can_manage_child_timers: user.can_manage_child_timers ?? false,
  });
  const [personalities, setPersonalities] = useState<{ key: string; label: string }[]>([]);

  useEffect(() => {
    apiListPersonalities().then(setPersonalities).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiUpdateUser(user.username, f);
      onDone();
    } catch (err: any) {
      onError(err?.response?.data?.detail ?? t("users.edit.error_generic"));
    }
  };

  return (
    <div style={{ ...S.modal, width: isMobile ? "calc(100vw - 32px)" : 420, maxHeight: "90vh", overflowY: "auto" }}>
      <div style={S.mTitle}>{t("users.edit.title", { name: user.display_name })}</div>
      <form onSubmit={submit}>
        <label style={S.label}>{t("users.edit.display_name")}</label>
        <input style={S.input} value={f.display_name} onChange={e => setF({ ...f, display_name: e.target.value })} required />
        <label style={S.label}>{t("users.edit.role")}</label>
        <select style={S.select} value={f.role} onChange={e => setF({ ...f, role: e.target.value as Role })}>
          {ROLES.map(r => <option key={r} value={r}>{t(`dashboard.roles.${r}`, r)}</option>)}
        </select>
        <label style={S.label}>{t("users.edit.personality")}</label>
        <select style={S.select} value={f.personality} onChange={e => setF({ ...f, personality: e.target.value })}>
          {personalities.map(p => <option key={p.key} value={p.key}>{t(`personality_variants.${p.key}`, p.key)}</option>)}
        </select>
        <label style={S.label}>{t("users.edit.avatar")}</label>
        <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
          {AVATARS.map(a => (
            <span key={a} onClick={() => setF({ ...f, avatar: a })}
              style={{ fontSize: 24, cursor: "pointer", opacity: f.avatar === a ? 1 : 0.4 }}>{a}</span>
          ))}
        </div>
        <label style={S.label}>{t("users.edit.vpn_label")}</label>
        <select style={S.select} value={f.vpn_access} onChange={e => setF({ ...f, vpn_access: e.target.value as VpnAccess })}>
          <option value="local_only">{t("users.edit.vpn_local_only")}</option>
          <option value="ai_only">{t("users.edit.vpn_ai_only")}</option>
          <option value="full_access">{t("users.edit.vpn_full_access")}</option>
        </select>
        <label style={S.label}>
          <input type="checkbox" checked={f.is_active} onChange={e => setF({ ...f, is_active: e.target.checked })} />
          {" "}{t("users.edit.active_label")}
        </label>

        {/* Parental control toggle — only for adult+ roles */}
        {(f.role === "adult" || f.role === "young_adult" || f.role === "admin") && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ color: "#aaa", fontSize: 13, marginBottom: 6 }}>
              {t("users.edit.can_manage_child_timers")}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div
                role="switch"
                aria-checked={f.can_manage_child_timers}
                onClick={() => setF({ ...f, can_manage_child_timers: !f.can_manage_child_timers })}
                style={{
                  display: "inline-block", width: 44, height: 24,
                  background: f.can_manage_child_timers ? "#4caf50" : "#333",
                  borderRadius: 12, cursor: "pointer",
                  position: "relative", transition: "background 0.2s", flexShrink: 0,
                }}
              >
                <div style={{
                  position: "absolute", top: 3,
                  left: f.can_manage_child_timers ? 23 : 3,
                  width: 18, height: 18,
                  background: "#fff", borderRadius: "50%",
                  transition: "left 0.2s",
                }} />
              </div>
              <span style={{ color: "#888", fontSize: 12 }}>
                {t("users.edit.can_manage_child_timers_desc")}
              </span>
            </div>
          </div>
        )}

        {error && <div style={S.err}>{error}</div>}
        <div style={S.row}>
          <button type="submit" style={S.btn()}>{t("users.edit.save")}</button>
          <button type="button" style={S.btn("#333")} onClick={onDone}>{t("users.edit.cancel")}</button>
        </div>
      </form>
    </div>
  );
}

// ── PIN ───────────────────────────────────────────────────────────────────────

function PinModal({ user, onDone, onError, error }: { user: KaareUser; onDone: () => void; onError: (e: string) => void; error: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [pin, setPin] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiUpdatePin(user.username, pin);
      onDone();
    } catch (err: any) {
      onError(err?.response?.data?.detail ?? t("users.pin_modal.error_generic"));
    }
  };

  return (
    <div style={{ ...S.modal, width: isMobile ? "calc(100vw - 32px)" : 420, maxHeight: "90vh", overflowY: "auto" }}>
      <div style={S.mTitle}>{t("users.pin_modal.title", { name: user.display_name })}</div>
      <form onSubmit={submit}>
        <label style={S.label}>{t("users.pin_modal.new_pin")}</label>
        <input style={S.input} type="password" inputMode="numeric" value={pin}
          onChange={e => setPin(e.target.value)} autoFocus required />
        {error && <div style={S.err}>{error}</div>}
        <div style={S.row}>
          <button type="submit" style={S.btn("#2a6a2a")}>{t("users.pin_modal.submit")}</button>
          <button type="button" style={S.btn("#333")} onClick={onDone}>{t("users.pin_modal.cancel")}</button>
        </div>
      </form>
    </div>
  );
}

// ── VPN ───────────────────────────────────────────────────────────────────────

function VpnModal({ user, onDone, onError, error }: { user: KaareUser; onDone: () => void; onError: (e: string) => void; error: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [clients, setClients] = useState<VpnClient[]>([]);
  const [deviceName, setDeviceName] = useState("");
  const [generatedConfig, setGeneratedConfig] = useState<string | null>(null);
  const [generatedName, setGeneratedName] = useState<string>("");
  const [loading, setLoading] = useState(false);

  const loadClients = () => {
    apiVpnListClients()
      .then(all => setClients(all.filter(c => c.username === user.username)))
      .catch(() => {});
  };

  useEffect(() => { loadClients(); }, []);

  const generate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!deviceName.trim()) return;
    setLoading(true);
    onError("");
    try {
      const result = await apiVpnCreateClient(user.username, deviceName.trim());
      setGeneratedConfig(result.config);
      setGeneratedName(result.name);
      setDeviceName("");
      loadClients();
    } catch (err: any) {
      onError(err?.response?.data?.detail ?? t("users.vpn_modal.error_generic"));
    } finally {
      setLoading(false);
    }
  };

  const remove = async (clientName: string) => {
    onError("");
    try {
      await apiVpnDeleteClient(clientName);
      if (generatedName === clientName) setGeneratedConfig(null);
      loadClients();
    } catch (err: any) {
      onError(err?.response?.data?.detail ?? t("users.vpn_modal.error_remove"));
    }
  };

  return (
    <div style={{ ...S.modal, width: isMobile ? "calc(100vw - 32px)" : 480, maxHeight: "90vh", overflowY: "auto" }}>
      <div style={S.mTitle}>{t("users.vpn_modal.title", { name: user.display_name })}</div>

      {clients.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ color: "#888", fontSize: 12, marginBottom: 8 }}>{t("users.vpn_modal.active_profiles")}</div>
          {clients.map(c => (
            <div key={c.name} style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              background: "#111", borderRadius: 6, padding: "8px 12px", marginBottom: 6,
            }}>
              <div>
                <span style={{ color: "#ddd", fontSize: 13 }}>{c.device_name}</span>
                <span style={{ color: "#555", fontSize: 11, marginLeft: 8 }}>{c.ip}</span>
              </div>
              <button style={{ ...S.btn("#3a1a1a"), padding: "4px 10px", fontSize: 12 }}
                onClick={() => remove(c.name)}>{t("users.vpn_modal.delete")}</button>
            </div>
          ))}
        </div>
      )}

      <form onSubmit={generate}>
        <label style={S.label}>{t("users.vpn_modal.new_device_label")}</label>
        <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: isMobile ? "wrap" : "nowrap" }}>
          <input
            style={{ ...S.input, flex: 1, marginBottom: 0 }}
            value={deviceName}
            onChange={e => setDeviceName(e.target.value)}
            placeholder={t("users.vpn_modal.new_device_ph")}
            disabled={loading}
          />
          <button type="submit" style={{ ...S.btn("#1a4a3a"), whiteSpace: "nowrap", ...(isMobile ? { width: "100%", marginTop: 8 } : {}) }} disabled={loading}>
            {loading ? t("users.vpn_modal.generating") : t("users.vpn_modal.generate_qr")}
          </button>
        </div>
      </form>

      {error && <div style={S.err}>{error}</div>}

      {generatedConfig && (
        <div style={{ marginTop: 8 }}>
          <div style={{ color: "#aaa", fontSize: 12, marginBottom: 10 }}>
            {t("users.vpn_modal.scan_hint")}
          </div>
          <div style={{
            background: "#fff", padding: 16, borderRadius: 8,
            display: "inline-block",
          }}>
            <QRCode value={generatedConfig} size={isMobile ? 200 : 220} />
          </div>
          <div style={{ marginTop: 12 }}>
            <details>
              <summary style={{ color: "#666", fontSize: 12, cursor: "pointer" }}>{t("users.vpn_modal.show_config")}</summary>
              <pre style={{
                background: "#0a0a0a", color: "#aaa", fontSize: 11, padding: 12,
                borderRadius: 6, marginTop: 8, overflowX: "auto", whiteSpace: "pre-wrap",
              }}>{generatedConfig}</pre>
            </details>
          </div>
        </div>
      )}

      <div style={{ ...S.row, marginTop: 16 }}>
        <button style={S.btn("#333")} onClick={onDone}>{t("users.vpn_modal.close")}</button>
      </div>
    </div>
  );
}

// ── Voice enrollment ──────────────────────────────────────────────────────────

function downsampleBuffer(buffer: AudioBuffer, targetRate: number): Float32Array {
  const input = buffer.getChannelData(0);
  const ratio = buffer.sampleRate / targetRate;
  const outputLen = Math.floor(input.length / ratio);
  const output = new Float32Array(outputLen);
  for (let i = 0; i < outputLen; i++) {
    output[i] = input[Math.floor(i * ratio)];
  }
  return output;
}

function encodeWAV(samples: Float32Array, sampleRate: number): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buf);
  const str = (off: number, s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  str(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true);
  str(8, "WAVE"); str(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); str(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    off += 2;
  }
  return new Blob([buf], { type: "audio/wav" });
}

type RecordState = "idle" | "recording" | "processing" | "done" | "error";
const MAX_RECORD_SEC = 15;

function VoiceModal({ user, onDone, onError, error }: { user: KaareUser; onDone: () => void; onError: (e: string) => void; error: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [hasVoiceprint, setHasVoiceprint] = useState<boolean | null>(null);
  const [recordState, setRecordState] = useState<RecordState>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [successMsg, setSuccessMsg] = useState("");

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef   = useRef<Blob[]>([]);
  const timerRef    = useRef<ReturnType<typeof setInterval> | null>(null);
  const streamRef   = useRef<MediaStream | null>(null);

  const loadStatus = () => {
    apiVoiceStatus(user.username).then(r => setHasVoiceprint(r.has_voiceprint)).catch(() => setHasVoiceprint(false));
  };

  useEffect(() => {
    loadStatus();
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      streamRef.current?.getTracks().forEach(tk => tk.stop());
    };
  }, [user.username]);

  const startRecording = async () => {
    onError("");
    setSuccessMsg("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = e => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      recorder.onstop = handleStop;
      recorder.start();
      setElapsed(0);
      setRecordState("recording");

      timerRef.current = setInterval(() => {
        setElapsed(prev => {
          if (prev + 1 >= MAX_RECORD_SEC) { stopRecording(); return prev; }
          return prev + 1;
        });
      }, 1000);
    } catch {
      onError(t("users.voice_modal.error_mic"));
    }
  };

  const stopRecording = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    streamRef.current?.getTracks().forEach(tk => tk.stop());
  };

  const handleStop = async () => {
    setRecordState("processing");
    try {
      const blob = new Blob(chunksRef.current, { type: "audio/webm" });
      const arrayBuffer = await blob.arrayBuffer();
      const audioCtx = new AudioContext();
      const decoded = await audioCtx.decodeAudioData(arrayBuffer);
      const pcm = downsampleBuffer(decoded, 16000);
      const wav = encodeWAV(pcm, 16000);
      await apiVoiceEnroll(user.username, wav);
      setHasVoiceprint(true);
      setRecordState("done");
      setSuccessMsg(t("users.voice_modal.registered"));
    } catch {
      setRecordState("error");
      onError(t("users.voice_modal.error_record"));
    }
  };

  const handleDelete = async () => {
    onError("");
    try {
      await apiVoiceDelete(user.username);
      setHasVoiceprint(false);
      setRecordState("idle");
      setSuccessMsg("");
    } catch {
      onError(t("users.voice_modal.error_delete"));
    }
  };

  const progress = Math.round((elapsed / MAX_RECORD_SEC) * 100);

  return (
    <div style={{ ...S.modal, width: isMobile ? "calc(100vw - 32px)" : 460, maxHeight: "90vh", overflowY: "auto" }}>
      <div style={S.mTitle}>{t("users.voice_modal.title", { name: user.display_name })}</div>

      <div style={{ marginBottom: 20 }}>
        <div style={{ color: "#888", fontSize: 12, marginBottom: 6 }}>{t("users.voice_modal.status")}</div>
        {hasVoiceprint === null ? (
          <span style={{ color: "#666", fontSize: 13 }}>{t("users.voice_modal.checking")}</span>
        ) : hasVoiceprint ? (
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ color: "#4caf50", fontSize: 13 }}>{t("users.voice_modal.enrolled")}</span>
            <button style={{ ...S.btn("#3a1a1a"), padding: "4px 10px", fontSize: 12 }} onClick={handleDelete}>
              {t("users.voice_modal.delete")}
            </button>
          </div>
        ) : (
          <span style={{ color: "#888", fontSize: 13 }}>{t("users.voice_modal.not_enrolled")}</span>
        )}
      </div>

      <div style={{ background: "#111", borderRadius: 8, padding: "12px 14px", marginBottom: 18 }}>
        <div style={{ color: "#aaa", fontSize: 12, marginBottom: 6 }}>{t("users.voice_modal.instructions_title")}</div>
        <ol style={{ color: "#888", fontSize: 12, margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
          <li>{t("users.voice_modal.instruction_1")}</li>
          <li>{t("users.voice_modal.instruction_2")}</li>
          <li>{t("users.voice_modal.instruction_3")}</li>
        </ol>
        <div style={{ marginTop: 10, color: "#666", fontSize: 12, fontStyle: "italic", lineHeight: 1.7 }}>
          "Hei Kåre, kan du slå på lyset i stua?"<br />
          "Hva er temperaturen ute akkurat nå?"<br />
          "Sett på litt musikk, jeg vil ha noe rolig."
        </div>
      </div>

      {recordState === "idle" || recordState === "done" || recordState === "error" ? (
        <button
          style={{ ...S.btn("#1a4a3a"), ...(isMobile ? { width: "100%", padding: "14px 18px", fontSize: 16 } : {}) }}
          onClick={startRecording}
        >
          {hasVoiceprint ? t("users.voice_modal.re_record") : t("users.voice_modal.start")}
        </button>
      ) : recordState === "recording" ? (
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
            <span style={{ color: "#ff4444", fontSize: 13 }}>
              {t("users.voice_modal.recording", { elapsed, max: MAX_RECORD_SEC })}
            </span>
            <button style={{ ...S.btn("#555"), padding: "6px 14px", fontSize: 13 }} onClick={stopRecording}>
              {t("users.voice_modal.stop")}
            </button>
          </div>
          <div style={{ background: "#222", borderRadius: 4, height: 6, overflow: "hidden" }}>
            <div style={{ background: "#ff4444", width: `${progress}%`, height: "100%", transition: "width 1s linear" }} />
          </div>
        </div>
      ) : (
        <div style={{ color: "#888", fontSize: 13 }}>{t("users.voice_modal.processing")}</div>
      )}

      {successMsg && <div style={{ color: "#4caf50", fontSize: 13, marginTop: 10 }}>{successMsg}</div>}
      {error && <div style={S.err}>{error}</div>}

      <div style={{ ...S.row, marginTop: 20 }}>
        <button style={S.btn("#333")} onClick={onDone}>{t("users.voice_modal.close")}</button>
      </div>
    </div>
  );
}

// ── Delete ────────────────────────────────────────────────────────────────────

function DeleteModal({ user, onDone, onError, error }: { user: KaareUser; onDone: () => void; onError: (e: string) => void; error: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  const submit = async () => {
    try {
      await apiDeleteUser(user.username);
      onDone();
    } catch (err: any) {
      onError(err?.response?.data?.detail ?? t("users.delete_modal.error_generic"));
    }
  };

  return (
    <div style={{ ...S.modal, width: isMobile ? "calc(100vw - 32px)" : 420, maxHeight: "90vh", overflowY: "auto" }}>
      <div style={S.mTitle}>{t("users.delete_modal.title")}</div>
      <p style={{ color: "#ccc" }}>{t("users.delete_modal.confirm", { name: user.display_name })}</p>
      {error && <div style={S.err}>{error}</div>}
      <div style={S.row}>
        <button style={S.btn("#8a1a1a")} onClick={submit}>{t("users.delete_modal.submit")}</button>
        <button style={S.btn("#333")} onClick={onDone}>{t("users.delete_modal.cancel")}</button>
      </div>
    </div>
  );
}
