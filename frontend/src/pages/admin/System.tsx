import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Loader2, RefreshCw, RotateCcw, ShieldCheck, Cpu } from "lucide-react";
import axios from "axios";
import i18n from "@/i18n";
import {
  apiAdminServices, apiRestartService, apiSettingsRollback,
  type ServiceKey, type AdminServiceStatus,
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
  "vaktmester", "voice", "ha-log-bridge", "frontend",
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
      <Button onClick={run} disabled={status === "loading"} variant="outline" className="gap-2">
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

function RollbackCard() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const [result, setResult] = useState<{ restored: string[]; errors: string[] } | null>(null);

  const doRollback = async () => {
    if (!window.confirm(t("system.rollback.confirm"))) return;
    setStatus("loading");
    setResult(null);
    try {
      const r = await apiSettingsRollback();
      setResult({ restored: r.restored, errors: r.errors });
      setStatus(r.ok ? "ok" : "error");
      setTimeout(() => setStatus("idle"), 8000);
    } catch {
      setStatus("error");
      setTimeout(() => setStatus("idle"), 4000);
    }
  };

  return (
    <div className="admin-card" style={{ border: "1px solid #ef444433", borderRadius: 12, padding: "20px 24px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <RotateCcw size={15} style={{ color: "#f87171", flexShrink: 0 }} />
        <span style={{ color: "#ddd", fontSize: 15, fontWeight: 600 }}>{t("system.rollback.title")}</span>
      </div>
      <p style={{ color: "#666", fontSize: 13, marginBottom: 16 }}>{t("system.rollback.description")}</p>
      <Button
        variant="destructive"
        size="sm"
        onClick={doRollback}
        disabled={status === "loading"}
        className="gap-2"
      >
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
                <Button onClick={reload} disabled={reloadStatus === "loading"} variant="outline" className="gap-2">
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
