import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ExpertOnly } from "./settings/shared";
import { API_BASE, apiGetEpisodeFramesUsage, apiGetFrigateUserPipe, apiPutFrigateUserPipe } from "@/services/api";
import { ChevronDown } from "lucide-react";
import StorageBar from "@/components/StorageBar";

type LabelConfig = {
  analyze: boolean;
  min_confidence: number;
  min_duration_seconds?: number;
};

type CameraConfig = {
  _id: string;
  display_name: string;
  role: string;
  labels: Record<string, LabelConfig>;
  frigate_type?: string | null;  // read-only, from Frigate's config (e.g. "lpr")
  episodes?: boolean;  // «Kan lage episoder» (P144-s14); missing = the role's default
  episodes_default?: boolean;  // read-only, from the API (road-facing off, others on) — never saved
};

type Role = {
  display_name: string;
};

type StorageConfig = {
  snapshots_max_mb?: number;
};

type AwayModeConfig = {
  nighttime_start: number;
  nighttime_end: number;
  person_confidence_high: number;
  unknown_night_high: boolean;
};

type CamerasData = {
  enabled: boolean;
  cameras: CameraConfig[];
  roles: Record<string, Role>;
  available_labels: string[];
  storage: StorageConfig;
  away_mode: AwayModeConfig;
};

const authHeaders = () => ({
  Authorization: `Bearer ${sessionStorage.getItem("kaare_token")}`,
  "Content-Type": "application/json",
});

const PREDEFINED_ROLES = ["front_door", "driveway", "road_facing", "garden", "garage", "indoor"];
const CUSTOM_ROLE_KEYS = ["custom_1", "custom_2", "custom_3"];

