import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Loader2, RefreshCw, RotateCcw, ShieldCheck, Cpu, BookmarkPlus, Trash2, Download, Upload } from "lucide-react";
import axios from "axios";
import i18n from "@/i18n";
import { useAuth } from "@/auth/AuthContext";
import {
  apiAdminServices, apiRestartService, apiSettingsRollback,
  apiSaveConfigSnapshot, apiListConfigSnapshots, apiRestoreConfigSnapshot, apiDeleteConfigSnapshot,
  apiExportConfigSnapshot, apiImportConfigSnapshot, apiVerifyPin,
  type ServiceKey, type AdminServiceStatus, type ConfigSnapshot,
} from "@/services/api";

const BASE = `http://${window.location.hostname}:8000`;
const token = () => sessionStorage.getItem("kaare_token");

type ReloadResult = { reloaded: string[]; errors: string[]; aliases_count: number };

type HealthCheckResult = {
  ok: boolean;
  total_errors: number;
  timestamp?: string;
  imports?: { passed: number; failed: number; skipped: number; errors: { name: string; detail: string }[] };
  configs?: { passed: number; failed: number; errors: { name: string; detail: string }[] };
  services?: { passed: number; failed: number; skipped: boolean; results: { name: string; ok: boolean; detail: string }[] };
  error?: string;
};
type CompressStatus = {
  running: boolean; episodes: number; compressed: number;
  step: string; log: string[]; started_at: string | null;
  finished_at: string | null; error: string | null; last_episode_ts: string | null;
};

