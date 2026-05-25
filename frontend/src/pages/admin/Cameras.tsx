import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

type LabelConfig = {
  analyze: boolean;
  min_confidence: number;
  min_duration_seconds?: number;
  announce: boolean;
};

type CameraConfig = {
  _id: string;
  display_name: string;
  role: string;
  labels: Record<string, LabelConfig>;
};

type Role = {
  display_name: string;
};

type StorageConfig = {
  snapshots_max_mb?: number;
  log_max_mb?: number;
};

type CamerasData = {
  enabled: boolean;
  cameras: CameraConfig[];
  roles: Record<string, Role>;
  available_labels: string[];
  storage: StorageConfig;
};

const API = `http://${window.location.hostname}:8000`;
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
  const [globalEnabled, setGlobalEnabled] = useState(true);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const [snapshotsMb, setSnapshotsMb] = useState<number>(500);
  const [logMb, setLogMb] = useState<number>(50);
  const [storageSaving, setStorageSaving] = useState(false);
  const [storageSaved, setStorageSaved] = useState(false);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [usageSnap, setUsageSnap] = useState<number>(0);
  const [usageLog, setUsageLog] = useState<number>(0);

  useEffect(() => {
    fetch(`${API}/api/settings/cameras`, { headers: authHeaders() })
      .then(r => {
        if (r.status === 401) throw new Error("Ikke innlogget. Logg ut og inn igjen.");
        if (!r.ok) throw new Error(`Serverfeil: ${r.status}`);
        return r.json();
      })
      .then((d: CamerasData) => {
        setData(d);
        setGlobalEnabled(d.enabled ?? true);
        setCameras(d.cameras ?? []);
        setSnapshotsMb(d.storage?.snapshots_max_mb ?? 500);
        setLogMb(d.storage?.log_max_mb ?? 50);
      })
      .catch((e: Error) => setError(e.message || t("cameras.error_load")));

    fetch(`${API}/api/settings/cameras/_storage_usage`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) { setUsageSnap(d.snapshots_mb); setUsageLog(d.log_mb); } })
      .catch(() => {});
  }, []);

  const handleGlobalToggle = async (val: boolean) => {
    setGlobalEnabled(val);
    try {
      const r = await fetch(`${API}/api/settings/cameras/_global`, {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({ enabled: val }),
      });
      if (!r.ok) throw new Error(`${r.status}`);
    } catch (e: unknown) {
      setError(t("cameras.error_save_global", { msg: e instanceof Error ? e.message : e }));
    }
  };

  const updateCamera = (camId: string, updated: CameraConfig) => {
    setCameras(prev => prev.map(c => c._id === camId ? updated : c));
  };

  const saveCamera = async (cam: CameraConfig) => {
    setSaving(prev => ({ ...prev, [cam._id]: true }));
    setError(null);
    try {
      const { _id, ...payload } = cam;
      await fetch(`${API}/api/settings/cameras/${_id}`, {
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
    const logVal = logMb;
    if ((snapVal !== 0 && (snapVal < 100 || snapVal > 10000)) ||
        (logVal !== 0 && (logVal < 10 || logVal > 1000))) {
      setStorageError(t("cameras.storage_error_invalid"));
      return;
    }
    setStorageSaving(true);
    setStorageError(null);
    try {
      const r = await fetch(`${API}/api/settings/cameras/_storage`, {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({ snapshots_max_mb: snapVal, log_max_mb: logVal }),
      });
      if (!r.ok) throw new Error(`${r.status}`);
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
    <div style={{ marginTop: 8 }}>
      {/* Global toggle */}
      <div style={{
        display: "flex", alignItems: "center", gap: 14,
        background: "#111", border: "1px solid #222", borderRadius: 10,
        padding: "14px 20px", marginBottom: 14,
      }}>
        <Toggle value={globalEnabled} onChange={handleGlobalToggle} />
        <div>
          <div style={{ color: "#ddd", fontWeight: 600, fontSize: 14 }}>
            {t("cameras.global_title")}
          </div>
          <div style={{ color: "#666", fontSize: 12, marginTop: 2 }}>
            {t("cameras.global_off_hint")}
          </div>
        </div>
      </div>

      {/* Storage settings */}
      <div style={{
        background: "#111", border: "1px solid #222", borderRadius: 10,
        padding: "16px 20px", marginBottom: 20,
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
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <label style={{ color: "#888", fontSize: 13, minWidth: 160 }}>
              {t("cameras.storage_log")}
            </label>
            <input
              type="number" min={0} max={1000} step={10}
              value={logMb}
              onChange={e => setLogMb(parseInt(e.target.value) || 0)}
              style={{
                width: 90, background: "#1a1a1a", border: "1px solid #333",
                borderRadius: 5, color: "#ccc", padding: "5px 8px", fontSize: 13,
              }}
            />
            <span style={{ color: "#555", fontSize: 12 }}>{t("cameras.storage_log_hint")}</span>
          </div>
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
            <StorageBar label={t("cameras.storage_log")} usedMb={usageLog} limitMb={logMb} />
          </div>
        </div>
      </div>

      {/* Camera cards — 2-column grid */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(2, 1fr)",
        gap: 14,
        opacity: globalEnabled ? 1 : 0.4,
        pointerEvents: globalEnabled ? "auto" : "none",
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
      </div>
    </div>
  );
}

function StorageBar({ label, usedMb, limitMb }: { label: string; usedMb: number; limitMb: number }) {
  const pct = limitMb > 0 ? Math.min(100, Math.round((usedMb / limitMb) * 100)) : 0;
  const color = pct >= 90 ? "#f87171" : pct >= 70 ? "#fb923c" : "#6366f1";
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ color: "#888", fontSize: 12 }}>{label}</span>
        <span style={{ color: "#666", fontSize: 12 }}>
          {usedMb} / {limitMb > 0 ? limitMb : "∞"} MB
          {limitMb > 0 && <span style={{ marginLeft: 6, color: pct >= 70 ? color : "#555" }}>({pct}%)</span>}
        </span>
      </div>
      <div style={{ height: 6, background: "#1e1e1e", borderRadius: 4, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 4, transition: "width 0.3s" }} />
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
        background: value ? "#6366f1" : "#333",
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
  const setRole = (role: string) => onChange({ ...cam, role });

  const setLabel = (label: string, field: keyof LabelConfig, val: boolean | number) => {
    const labels = { ...cam.labels };
    labels[label] = { ...(labels[label] || { analyze: false, min_confidence: 0.65, announce: false }), [field]: val };
    onChange({ ...cam, labels });
  };

  const allLabels = [...new Set([...availableLabels, ...Object.keys(cam.labels ?? {})])];

  const tableHeaders = [
    t("cameras.col_object"),
    t("cameras.col_analyze"),
    t("cameras.col_min_confidence"),
    t("cameras.col_min_duration"),
    t("cameras.col_announce"),
  ];

  return (
    <div style={{
      background: "#111", border: "1px solid #222", borderRadius: 10, overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{
        padding: "12px 16px", borderBottom: "1px solid #1a1a1a",
        display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
      }}>
        <div style={{ flex: 1, minWidth: 120 }}>
          <div style={{ color: "#fff", fontWeight: 600, fontSize: 13 }}>
            {cam.display_name || cam._id}
          </div>
          <div style={{ color: "#555", fontSize: 11, marginTop: 2 }}>{cam._id}</div>
        </div>

        {/* Role dropdown */}
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
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
        </div>
      </div>

      {/* Label table */}
      <div style={{ padding: "0 16px 12px" }}>
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
              const lcfg: LabelConfig = cam.labels[lbl] || { analyze: false, min_confidence: 0.65, announce: false };
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
                  <td style={{ padding: "6px 12px 6px 0" }}>
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
                  <td style={{ padding: "6px 0" }}>
                    <Toggle value={lcfg.announce} onChange={v => setLabel(lbl, "announce", v)} />
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
    </div>
  );
}