export default function Cameras() {
  const { t } = useTranslation();
  const [data, setData] = useState<CamerasData | null>(null);
  const [cameras, setCameras] = useState<CameraConfig[]>([]);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const [snapshotsMb, setSnapshotsMb] = useState<number>(500);
  const [storageSaving, setStorageSaving] = useState(false);
  const [storageSaved, setStorageSaved] = useState(false);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [usageSnap, setUsageSnap] = useState<number>(0);
  // P144-s11: episode frames (configs/frigate_user.yaml via the Frigate user's API)
  const [framesMb, setFramesMb] = useState<number>(500);
  const [keepMb, setKeepMb] = useState<number>(500);
  const [dbMb, setDbMb] = useState<number>(500);          // P150: frigate.db size limit (0 = off)
  const [usageDb, setUsageDb] = useState<number>(0);
  const [usageFrames, setUsageFrames] = useState<number>(0);
  const [usageKeep, setUsageKeep] = useState<number>(0);

  const [awayMode, setAwayMode] = useState<AwayModeConfig>({
    nighttime_start: 22, nighttime_end: 6,
    person_confidence_high: 0.6, unknown_night_high: true,
  });

  useEffect(() => {
    fetch(`${API_BASE}/api/settings/cameras`, { headers: authHeaders() })
      .then(r => {
        if (r.status === 401) throw new Error("Ikke innlogget. Logg ut og inn igjen.");
        if (!r.ok) throw new Error(`Serverfeil: ${r.status}`);
        return r.json();
      })
      .then((d: CamerasData) => {
        setData(d);
        setCameras(d.cameras ?? []);
        setSnapshotsMb(d.storage?.snapshots_max_mb ?? 500);
        if (d.away_mode) setAwayMode(d.away_mode);
      })
      .catch((e: Error) => setError(e.message || t("cameras.error_load")));

    fetch(`${API_BASE}/api/settings/cameras/_storage_usage`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setUsageSnap(d.snapshots_mb); })
      .catch(() => {});

    apiGetFrigateUserPipe()
      .then(p => {
        setFramesMb(p.episode_frames_max_mb); setKeepMb(p.episode_frames_keep_mb);
        if (p.frigate_db_max_mb !== undefined) setDbMb(p.frigate_db_max_mb);
      })
      .catch(() => {});
    apiGetEpisodeFramesUsage()
      .then(u => { setUsageFrames(u.full_mb); setUsageKeep(u.keep_mb); setUsageDb(u.db_mb ?? 0); })
      .catch(() => {});
  }, [t]);

  const updateCamera = (camId: string, updated: CameraConfig) => {
    setCameras(prev => prev.map(c => c._id === camId ? updated : c));
  };

  const saveCamera = async (cam: CameraConfig) => {
    setSaving(prev => ({ ...prev, [cam._id]: true }));
    setError(null);
    try {
      const { _id, ...rest } = cam;
      // frigate_type (from Frigate) and episodes_default (from the role) are read-only — never saved
      const payload = Object.fromEntries(
        Object.entries(rest).filter(([k]) => k !== "frigate_type" && k !== "episodes_default"));
      await fetch(`${API_BASE}/api/settings/cameras/${_id}`, {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify(payload),
      });
      setSaved(prev => ({ ...prev, [cam._id]: true }));
      setTimeout(() => setSaved(prev => ({ ...prev, [cam._id]: false })), 2000);
    } catch {
      setError(t("cameras.error_save", { id: cam._id }));
    } finally {
      setSaving(prev => ({ ...prev, [cam._id]: false }));
    }
  };

  const saveStorage = async () => {
    const snapVal = snapshotsMb;
    if ((snapVal !== 0 && (snapVal < 100 || snapVal > 10000)) ||
        framesMb < 0 || framesMb > 100000 || keepMb < 0 || keepMb > 100000 || dbMb < 0 || dbMb > 1000000) {
      setStorageError(t("cameras.storage_error_invalid"));
      return;
    }
    setStorageSaving(true);
    setStorageError(null);
    try {
      const r = await fetch(`${API_BASE}/api/settings/cameras/_storage`, {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({ snapshots_max_mb: snapVal }),
      });
      if (!r.ok) throw new Error(`${r.status}`);
      await apiPutFrigateUserPipe({ episode_frames_max_mb: framesMb, episode_frames_keep_mb: keepMb, frigate_db_max_mb: dbMb });
      setStorageSaved(true);
      setTimeout(() => setStorageSaved(false), 2000);
    } catch (e: unknown) {
      setStorageError(t("cameras.error_save_global", { msg: e instanceof Error ? e.message : e }));
    } finally {
      setStorageSaving(false);
    }
  };

  const roleLabel = (roleKey: string) => {
    if (!data) return roleKey;
    const r = data.roles[roleKey];
    if (r?.display_name) return r.display_name;
    const key = `cameras.roles.${roleKey}`;
    const translated = t(key);
    if (translated !== key) return translated;
    return roleKey;
  };

  const allRoleOptions = data
    ? [
        ...PREDEFINED_ROLES.map(k => ({ key: k, label: roleLabel(k) })),
        ...CUSTOM_ROLE_KEYS.map(k => ({
          key: k,
          label: data.roles[k]?.display_name || `${t("cameras.roles.custom_prefix")} (${k})`,
        })),
      ]
    : [];

  if (error) {
    return <div style={{ padding: "16px 0", color: "#f87171" }}>{error}</div>;
  }
  if (!data) {
    return <div style={{ padding: "16px 0", color: "#666" }}>{t("cameras.loading")}</div>;
  }

  return (
    <div>
      {/* Header: card title (P144 phase 6: the old analysis switch is gone — the module panel governs the pipe) */}
      <div style={{ marginBottom: 18 }}>
        <div style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("cameras.title")}</div>
        <div style={{ color: "#666", fontSize: 13, marginTop: 4 }}>{t("cameras.description")}</div>
      </div>

      {/* Camera cards — folded lines first: what gets adjusted most */}
      <ExpertOnly><div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 460px), 1fr))",
        gap: 14,
        alignItems: "start",  // a folded card stays one line next to an open one
        marginBottom: 20,
      }}>
        {cameras.map(cam => (
          <CameraCard
            key={cam._id}
            cam={cam}
            roles={allRoleOptions}
            availableLabels={data.available_labels}
            saving={saving[cam._id] || false}
            savedOk={saved[cam._id] || false}
            onChange={updated => updateCamera(cam._id, updated)}
            onSave={() => saveCamera(cam)}
          />
        ))}
      </div></ExpertOnly>

      {/* Set-once settings side by side: storage | away mode (stack on narrow screens) */}
      <ExpertOnly><div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 420px), 1fr))", gap: 14,
      }}>

      {/* Storage settings */}
      <div style={{
        background: "#111", border: "1px solid #222", borderRadius: 10,
        padding: "16px 20px",
      }}>
        <div style={{ color: "#aaa", fontWeight: 600, fontSize: 13, marginBottom: 14 }}>
          {t("cameras.storage_title")}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <label style={{ color: "#888", fontSize: 13, minWidth: 160 }}>
              {t("cameras.storage_snapshot")}
            </label>
            <input
              type="number" min={0} max={10000} step={100}
              value={snapshotsMb}
              onChange={e => setSnapshotsMb(parseInt(e.target.value) || 0)}
              style={{
                width: 90, background: "#1a1a1a", border: "1px solid #333",
                borderRadius: 5, color: "#ccc", padding: "5px 8px", fontSize: 13,
              }}
            />
            <span style={{ color: "#555", fontSize: 12 }}>{t("cameras.storage_snapshot_hint")}</span>
          </div>
          {([
            ["frames", framesMb, setFramesMb],
            ["keep", keepMb, setKeepMb],
            ["db", dbMb, setDbMb],
          ] as const).map(([key, value, set]) => (
            <div key={key} style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <label style={{ color: "#888", fontSize: 13, minWidth: 160 }}>
                {t(`integrations.frigate_episodes.storage_${key}`)}
              </label>
              <input
                type="number" min={0} max={100000} step={100}
                value={value}
                onChange={e => set(parseInt(e.target.value) || 0)}
                style={{
                  width: 90, background: "#1a1a1a", border: "1px solid #333",
                  borderRadius: 5, color: "#ccc", padding: "5px 8px", fontSize: 13,
                }}
              />
              <span style={{ color: "#555", fontSize: 12 }}>{t(`integrations.frigate_episodes.storage_${key}_hint`)}</span>
            </div>
          ))}
          {storageError && (
            <div style={{ color: "#f87171", fontSize: 12 }}>{storageError}</div>
          )}
          <div>
            <button
              onClick={saveStorage}
              disabled={storageSaving}
              style={{
                background: storageSaved ? "#16a34a" : "#6366f1",
                border: "none", borderRadius: 7, color: "#fff",
                padding: "7px 20px", fontSize: 13, fontWeight: 600,
                cursor: storageSaving ? "not-allowed" : "pointer",
                opacity: storageSaving ? 0.7 : 1,
                transition: "background 0.2s",
              }}
            >
              {storageSaving ? t("cameras.storage_saving") : storageSaved ? t("cameras.storage_saved") : t("cameras.storage_save")}
            </button>
          </div>

          {/* Storage usage bars */}
          <div style={{ borderTop: "1px solid #1e1e1e", marginTop: 14, paddingTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
            <StorageBar label={t("cameras.storage_snapshot")} usedMb={usageSnap} limitMb={snapshotsMb} />
            <StorageBar label={t("integrations.frigate_episodes.storage_frames")} usedMb={usageFrames} limitMb={framesMb} />
            <StorageBar label={t("integrations.frigate_episodes.storage_keep")} usedMb={usageKeep} limitMb={keepMb} />
            <StorageBar label={t("integrations.frigate_episodes.storage_db")} usedMb={usageDb} limitMb={dbMb} />
          </div>
        </div>
      </div>

      <AwayModePanel awayMode={awayMode} onChange={setAwayMode} />
      </div></ExpertOnly>
    </div>
  );
}