function formatTs(iso: string | null, neverLabel: string) {
  if (!iso) return neverLabel;
  try {
    const locale = i18n.language === "nb" ? "nb-NO" : i18n.language;
    return new Date(iso).toLocaleString(locale, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

const SERVICE_KEYS = new Set([
  "kaare", "gateway", "semantic_embed", "agents", "embedding",
  "argus", "voice", "ha-log-bridge", "frontend",
]);


function ServiceRestartRow({ serviceKey, unit, active, onRestarted }: {
  serviceKey: string; unit: string; active: boolean; onRestarted: () => void;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<"idle" | "restarting" | "ok" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");

  const label = SERVICE_KEYS.has(serviceKey)
    ? t(`system.services.labels.${serviceKey}.label`, serviceKey)
    : serviceKey;
  const hint = SERVICE_KEYS.has(serviceKey)
    ? t(`system.services.labels.${serviceKey}.hint`, unit)
    : unit;

  const restart = async () => {
    setState("restarting");
    setErrorMsg("");
    try {
      const r = await apiRestartService(serviceKey as ServiceKey);
      if (r.ok) {
        setState("ok");
        setTimeout(() => { setState("idle"); onRestarted(); }, 3000);
      } else {
        setErrorMsg(r.error ?? t("system.services.error"));
        setState("error");
        setTimeout(() => setState("idle"), 5000);
      }
    } catch {
      setState("error");
      setTimeout(() => setState("idle"), 5000);
    }
  };

  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 0", borderBottom: "1px solid #1e1e1e", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
        <div style={{ width: 8, height: 8, borderRadius: "50%", flexShrink: 0, background: active ? "#4caf50" : "#333", boxShadow: active ? "0 0 5px #4caf5066" : "none" }} />
        <div style={{ minWidth: 0 }}>
          <p style={{ color: "#ccc", fontSize: 14, fontWeight: 500 }}>{label}</p>
          <p style={{ color: "#555", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{hint}</p>
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
        {state === "error" && <span style={{ color: "#f87171", fontSize: 12, maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{errorMsg || t("system.services.error")}</span>}
        {state === "ok"    && <span style={{ color: "#4caf50", fontSize: 12 }}>{t("system.services.restarted")}</span>}
        <Button
          variant="outline"
          size="sm"
          onClick={restart}
          disabled={state === "restarting"}
          className="gap-1.5"
        >
          {state === "restarting"
            ? <Loader2 className="h-3 w-3 animate-spin" />
            : <RotateCcw className="h-3 w-3" />}
          {state === "restarting" ? t("system.services.restarting") : t("system.services.restart")}
        </Button>
      </div>
    </div>
  );
}

type HardwareInfo = {
  detected_at: string;
  source: "host_script" | "container";
  platform: string;
  cpu: { model: string; cores: number };
  ram_gb: number;
  gpus: { type: string; id: number; name: string; vram_gb: number | null }[];
  npu: { detected: boolean; devices?: string[] };
};

function HardwareCard() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [hw, setHw] = useState<HardwareInfo | null>(null);

  const detect = useCallback(async (refresh = false) => {
    setStatus("loading");
    try {
      const r = await axios.get<HardwareInfo>(
        `${BASE}/api/system/hardware${refresh ? "?refresh=true" : ""}`,
        { headers: { Authorization: `Bearer ${token()}` } },
      );
      setHw(r.data);
      setStatus("done");
    } catch {
      setStatus("error");
    }
  }, []);

  useEffect(() => { detect(false); }, [detect]);

  const gpuLabel = (gpu: HardwareInfo["gpus"][0]) => {
    const vram = gpu.vram_gb != null ? ` (${t("system.hardware.vram", { gb: gpu.vram_gb })})` : "";
    return `${gpu.type.toUpperCase()} ${gpu.name}${vram}`;
  };

  const row = (label: string, value: string, accent?: boolean) => (
    <div style={{ display: "flex", gap: 8, fontSize: 12, lineHeight: 1.8 }}>
      <span style={{ color: "#555", minWidth: 44 }}>{label}</span>
      <span style={{ color: accent ? "#f59e0b" : "#999", wordBreak: "break-word" }}>{value}</span>
    </div>
  );

  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <Cpu size={15} style={{ color: "#888", flexShrink: 0 }} />
        <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.hardware.title")}</span>
      </div>
      <p style={{ color: "#666", fontSize: 13, marginBottom: 16 }}>{t("system.hardware.description")}</p>

      <Button
        onClick={() => detect(true)}
        disabled={status === "loading"}
        variant="outline"
        className="gap-2"
        style={{ borderColor: "#f59e0b", color: "#f59e0b" }}
      >
        {status === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Cpu className="h-4 w-4" />}
        {status === "loading" ? t("system.hardware.loading") : hw ? t("system.hardware.refresh") : t("system.hardware.button")}
      </Button>

      {status === "error" && (
        <p style={{ color: "#f87171", fontSize: 13, marginTop: 12 }}>{t("system.hardware.error")}</p>
      )}

      {hw && status !== "loading" && (
        <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 1 }}>
          {row(t("system.hardware.cpu"), `${hw.cpu.model} · ${t("system.hardware.cores", { n: hw.cpu.cores })}`)}
          {row(t("system.hardware.ram"), t("system.hardware.ram_gb", { gb: hw.ram_gb }))}
          {hw.gpus.length > 0
            ? hw.gpus.map((g, i) => row(i === 0 ? t("system.hardware.gpu") : "", gpuLabel(g), true))
            : row(t("system.hardware.gpu"), t("system.hardware.none_detected"))}
          {row(t("system.hardware.npu"), hw.npu.detected ? (hw.npu.devices ?? []).join(", ") : t("system.hardware.none_detected"))}
          <div style={{ marginTop: 6, fontSize: 11, color: "#444" }}>
            {t("system.hardware.detected_at")} {new Date(hw.detected_at).toLocaleString()} · {hw.source === "host_script" ? t("system.hardware.source_host") : t("system.hardware.source_container")}
          </div>
          {hw.gpus.length > 0 && hw.source !== "host_script" && (
            <div style={{ marginTop: 8, fontSize: 11, color: "#f59e0b", lineHeight: 1.5 }}>
              {t("system.hardware.note_gpu")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function HealthCheckCard() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<"idle" | "loading" | "done">("idle");
  const [result, setResult] = useState<HealthCheckResult | null>(null);

  const run = async () => {
    setStatus("loading");
    setResult(null);
    try {
      const r = await axios.get<HealthCheckResult>(`${BASE}/api/health_check`, {
        headers: { Authorization: `Bearer ${token()}` },
      });
      setResult(r.data);
    } catch {
      setResult({ ok: false, total_errors: 1, error: t("system.hot_reload.error_api") });
    }
    setStatus("done");
  };

  const allOk = result?.ok;

  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <ShieldCheck size={15} style={{ color: "#888", flexShrink: 0 }} />
        <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.health_check.title")}</span>
      </div>
      <p style={{ color: "#666", fontSize: 13, marginBottom: 16 }}>{t("system.health_check.description")}</p>
      <Button onClick={run} disabled={status === "loading"} variant="outline" className="gap-2" style={{ borderColor: "#4caf50", color: "#4caf50" }}>
        {status === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
        {status === "loading" ? t("system.health_check.loading") : t("system.health_check.button")}
      </Button>

      {status === "done" && result && (
        <div style={{ marginTop: 14 }}>
          {result.error ? (
            <p style={{ color: "#f87171", fontSize: 13 }}>{result.error}</p>
          ) : (
            <>
              <p style={{ color: allOk ? "#4caf50" : "#f87171", fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
                {allOk ? `✓ ${t("system.health_check.ok")}` : `✗ ${t("system.health_check.errors", { count: result.total_errors })}`}
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {result.imports && (
                  <HealthRow
                    label={t("system.health_check.imports")}
                    passed={result.imports.passed}
                    failed={result.imports.failed}
                    extra={result.imports.skipped > 0 ? `${result.imports.skipped} ${t("system.health_check.skipped")}` : undefined}
                    errors={result.imports.errors}
                    t={t}
                  />
                )}
                {result.configs && (
                  <HealthRow
                    label={t("system.health_check.configs")}
                    passed={result.configs.passed}
                    failed={result.configs.failed}
                    errors={result.configs.errors}
                    t={t}
                  />
                )}
                {result.services && !result.services.skipped && (
                  <HealthRow
                    label={t("system.health_check.services")}
                    passed={result.services.passed}
                    failed={result.services.failed}
                    errors={result.services.results.filter(r => !r.ok).map(r => ({ name: r.name, detail: r.detail }))}
                    t={t}
                  />
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function HealthRow({ label, passed, failed, extra, errors, t }: {
  label: string;
  passed: number;
  failed: number;
  extra?: string;
  errors?: { name: string; detail: string }[];
  t: (k: string) => string;
}) {
  const ok = failed === 0;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ color: "#777", fontSize: 12, width: 60 }}>{label}</span>
        <span style={{ color: ok ? "#4caf50" : "#f87171", fontSize: 12 }}>
          {ok ? `${passed} ${t("system.health_check.ok_short")}` : `${failed} ${t("system.health_check.failed")}`}
        </span>
        {extra && <span style={{ color: "#666", fontSize: 11 }}>({extra})</span>}
      </div>
      {errors && errors.length > 0 && (
        <div style={{ marginTop: 4, marginLeft: 68 }}>
          {errors.map((e, i) => (
            <div key={i} style={{ color: "#f87171", fontSize: 11, lineHeight: 1.5 }}>
              <span style={{ color: "#888" }}>{e.name}</span> — {e.detail}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SnapshotCard() {
  const { t } = useTranslation();
  const [snapshots, setSnapshots] = useState<ConfigSnapshot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [actionMsg, setActionMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Import/export current state
  const [importName, setImportName] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await apiListConfigSnapshots();
      setSnapshots(r.snapshots);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { load(); }, [load]);

  const atMax = snapshots.length >= 10;
  const selected = snapshots.find(s => s.id === selectedId) ?? null;
  const busy = restoring || deleting || exporting;

  const formatDate = (iso: string) => {
    try {
      const locale = i18n.language === "nb" ? "nb-NO" : i18n.language;
      return new Date(iso).toLocaleString(locale, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch { return iso; }
  };

  const doSave = async () => {
    if (!name.trim()) { setSaveMsg({ ok: false, text: t("system.snapshots.name_required") }); return; }
    setSaving(true); setSaveMsg(null);
    try {
      const r = await apiSaveConfigSnapshot(name.trim());
      if (r.ok) {
        setName("");
        setSaveMsg({ ok: true, text: t("system.snapshots.save_button") + " ✓" });
        await load();
      } else if (r.error === "max_reached") {
        setSaveMsg({ ok: false, text: t("system.snapshots.max_reached") });
      } else {
        setSaveMsg({ ok: false, text: t("system.snapshots.save_error") });
      }
    } catch {
      setSaveMsg({ ok: false, text: t("system.snapshots.save_error") });
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(null), 5000);
    }
  };

  const doInstall = async () => {
    if (!selected) return;
    if (!window.confirm(t("system.snapshots.restore_confirm", { name: selected.name }))) return;
    setRestoring(true); setActionMsg(null);
    try {
      const r = await apiRestoreConfigSnapshot(selected.id);
      setActionMsg({ ok: r.ok, text: r.ok ? t("system.snapshots.restore_ok", { files: r.restored.join(", ") }) : t("system.snapshots.restore_error") });
    } catch {
      setActionMsg({ ok: false, text: t("system.snapshots.restore_error") });
    } finally {
      setRestoring(false);
      setTimeout(() => setActionMsg(null), 6000);
    }
  };

  const doDownload = async () => {
    if (!selected) return;
    setExporting(true);
    try {
      const slug = selected.name.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 40);
      await apiExportConfigSnapshot(selected.id, `ktsb-config-${slug}-${selected.id}.zip`);
    } catch {
      setActionMsg({ ok: false, text: t("system.snapshots.save_error") });
      setTimeout(() => setActionMsg(null), 4000);
    } finally {
      setExporting(false);
    }
  };

  const doDelete = async () => {
    if (!selected) return;
    if (!window.confirm(t("system.snapshots.delete_confirm", { name: selected.name }))) return;
    setDeleting(true);
    try {
      await apiDeleteConfigSnapshot(selected.id);
      setSelectedId(null);
      await load();
    } catch {
      setActionMsg({ ok: false, text: t("system.snapshots.delete_error") });
      setTimeout(() => setActionMsg(null), 4000);
    } finally {
      setDeleting(false);
    }
  };

  const doImport = async () => {
    if (!importName.trim()) { setImportMsg({ ok: false, text: t("system.snapshots.name_required") }); return; }
    if (!importFile) { setImportMsg({ ok: false, text: t("system.snapshots.import_error") }); return; }
    setImporting(true); setImportMsg(null);
    try {
      const r = await apiImportConfigSnapshot(importName.trim(), importFile);
      if (r.ok) {
        setImportName(""); setImportFile(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
        setImportMsg({ ok: true, text: t("system.snapshots.import_ok", { count: r.count ?? 0 }) });
        await load();
      } else if (r.error === "max_reached") {
        setImportMsg({ ok: false, text: t("system.snapshots.max_reached") });
      } else if (r.error === "no_yaml_files") {
        setImportMsg({ ok: false, text: t("system.snapshots.import_no_yaml") });
      } else {
        setImportMsg({ ok: false, text: t("system.snapshots.import_error") });
      }
    } catch {
      setImportMsg({ ok: false, text: t("system.snapshots.import_error") });
    } finally {
      setImporting(false);
      setTimeout(() => setImportMsg(null), 6000);
    }
  };

  return (
    <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px", border: "1px solid #3b82f633" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <BookmarkPlus size={15} style={{ color: "#60a5fa", flexShrink: 0 }} />
        <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.snapshots.title")}</span>
      </div>
      <p style={{ color: "#666", fontSize: 13, marginBottom: 16 }}>{t("system.snapshots.description")}</p>

      {/* Save row */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
        <input
          type="text"
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === "Enter" && !atMax && doSave()}
          placeholder={t("system.snapshots.name_placeholder")}
          disabled={saving || atMax}
          style={{ flex: 1, background: "#1a1a1a", border: "1px solid #333", borderRadius: 6, padding: "6px 10px", color: "#ddd", fontSize: 13 }}
        />
        <Button size="sm" onClick={doSave} disabled={saving || atMax} className="gap-2">
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <BookmarkPlus className="h-3 w-3" />}
          {saving ? t("system.snapshots.saving") : t("system.snapshots.save_button")}
        </Button>
      </div>

      {/* Counter */}
      <div style={{ fontSize: 12, marginBottom: 12, color: atMax ? "#f87171" : "#555" }}>
        {t("system.snapshots.counter", { count: snapshots.length })}
        {atMax && <span style={{ marginLeft: 8 }}>{t("system.snapshots.max_reached")}</span>}
      </div>

      {saveMsg && <p style={{ fontSize: 13, marginBottom: 10, color: saveMsg.ok ? "#4caf50" : "#f87171" }}>{saveMsg.text}</p>}

      {/* Snapshot list — click to select */}
      {snapshots.length === 0 ? (
        <p style={{ color: "#555", fontSize: 13, marginBottom: 16 }}>{t("system.snapshots.empty")}</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}>
          {snapshots.map(s => {
            const isSelected = s.id === selectedId;
            return (
              <div
                key={s.id}
                onClick={() => setSelectedId(isSelected ? null : s.id)}
                style={{
                  background: isSelected ? "#1a2a3a" : "#111",
                  border: `1px solid ${isSelected ? "#3b82f6" : "#222"}`,
                  borderRadius: 8, padding: "10px 14px", cursor: "pointer",
                  transition: "border-color 0.15s, background 0.15s",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{
                    width: 10, height: 10, borderRadius: "50%", flexShrink: 0,
                    border: `2px solid ${isSelected ? "#3b82f6" : "#444"}`,
                    background: isSelected ? "#3b82f6" : "transparent",
                    transition: "all 0.15s",
                  }} />
                  <span style={{ color: isSelected ? "#93c5fd" : "#ddd", fontSize: 13, fontWeight: 500, flex: 1 }}>{s.name}</span>
                  <span style={{ color: "#555", fontSize: 11 }}>{formatDate(s.created)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Action bar — activates when a snapshot is selected */}
      <div style={{ marginBottom: 16 }}>
        {!selected && snapshots.length > 0 && (
          <p style={{ color: "#444", fontSize: 12, marginBottom: 8 }}>{t("system.snapshots.select_hint")}</p>
        )}
        <div style={{ display: "flex", gap: 8 }}>
          <Button
            size="sm"
            variant="outline"
            onClick={doInstall}
            disabled={!selected || busy}
            className="gap-2"
            style={{ flex: 1, opacity: selected ? 1 : 0.35, borderColor: "#4caf50", color: "#4caf50" }}
          >
            {restoring ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
            {restoring ? t("system.snapshots.saving") : t("system.snapshots.install_button")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={doDownload}
            disabled={!selected || busy}
            className="gap-2"
            style={{ flex: 1, opacity: selected ? 1 : 0.35, borderColor: "#60a5fa", color: "#60a5fa" }}
          >
            {exporting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
            {exporting ? "…" : t("system.snapshots.download_button")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={doDelete}
            disabled={!selected || busy}
            style={{ opacity: selected ? 1 : 0.35, color: "#666", padding: "0 10px" }}
          >
            {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
          </Button>
        </div>
        {actionMsg && (
          <p style={{ fontSize: 12, marginTop: 8, color: actionMsg.ok ? "#4caf50" : "#f87171" }}>{actionMsg.text}</p>
        )}
      </div>

      {/* Export current + Import section */}
      <div style={{ borderTop: "1px solid #1e1e1e", paddingTop: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
          <Upload size={13} style={{ color: "#888" }} />
          <span style={{ color: "#aaa", fontSize: 13, fontWeight: 600 }}>{t("system.snapshots.import_title")}</span>
        </div>
        <p style={{ color: "#555", fontSize: 12, marginBottom: 12 }}>{t("system.snapshots.import_description")}</p>

        {/* Import zip */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input
            type="text"
            value={importName}
            onChange={e => setImportName(e.target.value)}
            placeholder={t("system.snapshots.import_name_placeholder")}
            disabled={importing}
            style={{ flex: 1, minWidth: 140, background: "#1a1a1a", border: "1px solid #333", borderRadius: 6, padding: "6px 10px", color: "#ddd", fontSize: 13 }}
          />
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            id="snapshot-file-input"
            onChange={e => {
              const file = e.target.files?.[0] ?? null;
              setImportFile(file);
              if (file && !importName.trim()) {
                const autoName = file.name
                  .replace(/\.zip$/i, "")
                  .replace(/^ktsb-config-/, "");
                setImportName(autoName);
              }
            }}
            disabled={importing}
            style={{ display: "none" }}
          />
          <label
            htmlFor="snapshot-file-input"
            style={{
              flex: 1, minWidth: 140,
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 10px", borderRadius: 8, fontSize: 12,
              border: "1px solid #444", color: importFile ? "#ccc" : "#666",
              cursor: importing ? "default" : "pointer",
              background: "#1a1a1a",
              userSelect: "none", overflow: "hidden",
              opacity: importing ? 0.5 : 1,
            }}
          >
            <Upload style={{ width: 12, height: 12, flexShrink: 0 }} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {importFile ? importFile.name : t("system.snapshots.choose_file")}
            </span>
          </label>
          <Button size="sm" variant="outline" onClick={doImport} disabled={importing || !importFile} className="gap-2" style={{ borderColor: "#a78bfa", color: "#a78bfa" }}>
            {importing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
            {importing ? t("system.snapshots.importing") : t("system.snapshots.import_button")}
          </Button>
        </div>
        {importMsg && (
          <p style={{ fontSize: 12, marginTop: 8, color: importMsg.ok ? "#4caf50" : "#f87171" }}>{importMsg.text}</p>
        )}
      </div>
    </div>
  );
}

function RollbackCard() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const [result, setResult] = useState<{ restored: string[]; errors: string[] } | null>(null);
  const [showPin, setShowPin] = useState(false);
  const [pin, setPin] = useState("");
  const [pinError, setPinError] = useState("");
  const [verifying, setVerifying] = useState(false);

  const openModal = () => { setShowPin(true); setPin(""); setPinError(""); };
  const closeModal = () => { setShowPin(false); setPin(""); setPinError(""); };

  const doVerifyAndRollback = async () => {
    if (!pin.trim()) return;
    setVerifying(true); setPinError("");
    const ok = await apiVerifyPin(user?.username ?? "", pin);
    if (!ok) {
      setPinError(t("system.rollback.pin_error"));
      setVerifying(false);
      return;
    }
    closeModal();
    setStatus("loading"); setResult(null);
    try {
      const r = await apiSettingsRollback();
      setResult({ restored: r.restored, errors: r.errors });
      setStatus(r.ok ? "ok" : "error");
      setTimeout(() => setStatus("idle"), 8000);
    } catch {
      setStatus("error");
      setTimeout(() => setStatus("idle"), 4000);
    } finally {
      setVerifying(false);
    }
  };

  return (
    <>
      {/* PIN modal */}
      {showPin && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ background: "#1a1a1a", border: "1px solid #ef444455", borderRadius: 12, padding: "28px 32px", maxWidth: 400, width: "90%" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <RotateCcw size={15} style={{ color: "#f87171" }} />
              <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.rollback.pin_title")}</span>
            </div>
            <p style={{ color: "#f87171", fontSize: 13, marginBottom: 18, lineHeight: 1.6 }}>{t("system.rollback.pin_warning")}</p>
            <input
              type="password"
              inputMode="numeric"
              value={pin}
              onChange={e => setPin(e.target.value)}
              onKeyDown={e => e.key === "Enter" && doVerifyAndRollback()}
              placeholder={t("system.rollback.pin_placeholder")}
              autoFocus
              style={{ width: "100%", background: "#111", border: "1px solid #555", borderRadius: 6, padding: "8px 12px", color: "#ddd", fontSize: 14, marginBottom: 8, boxSizing: "border-box" }}
            />
            {pinError && <p style={{ color: "#f87171", fontSize: 12, marginBottom: 8 }}>{pinError}</p>}
            <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
              <Button variant="destructive" size="sm" onClick={doVerifyAndRollback} disabled={verifying || !pin.trim()} className="gap-2" style={{ flex: 1 }}>
                {verifying ? <><Loader2 className="h-3 w-3 animate-spin" /> {t("system.rollback.verifying")}</> : t("system.rollback.pin_confirm_button")}
              </Button>
              <Button variant="ghost" size="sm" onClick={closeModal} disabled={verifying} style={{ flex: 1 }}>
                {t("system.rollback.pin_cancel")}
              </Button>
            </div>
          </div>
        </div>
      )}

      <div className="admin-card" style={{ border: "1px solid #ef444433", borderRadius: 12, padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <RotateCcw size={15} style={{ color: "#f87171", flexShrink: 0 }} />
          <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.rollback.title")}</span>
        </div>
        <p style={{ color: "#666", fontSize: 13, marginBottom: 16 }}>{t("system.rollback.description")}</p>
        <Button variant="destructive" size="sm" onClick={openModal} disabled={status === "loading"} className="gap-2">
          {status === "loading"
            ? <><Loader2 className="h-3 w-3 animate-spin" /> {t("system.rollback.loading")}</>
            : <><RotateCcw className="h-4 w-4" /> {t("system.rollback.button")}</>}
        </Button>
        {status === "ok" && result && (
          <p style={{ color: "#4caf50", fontSize: 13, marginTop: 12 }}>
            ✓ {t("system.rollback.ok", { files: result.restored.join(", ") })}
            {result.errors.length > 0 && <span style={{ color: "#f87171" }}> {t("system.rollback.error_suffix")}{result.errors.join(", ")}</span>}
          </p>
        )}
        {status === "error" && <p style={{ color: "#f87171", fontSize: 13, marginTop: 12 }}>{t("system.rollback.error")}</p>}
      </div>
    </>
  );
}

export default function System() {
  const { t } = useTranslation();
  const [reloadStatus, setReloadStatus] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const [reloadResult, setReloadResult] = useState<ReloadResult | null>(null);
  const [compress, setCompress] = useState<CompressStatus | null>(null);
  const [compressStarting, setCompressStarting] = useState(false);
  const [services, setServices] = useState<Record<string, AdminServiceStatus>>({});

  const loadServices = useCallback(() => {
    apiAdminServices().then(d => setServices(d)).catch(() => {});
  }, []);

  useEffect(() => {
    loadServices();
    let active = true;
    const poll = () => {
      if (!active) return;
      axios.get<CompressStatus>(`${BASE}/api/memory/compress/status`, { headers: { Authorization: `Bearer ${token()}` } })
        .then(r => { if (active) setCompress(r.data); })
        .catch(() => {});
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => { active = false; clearInterval(id); };
  }, [loadServices]);

  const reload = async () => {
    setReloadStatus("loading");
    setReloadResult(null);
    try {
      const r = await axios.post<ReloadResult>(`${BASE}/api/reload`, {}, { headers: { Authorization: `Bearer ${token()}` } });
      setReloadResult(r.data);
      setReloadStatus("ok");
      setTimeout(() => setReloadStatus("idle"), 6000);
    } catch {
      setReloadStatus("error");
      setTimeout(() => setReloadStatus("idle"), 4000);
    }
  };

  const startCompress = async () => {
    setCompressStarting(true);
    try {
      await axios.post(`${BASE}/api/memory/compress`, {}, { headers: { Authorization: `Bearer ${token()}` } });
    } catch { /* polling handles it */ }
    setCompressStarting(false);
  };

  return (
    <div>
      <h1>{t("system.title")}</h1>
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

        {/* Hot-reload + Systemsjekk + Maskinvare — tre kolonner */}
        <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px" }}>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>

            {/* Kolonne 1: Hot-reload */}
            <div style={{ flex: 1, minWidth: 200 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <RefreshCw size={15} style={{ color: "#888", flexShrink: 0 }} />
                <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.hot_reload.title")}</span>
              </div>
              <p style={{ color: "#666", fontSize: 13, marginBottom: 16 }}>{t("system.hot_reload.description")}</p>
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <Button onClick={reload} disabled={reloadStatus === "loading"} variant="outline" className="gap-2" style={{ borderColor: "#60a5fa", color: "#60a5fa" }}>
                  {reloadStatus === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  {reloadStatus === "loading" ? t("system.hot_reload.loading") : t("system.hot_reload.button")}
                </Button>
                {reloadStatus === "ok" && reloadResult && (
                  <span style={{ color: "#4caf50", fontSize: 13, display: "flex", flexWrap: "wrap", gap: 4 }}>
                    ✓ {t("system.hot_reload.ok", { count: reloadResult.reloaded.length, aliases: reloadResult.aliases_count })}
                    {reloadResult.errors.length > 0 && (
                      <span style={{ color: "#f87171" }}>{t("system.hot_reload.error_api")}: {reloadResult.errors.join(", ")}</span>
                    )}
                  </span>
                )}
                {reloadStatus === "error" && <span style={{ color: "#f87171", fontSize: 13 }}>{t("system.hot_reload.error_api")}</span>}
              </div>
              <p style={{ color: "#555", fontSize: 12, marginTop: 12 }}>{t("system.hot_reload.note")}</p>
            </div>

            <div style={{ width: 1, background: "#1e1e1e", flexShrink: 0, minHeight: 80 }} />

            {/* Kolonne 2: Systemsjekk */}
            <HealthCheckCard />

            <div style={{ width: 1, background: "#1e1e1e", flexShrink: 0, minHeight: 80 }} />

            {/* Kolonne 3: Maskinvare */}
            <HardwareCard />

          </div>
        </div>

        {/* Per-service restart */}
        <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px" }}>
          <div style={{ color: "#ddd", fontSize: 15, fontWeight: 600, marginBottom: 6 }}>{t("system.services.title")}</div>
          <p style={{ color: "#666", fontSize: 13, marginBottom: 16 }}>{t("system.services.description")}</p>
          <div>
            {Object.entries(services).map(([key, { unit, active }]) => (
              <ServiceRestartRow
                key={key}
                serviceKey={key}
                unit={unit}
                active={active}
                onRestarted={loadServices}
              />
            ))}
          </div>
          {Object.keys(services).length === 0 && (
            <p style={{ color: "#555", fontSize: 13 }}>{t("system.services.loading")}</p>
          )}
        </div>

        {/* Memory */}
        <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px" }}>
          <div style={{ color: "#ddd", fontSize: 15, fontWeight: 600, marginBottom: 6 }}>{t("system.memory.title")}</div>
          <p style={{ color: "#666", fontSize: 13, marginBottom: 4 }}>{t("system.memory.description")}</p>
          <p style={{ color: "#555", fontSize: 12, marginBottom: 16 }}>
            {t("system.memory.last_run", { ts: formatTs(compress?.last_episode_ts ?? null, t("system.memory.never")) })}
          </p>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <Button
              onClick={startCompress}
              disabled={compress?.running || compressStarting}
              className="gap-2"
              style={{ background: (compress?.running || compressStarting) ? undefined : "#3d8a5e" }}
            >
              {(compress?.running || compressStarting) && <Loader2 className="h-4 w-4 animate-spin" />}
              {compress?.running ? t("system.memory.running") : compressStarting ? t("system.memory.starting") : t("system.memory.button")}
            </Button>
            {compress?.running && compress.step && (
              <span style={{ color: "#888", fontSize: 13 }}>{compress.step}</span>
            )}
            {!compress?.running && compress?.finished_at && !compress.error && (
              <span style={{ color: "#4caf50", fontSize: 13 }}>
                ✓ {t("system.memory.ok", { episodes: compress.episodes, compressed: compress.compressed })}
              </span>
            )}
            {!compress?.running && compress?.error && (
              <span style={{ color: "#f87171", fontSize: 13 }}>{t("system.memory.error_prefix")}{compress.error}</span>
            )}
          </div>
          {compress?.running && compress.log.length > 0 && (
            <div style={{ marginTop: 16, background: "#111", borderRadius: 8, padding: "10px 14px", fontFamily: "monospace", fontSize: 12, color: "#666", maxHeight: 112, overflowY: "auto" }}>
              {compress.log.slice(-6).map((line, i) => <div key={i}>{line}</div>)}
            </div>
          )}
        </div>

        {/* Config snapshots */}
        <SnapshotCard />

        {/* Rollback */}
        <RollbackCard />

        {/* GitHub note */}
        <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px", border: "1px solid #f59e0b33" }}>
          <div style={{ color: "#f59e0b", fontSize: 13, fontWeight: 600, marginBottom: 10 }}>{t("system.github.title")}</div>
          <p style={{ color: "#666", fontSize: 13, lineHeight: 1.6 }}>{t("system.github.description")}</p>
        </div>

      </div>
    </div>
  );
}
