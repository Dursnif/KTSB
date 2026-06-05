import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Loader2, RefreshCw, RotateCcw, ShieldCheck, Cpu, Save, Trash2, Download, Upload, FlaskConical, HardDrive } from "lucide-react";
import axios from "axios";
import i18n from "@/i18n";
import { useAuth } from "@/auth/AuthContext";
import {
  apiAdminServices, apiRestartService, apiSettingsRollback,
  apiVerifyPin,
  apiExportBackup, apiRestoreBackup,
  apiSaveBackupPoint, apiListBackupPoints, apiRestoreBackupPoint, apiDeleteBackupPoint, apiDownloadBackupPoint,
  type ServiceKey, type AdminServiceStatus, type BackupPoint, type RestoreResult,
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

function TestRunCard() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<"idle" | "loading" | "done">("idle");
  const [result, setResult] = useState<{ ok: boolean; passed: number; failed: number; total: number; failures: { name: string; detail: string }[]; error?: string } | null>(null);

  const run = async () => {
    setStatus("loading");
    setResult(null);
    try {
      const r = await axios.get(`${BASE}/api/run_tests`, {
        headers: { Authorization: `Bearer ${token()}` },
        timeout: 60000,
      });
      setResult(r.data);
    } catch {
      setResult({ ok: false, passed: 0, failed: 0, total: 0, failures: [], error: t("system.hot_reload.error_api") });
    }
    setStatus("done");
  };

  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <FlaskConical size={15} style={{ color: "#888", flexShrink: 0 }} />
        <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.test_run.title")}</span>
      </div>
      <p style={{ color: "#666", fontSize: 13, marginBottom: 16 }}>{t("system.test_run.description")}</p>
      <Button onClick={run} disabled={status === "loading"} variant="outline" className="gap-2" style={{ borderColor: "#a78bfa", color: "#a78bfa" }}>
        {status === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
        {status === "loading" ? t("system.test_run.loading") : t("system.test_run.button")}
      </Button>

      {status === "done" && result && (
        <div style={{ marginTop: 14 }}>
          {result.error ? (
            <p style={{ color: "#f87171", fontSize: 13 }}>{result.error}</p>
          ) : (
            <>
              <p style={{ color: result.ok ? "#4caf50" : "#f87171", fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
                {result.ok
                  ? `✓ ${t("system.test_run.ok")}`
                  : `✗ ${t("system.test_run.failed", { count: result.failed })}`}
              </p>
              <p style={{ color: "#666", fontSize: 12, marginBottom: result.failures.length > 0 ? 8 : 0 }}>
                {result.passed} {t("system.test_run.of")} {result.total} {t("system.test_run.passed_short")}
              </p>
              {result.failures.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {result.failures.map((f, i) => (
                    <div key={i}>
                      <div style={{ color: "#f87171", fontSize: 12, fontWeight: 500 }}>{f.name}</div>
                      {f.detail && <div style={{ color: "#888", fontSize: 11, marginLeft: 8, lineHeight: 1.4 }}>{f.detail}</div>}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
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

// ── BackupRestoreCard ──────────────────────────────────────────────────────────

const ALL_CATEGORIES = [
  "config", "ltm_database", "kaare_memory",
  "personality", "user_profiles", "notes_state", "argus_events", "secrets", "images",
] as const;
type BackupCat = typeof ALL_CATEGORIES[number] | "user_keys";

const ENCRYPTED_CATS: BackupCat[] = ["ltm_database", "kaare_memory", "personality", "user_profiles"];
const DEFAULT_CATS: BackupCat[] = ["config", "ltm_database", "kaare_memory", "personality", "user_profiles", "notes_state"];

function BackupRestoreCard() {
  const { t } = useTranslation();

  // shared category selection (used for both export and save-point)
  const [cats, setCats] = useState<Set<BackupCat>>(new Set(DEFAULT_CATS));

  // save-point state
  const [pointName, setPointName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [points, setPoints] = useState<BackupPoint[]>([]);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  // PIN modal for restore-from-point
  const [restorePointId, setRestorePointId] = useState<string | null>(null);
  const [pinModal, setPinModal] = useState(false);
  const [pin, setPin] = useState("");
  const [pinError, setPinError] = useState("");
  const [restoringPoint, setRestoringPoint] = useState(false);

  // export (download) state
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // restore-from-file state
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreCats, setRestoreCats] = useState<Set<BackupCat>>(new Set());
  const [restorePin, setRestorePin] = useState("");
  const [restoring, setRestoring] = useState(false);
  const [restoreResult, setRestoreResult] = useState<RestoreResult | null>(null);
  const [restoreMsg, setRestoreMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const restoreFileRef = useRef<HTMLInputElement>(null);

  const needsUserKeys = (c: Set<BackupCat>) => [...c].some(x => ENCRYPTED_CATS.includes(x as BackupCat));
  const effectiveCats = (c: Set<BackupCat>): BackupCat[] => {
    const out = new Set(c);
    if (needsUserKeys(c)) out.add("user_keys");
    return [...out];
  };

  const toggleCat = (cat: BackupCat) => {
    if (cat === "user_keys") return;
    setCats(prev => { const n = new Set(prev); n.has(cat) ? n.delete(cat) : n.add(cat); return n; });
  };

  const loadPoints = useCallback(async () => {
    try {
      const r = await apiListBackupPoints();
      setPoints(r.points);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadPoints(); }, [loadPoints]);

  const atMax = points.length >= 5;

  const formatDate = (iso: string) => {
    try {
      const locale = i18n.language === "nb" ? "nb-NO" : i18n.language;
      return new Date(iso).toLocaleString(locale, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch { return iso; }
  };
  const formatSize = (bytes: number) => {
    const mb = bytes / (1024 * 1024);
    return mb < 1 ? "< 1 MB" : `${mb.toFixed(1)} MB`;
  };

  const doSavePoint = async () => {
    const allCats = effectiveCats(cats);
    if (allCats.filter(c => c !== "user_keys").length === 0) {
      setSaveMsg({ ok: false, text: t("system.backup.no_categories") });
      return;
    }
    setSaving(true); setSaveMsg(null);
    try {
      const r = await apiSaveBackupPoint(allCats, pointName.trim());
      if (r.ok) {
        setPointName("");
        setSaveMsg({ ok: true, text: "✓ " + (r.name ?? t("system.backup.save_point_button")) });
        await loadPoints();
      } else {
        setSaveMsg({ ok: false, text: t("system.backup.max_reached") });
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "";
      setSaveMsg({ ok: false, text: detail === "max_reached" ? t("system.backup.max_reached") : (detail || "Error") });
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(null), 5000);
    }
  };

  const doDeletePoint = async (id: string, name: string) => {
    if (!window.confirm(t("system.backup.delete_point_confirm", { name }))) return;
    setDeletingId(id);
    try {
      await apiDeleteBackupPoint(id);
      await loadPoints();
    } catch { /* ignore */ }
    setDeletingId(null);
  };

  const doDownloadPoint = async (id: string, name: string) => {
    setDownloadingId(id);
    try {
      const safeName = name.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 40);
      await apiDownloadBackupPoint(id, `ktsb-backup-${safeName}-${id}.zip`);
    } catch { /* ignore */ }
    setDownloadingId(null);
  };

  const openPinModal = (id: string) => { setRestorePointId(id); setPinModal(true); setPin(""); setPinError(""); };
  const closePinModal = () => { setPinModal(false); setRestorePointId(null); setPin(""); setPinError(""); };

  const doRestorePoint = async () => {
    if (!restorePointId || !pin.trim()) return;
    setRestoringPoint(true); setPinError("");
    try {
      const r = await apiRestoreBackupPoint(restorePointId, [], pin);
      closePinModal();
      setRestoreMsg(r.ok
        ? { ok: true, text: t("system.backup.restore_ok", { items: r.restored.join(", ") }) }
        : { ok: false, text: r.errors.join("; ") || "Restore failed" },
      );
      if (r.restart_needed) setRestoreResult(r);
      setTimeout(() => { setRestoreMsg(null); setRestoreResult(null); }, 8000);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "";
      setPinError(detail || t("system.rollback.pin_error"));
    } finally {
      setRestoringPoint(false);
    }
  };

  const doExport = async () => {
    const allCats = effectiveCats(cats);
    if (allCats.filter(c => c !== "user_keys").length === 0) {
      setExportMsg({ ok: false, text: t("system.backup.no_categories") });
      return;
    }
    setExporting(true); setExportMsg(null);
    try {
      const ts = new Date().toISOString().slice(0, 10);
      await apiExportBackup(allCats, `ktsb-backup-${ts}.zip`);
      setExportMsg({ ok: true, text: "✓ " + t("system.backup.download_button") });
    } catch {
      setExportMsg({ ok: false, text: "Export failed" });
    } finally {
      setExporting(false);
      setTimeout(() => setExportMsg(null), 5000);
    }
  };

  const onRestoreFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    setRestoreFile(file); setRestoreResult(null); setRestoreMsg(null);
    setRestoreCats(file ? new Set(DEFAULT_CATS) : new Set());
  };

  const doRestore = async () => {
    if (!restoreFile || restoreCats.size === 0 || !restorePin.trim()) return;
    setRestoring(true); setRestoreMsg(null); setRestoreResult(null);
    try {
      const r = await apiRestoreBackup(restoreFile, [...restoreCats], restorePin);
      setRestoreResult(r);
      setRestoreMsg(r.ok
        ? { ok: true, text: t("system.backup.restore_ok", { items: r.restored.join(", ") }) }
        : { ok: false, text: r.errors.length > 0 ? t("system.backup.restore_errors", { errors: r.errors.join("; ") }) : "Restore failed" },
      );
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "restore failed";
      setRestoreMsg({ ok: false, text: detail });
    } finally {
      setRestoring(false);
    }
  };

  const hasSecrets = cats.has("secrets");
  const userKeysAuto = needsUserKeys(cats);
  const restorePointName = points.find(p => p.id === restorePointId)?.name ?? "";

  return (
    <>
      {/* PIN modal for restore from saved point */}
      {pinModal && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ background: "#1a1a1a", border: "1px solid #f59e0b55", borderRadius: 12, padding: "28px 32px", maxWidth: 400, width: "90%" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <RotateCcw size={15} style={{ color: "#f59e0b" }} />
              <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.backup.restore_point_title")}</span>
            </div>
            <p style={{ color: "#888", fontSize: 13, marginBottom: 18, lineHeight: 1.6 }}>
              {t("system.backup.restore_point_warning", { name: restorePointName })}
            </p>
            <input
              type="password"
              inputMode="numeric"
              value={pin}
              onChange={e => setPin(e.target.value)}
              onKeyDown={e => e.key === "Enter" && doRestorePoint()}
              placeholder={t("system.backup.pin_placeholder")}
              autoFocus
              style={{ width: "100%", background: "#111", border: "1px solid #555", borderRadius: 6, padding: "8px 12px", color: "#ddd", fontSize: 14, marginBottom: 8, boxSizing: "border-box" }}
            />
            {pinError && <p style={{ color: "#f87171", fontSize: 12, marginBottom: 8 }}>{pinError}</p>}
            <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
              <Button variant="outline" size="sm" onClick={doRestorePoint} disabled={restoringPoint || !pin.trim()} className="gap-2" style={{ flex: 1, borderColor: "#f59e0b", color: "#f59e0b" }}>
                {restoringPoint ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                {restoringPoint ? t("system.backup.restoring") : t("system.backup.restore_point_button")}
              </Button>
              <Button variant="ghost" size="sm" onClick={closePinModal} disabled={restoringPoint} style={{ flex: 1 }}>
                {t("system.rollback.pin_cancel")}
              </Button>
            </div>
          </div>
        </div>
      )}

      <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px", border: "1px solid #22c55e33" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <HardDrive size={15} style={{ color: "#22c55e", flexShrink: 0 }} />
          <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.backup.title")}</span>
        </div>
        <p style={{ color: "#666", fontSize: 13, marginBottom: 16 }}>{t("system.backup.description")}</p>

        {/* Category checkboxes */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 14 }}>
          <label style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: "default", opacity: 0.6 }}>
            <input type="checkbox" checked={userKeysAuto} disabled readOnly style={{ marginTop: 2 }} />
            <span style={{ fontSize: 13, color: "#aaa" }}>
              {t("system.backup.cat_user_keys")}
              {userKeysAuto && <span style={{ fontSize: 11, color: "#555", marginLeft: 6 }}>— {t("system.backup.user_keys_required")}</span>}
            </span>
          </label>
          {ALL_CATEGORIES.map(cat => (
            <label key={cat} style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: "pointer" }}>
              <input type="checkbox" checked={cats.has(cat)} onChange={() => toggleCat(cat)} style={{ marginTop: 2 }} />
              <span style={{ fontSize: 13, color: "#ddd" }}>
                {t(`system.backup.cat_${cat}`)}
                {cat === "secrets" && cats.has("secrets") && (
                  <span style={{ fontSize: 11, color: "#f87171", marginLeft: 6, fontWeight: 500 }}>⚠ {t("system.backup.secrets_warning")}</span>
                )}
              </span>
            </label>
          ))}
        </div>

        {hasSecrets && (
          <p style={{ fontSize: 12, color: "#f87171", marginBottom: 10, fontWeight: 500 }}>⚠ {t("system.backup.secrets_warning")}</p>
        )}

        {/* ── Saved backup points ── */}
        <div style={{ borderTop: "1px solid #1e1e1e", marginTop: 6, paddingTop: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <span style={{ color: "#aaa", fontSize: 13, fontWeight: 600 }}>{t("system.backup.saved_points_title")}</span>
            <span style={{ fontSize: 11, color: atMax ? "#f87171" : "#555" }}>{points.length}/5</span>
          </div>

          <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
            <input
              type="text"
              value={pointName}
              onChange={e => setPointName(e.target.value)}
              onKeyDown={e => e.key === "Enter" && !atMax && !saving && doSavePoint()}
              placeholder={t("system.backup.save_point_name_placeholder")}
              disabled={saving || atMax}
              style={{ flex: 1, background: "#1a1a1a", border: "1px solid #333", borderRadius: 6, padding: "6px 10px", color: "#ddd", fontSize: 13 }}
            />
            <Button size="sm" onClick={doSavePoint} disabled={saving || atMax} className="gap-2">
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
              {saving ? t("system.backup.saving_point") : t("system.backup.save_point_button")}
            </Button>
          </div>

          {saveMsg && <p style={{ fontSize: 12, marginBottom: 6, color: saveMsg.ok ? "#4caf50" : "#f87171" }}>{saveMsg.text}</p>}
          {atMax && !saveMsg && <p style={{ fontSize: 12, color: "#f87171", marginBottom: 6 }}>{t("system.backup.max_reached")}</p>}

          {points.length === 0 ? (
            <p style={{ color: "#555", fontSize: 13, marginBottom: 4 }}>{t("system.backup.saved_points_empty")}</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 4 }}>
              {points.map(pt => (
                <div key={pt.id} style={{ background: "#111", border: "1px solid #222", borderRadius: 8, padding: "10px 14px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ color: "#ddd", fontSize: 13, fontWeight: 500, flex: 1, minWidth: 80 }}>{pt.name}</span>
                    <span style={{ color: "#555", fontSize: 11 }}>{formatDate(pt.created)}</span>
                    <span style={{ color: "#444", fontSize: 11 }}>{formatSize(pt.size_bytes)}</span>
                    <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                      <Button
                        size="sm" variant="outline"
                        onClick={() => openPinModal(pt.id)}
                        disabled={!!deletingId || !!downloadingId}
                        className="gap-1"
                        style={{ fontSize: 11, padding: "2px 8px", borderColor: "#f59e0b", color: "#f59e0b" }}
                      >
                        <RotateCcw className="h-3 w-3" />
                        {t("system.backup.restore_point_button")}
                      </Button>
                      <Button
                        size="sm" variant="outline"
                        onClick={() => doDownloadPoint(pt.id, pt.name)}
                        disabled={downloadingId === pt.id || !!deletingId}
                        style={{ padding: "2px 8px", borderColor: "#60a5fa", color: "#60a5fa" }}
                      >
                        {downloadingId === pt.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                      </Button>
                      <Button
                        size="sm" variant="ghost"
                        onClick={() => doDeletePoint(pt.id, pt.name)}
                        disabled={deletingId === pt.id || !!downloadingId}
                        style={{ padding: "2px 6px", color: "#666" }}
                      >
                        {deletingId === pt.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                      </Button>
                    </div>
                  </div>
                  <div style={{ marginTop: 4, fontSize: 11, color: "#444" }}>{pt.categories.join(", ")}</div>
                </div>
              ))}
            </div>
          )}

          {restoreMsg && (
            <p style={{ fontSize: 12, marginTop: 8, color: restoreMsg.ok ? "#4caf50" : "#f87171", lineHeight: 1.5 }}>{restoreMsg.text}</p>
          )}
          {restoreResult?.restart_needed && (
            <p style={{ fontSize: 12, marginTop: 4, color: "#60a5fa" }}>{t("system.backup.restarting")}</p>
          )}
        </div>

        {/* ── Download ZIP ── */}
        <div style={{ borderTop: "1px solid #1e1e1e", marginTop: 14, paddingTop: 14 }}>
          <Button
            size="sm" onClick={doExport} disabled={exporting} className="gap-2"
            style={{ background: exporting ? undefined : "#166534", borderColor: "#22c55e", color: "#22c55e", border: "1px solid" }}
          >
            {exporting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
            {exporting ? t("system.backup.downloading") : t("system.backup.download_button")}
          </Button>
          {exportMsg && <p style={{ fontSize: 12, marginTop: 8, color: exportMsg.ok ? "#4caf50" : "#f87171" }}>{exportMsg.text}</p>}
        </div>

        {/* ── Restore from file ── */}
        <div style={{ borderTop: "1px solid #1e1e1e", marginTop: 14, paddingTop: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
            <Upload size={13} style={{ color: "#888" }} />
            <span style={{ color: "#aaa", fontSize: 13, fontWeight: 600 }}>{t("system.backup.restore_section")}</span>
          </div>

          <input ref={restoreFileRef} type="file" accept=".zip" id="backup-restore-file" onChange={onRestoreFileChange} style={{ display: "none" }} />
          <label
            htmlFor="backup-restore-file"
            style={{
              display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 12,
              padding: "5px 10px", borderRadius: 8, fontSize: 12,
              border: "1px solid #444", color: restoreFile ? "#ccc" : "#666",
              cursor: "pointer", background: "#1a1a1a", userSelect: "none",
              maxWidth: "100%", overflow: "hidden",
            }}
          >
            <Upload style={{ width: 12, height: 12, flexShrink: 0 }} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {restoreFile ? restoreFile.name : t("system.backup.choose_file")}
            </span>
          </label>

          {restoreFile && (
            <>
              <div style={{ display: "flex", flexDirection: "column", gap: 5, marginBottom: 12 }}>
                {(["user_keys", ...ALL_CATEGORIES] as BackupCat[]).map(cat => (
                  <label key={cat} style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                    <input type="checkbox" checked={restoreCats.has(cat)} onChange={() => {
                      setRestoreCats(prev => { const n = new Set(prev); n.has(cat) ? n.delete(cat) : n.add(cat); return n; });
                    }} />
                    <span style={{ fontSize: 12, color: "#bbb" }}>{t(`system.backup.cat_${cat}`)}</span>
                  </label>
                ))}
              </div>

              <p style={{ fontSize: 12, color: "#f59e0b", marginBottom: 12 }}>⚠ {t("system.backup.restore_warning")}</p>

              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <input
                  type="password" inputMode="numeric"
                  value={restorePin} onChange={e => setRestorePin(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && !restoring && doRestore()}
                  placeholder={t("system.backup.pin_placeholder")}
                  disabled={restoring}
                  style={{ flex: 1, minWidth: 120, maxWidth: 160, background: "#1a1a1a", border: "1px solid #333", borderRadius: 6, padding: "6px 10px", color: "#ddd", fontSize: 13 }}
                />
                <Button
                  size="sm" variant="outline" onClick={doRestore}
                  disabled={restoring || !restorePin.trim() || restoreCats.size === 0}
                  className="gap-2" style={{ borderColor: "#f59e0b", color: "#f59e0b" }}
                >
                  {restoring ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                  {restoring ? t("system.backup.restoring") : t("system.backup.restore_button")}
                </Button>
              </div>

              {restoreMsg && <p style={{ fontSize: 12, marginTop: 10, color: restoreMsg.ok ? "#4caf50" : "#f87171", lineHeight: 1.5 }}>{restoreMsg.text}</p>}
              {restoreResult?.restart_needed && <p style={{ fontSize: 12, marginTop: 6, color: "#60a5fa" }}>{t("system.backup.restarting")}</p>}
            </>
          )}
        </div>
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

        {/* Hot-reload + Systemsjekk + Testkjøring + Maskinvare — fire kolonner */}
        <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px" }}>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>

            {/* Kolonne 1: Hot-reload */}
            <div style={{ flex: 1, minWidth: 160 }}>
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

            {/* Kolonne 3: Testkjøring */}
            <TestRunCard />

            <div style={{ width: 1, background: "#1e1e1e", flexShrink: 0, minHeight: 80 }} />

            {/* Kolonne 4: Maskinvare */}
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

        {/* Backup & restore */}
        <BackupRestoreCard />

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