function AwayModePanel({ awayMode, onChange }: { awayMode: AwayModeConfig; onChange: (v: AwayModeConfig) => void }) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setSaving(true); setError(null);
    try {
      const r = await fetch(`${API_BASE}/api/settings/cameras/_away_mode`, {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify(awayMode),
      });
      if (!r.ok) throw new Error(`${r.status}`);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const row = (label: string, hint: string, children: React.ReactNode) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <label style={{ color: "#888", fontSize: 13, minWidth: 260 }}>{label}</label>
        {children}
      </div>
      <span style={{ color: "#444", fontSize: 11, paddingLeft: 2 }}>{hint}</span>
    </div>
  );

  return (
    <div style={{
      background: "#111", border: "1px solid #222", borderRadius: 10,
      padding: "16px 20px",
    }}>
      <div style={{ color: "#aaa", fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
        {t("cameras.away_mode_title")}
      </div>
      <div style={{ color: "#555", fontSize: 12, marginBottom: 14 }}>{t("cameras.away_mode_hint")}</div>

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {row(t("cameras.away_mode_nighttime_start"), "", (
          <input type="number" min={0} max={23}
            value={awayMode.nighttime_start}
            onChange={e => onChange({ ...awayMode, nighttime_start: parseInt(e.target.value) || 0 })}
            style={{ width: 60, background: "#1a1a1a", border: "1px solid #333", borderRadius: 5, color: "#ccc", padding: "5px 8px", fontSize: 13 }}
          />
        ))}
        {row(t("cameras.away_mode_nighttime_end"), "", (
          <input type="number" min={0} max={23}
            value={awayMode.nighttime_end}
            onChange={e => onChange({ ...awayMode, nighttime_end: parseInt(e.target.value) || 0 })}
            style={{ width: 60, background: "#1a1a1a", border: "1px solid #333", borderRadius: 5, color: "#ccc", padding: "5px 8px", fontSize: 13 }}
          />
        ))}
        {row(t("cameras.away_mode_person_confidence"), t("cameras.away_mode_person_confidence_hint"), (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input type="range" min={0} max={100} step={5}
              value={Math.round(awayMode.person_confidence_high * 100)}
              onChange={e => onChange({ ...awayMode, person_confidence_high: parseInt(e.target.value) / 100 })}
              style={{ width: 100, accentColor: "#6366f1" }}
            />
            <span style={{ color: "#888", fontSize: 12, minWidth: 32 }}>
              {Math.round(awayMode.person_confidence_high * 100)}%
            </span>
          </div>
        ))}
        {row(t("cameras.away_mode_unknown_night"), t("cameras.away_mode_unknown_night_hint"), (
          <Toggle
            value={awayMode.unknown_night_high}
            onChange={v => onChange({ ...awayMode, unknown_night_high: v })}
          />
        ))}
      </div>

      <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={save} disabled={saving} style={{
          background: saved ? "#16a34a" : "#6366f1",
          border: "none", borderRadius: 7, color: "#fff",
          padding: "7px 20px", fontSize: 13, fontWeight: 600,
          cursor: saving ? "not-allowed" : "pointer",
          opacity: saving ? 0.7 : 1, transition: "background 0.2s",
        }}>
          {saving ? t("cameras.away_mode_saving") : saved ? t("cameras.away_mode_saved") : t("cameras.away_mode_save")}
        </button>
        {error && <span style={{ color: "#f87171", fontSize: 12 }}>{error}</span>}
      </div>
    </div>
  );
}


function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!value)}
      style={{
        width: 44, height: 24, borderRadius: 12, border: "none",
        background: value ? "#16a34a" : "#333",
        position: "relative", cursor: "pointer", flexShrink: 0,
        transition: "background 0.2s",
      }}
    >
      <span style={{
        display: "block", width: 18, height: 18, borderRadius: "50%",
        background: "#fff", position: "absolute",
        top: 3, left: value ? 23 : 3,
        transition: "left 0.2s",
      }} />
    </button>
  );
}

function CameraCard({
  cam, roles, availableLabels,
  saving, savedOk, onChange, onSave,
}: {
  cam: CameraConfig;
  roles: { key: string; label: string }[];
  availableLabels: string[];
  saving: boolean;
  savedOk: boolean;
  onChange: (updated: CameraConfig) => void;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  // Folded by default: one line per camera, the table opens with the arrow (2026-10-26).
  const [open, setOpen] = useState(false);
  const setRole = (role: string) => onChange({ ...cam, role });

  const setLabel = (label: string, field: keyof LabelConfig, val: boolean | number) => {
    const labels = { ...cam.labels };
    labels[label] = { ...(labels[label] || { analyze: false, min_confidence: 0.65 }), [field]: val };
    onChange({ ...cam, labels });
  };

  const allLabels = [...new Set([...availableLabels, ...Object.keys(cam.labels ?? {})])];

  const analyzed = allLabels.filter(l => cam.labels?.[l]?.analyze);
  const roleLabel = roles.find(r => r.key === cam.role)?.label ?? cam.role;

  const tableHeaders = [
    t("cameras.col_object"),
    t("cameras.col_analyze"),
    t("cameras.col_min_confidence"),
    t("cameras.col_min_duration"),
  ];

  return (
    <div style={{
      background: "#111", border: "1px solid #222", borderRadius: 10, overflow: "hidden",
    }}>
      {/* Header — always visible; click folds/unfolds (the role dropdown only shows when open) */}
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          padding: "12px 16px", borderBottom: open ? "1px solid #1a1a1a" : "none",
          display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", cursor: "pointer",
        }}
      >
        <div style={{ flex: 1, minWidth: 120 }}>
          <div style={{ color: "#fff", fontWeight: 600, fontSize: 13 }}>
            {cam.display_name || cam._id}
            {cam.frigate_type === "lpr" && (
              <span
                title={t("cameras.frigate_type_lpr_hint")}
                style={{
                  marginLeft: 8, padding: "1px 6px", borderRadius: 4, border: "1px solid #333",
                  color: "#aaa", fontSize: 10, fontWeight: 500,
                }}
              >
                {t("cameras.frigate_type_lpr")}
              </span>
            )}
          </div>
          <div style={{ color: "#555", fontSize: 11, marginTop: 2 }}>
            {open ? cam._id : (
              <>
                {roleLabel} · {analyzed.length
                  ? <span style={{ color: "#4ade80" }}>{t("cameras.summary_analyzes", { labels: analyzed.join(", ") })}</span>
                  : t("cameras.summary_none")}
              </>
            )}
          </div>
        </div>

        {/* Role dropdown */}
        {open && <div style={{ display: "flex", flexDirection: "column", gap: 3 }} onClick={e => e.stopPropagation()}>
          <label style={{ color: "#888", fontSize: 10 }}>{t("cameras.role_label")}</label>
          <select
            value={cam.role}
            onChange={e => setRole(e.target.value)}
            style={{
              background: "#1a1a1a", border: "1px solid #333", borderRadius: 6,
              color: "#ccc", padding: "4px 8px", fontSize: 12, cursor: "pointer",
            }}
          >
            {roles.map(r => (
              <option key={r.key} value={r.key}>{r.label}</option>
            ))}
          </select>
        </div>}

        <ChevronDown
          size={18}
          aria-label={open ? t("cameras.fold") : t("cameras.unfold")}
          style={{ color: "#888", flexShrink: 0, transition: "transform 0.15s", transform: open ? "rotate(180deg)" : "none" }}
        />
      </div>

      {open && <>
      {/* «Kan lage episoder» (P144-s14): this camera may start/keep a situation; the ticks below decide what */}
      <div style={{ padding: "10px 16px 0", display: "flex", alignItems: "center", gap: 10 }}>
        <Toggle value={cam.episodes ?? cam.episodes_default ?? true} onChange={v => onChange({ ...cam, episodes: v })} />
        <div style={{ minWidth: 0 }}>
          <div style={{ color: "#ccc", fontSize: 12 }}>{t("cameras.episodes_label")}</div>
          <div style={{ color: "#555", fontSize: 11 }}>{t("cameras.episodes_hint")}</div>
        </div>
      </div>
      {/* Label table */}
      <div style={{ padding: "0 16px 12px" }}>
        <div style={{ color: "#555", fontSize: 11, marginTop: 10 }}>{t("cameras.labels_hint")}</div>
        <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 10 }}>
          <thead>
            <tr>
              {tableHeaders.map(h => (
                <th key={h} style={{
                  textAlign: "left", color: "#555", fontSize: 10, fontWeight: 500,
                  padding: "3px 6px 6px 0", borderBottom: "1px solid #1e1e1e",
                  whiteSpace: "nowrap",
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {allLabels.map(lbl => {
              const lcfg: LabelConfig = cam.labels[lbl] || { analyze: false, min_confidence: 0.65 };
              return (
                <tr key={lbl} style={{ opacity: lcfg.analyze ? 1 : 0.5 }}>
                  <td style={{ padding: "6px 6px 6px 0", color: "#ccc", fontSize: 12, width: 90 }}>
                    {lbl}
                  </td>
                  <td style={{ padding: "6px 12px 6px 0" }}>
                    <Toggle value={lcfg.analyze} onChange={v => setLabel(lbl, "analyze", v)} />
                  </td>
                  <td style={{ padding: "6px 12px 6px 0" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <input
                        type="range" min={0} max={100} step={5}
                        value={Math.round((lcfg.min_confidence ?? 0.65) * 100)}
                        disabled={!lcfg.analyze}
                        onChange={e => setLabel(lbl, "min_confidence", parseInt(e.target.value) / 100)}
                        style={{ width: 64, accentColor: "#6366f1" }}
                      />
                      <span style={{ color: "#888", fontSize: 11, minWidth: 28 }}>
                        {Math.round((lcfg.min_confidence ?? 0.65) * 100)}%
                      </span>
                    </div>
                  </td>
                  <td style={{ padding: "6px 0" }}>
                    <input
                      type="number" min={0} max={300} step={5}
                      value={lcfg.min_duration_seconds ?? 0}
                      disabled={!lcfg.analyze}
                      onChange={e => setLabel(lbl, "min_duration_seconds", parseInt(e.target.value) || 0)}
                      style={{
                        width: 52, background: "#1a1a1a", border: "1px solid #333",
                        borderRadius: 5, color: "#ccc", padding: "3px 6px", fontSize: 12,
                      }}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Save button */}
      <div style={{
        padding: "10px 16px", borderTop: "1px solid #1a1a1a",
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <button
          onClick={onSave}
          disabled={saving}
          style={{
            background: savedOk ? "#16a34a" : "#6366f1",
            border: "none", borderRadius: 7, color: "#fff",
            padding: "6px 16px", fontSize: 12, fontWeight: 600,
            cursor: saving ? "not-allowed" : "pointer",
            opacity: saving ? 0.7 : 1,
            transition: "background 0.2s",
          }}
        >
          {saving ? t("cameras.saving") : savedOk ? t("cameras.saved") : t("cameras.save")}
        </button>
      </div>
      </>}
    </div>
  );
}
