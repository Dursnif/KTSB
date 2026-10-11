import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CheckCircle2, XCircle, Loader2, RotateCcw, ChevronDown, ChevronUp, Download } from "lucide-react";
import {
  apiGetSttModelsStatus, apiPostSttModelActivate, apiPostSttModelDownload, apiPutVoiceBackend, apiRestartService,
  type MemoryEmbedJob, type SttModelsStatus,
} from "@/services/api";
import { FieldRow, SaveFeedback, ExpertOnly } from "../shared";
import { useSaveState } from "../useSaveState";

export type VoiceServicesData = {
  stt_backend: string;
  faster_whisper_model: string;
  compute_type: string;
  stt_enabled: boolean;
  model_dir: string;
  cpu_threads: number;
};

const CPU_THREAD_OPTIONS = [0, 2, 4, 6, 8, 12, 16];

const WHISPER_COMPUTE_OPTIONS = [
  { value: "int8",          labelKey: "settings.llm.whisper_compute_int8" },
  { value: "float16",       labelKey: "settings.llm.whisper_compute_float16" },
  { value: "int8_float16",  labelKey: "settings.llm.whisper_compute_int8_float16" },
  { value: "float32",       labelKey: "settings.llm.whisper_compute_float32" },
];

// STT language is driven by kare_language alone (P101) — no separate switch here. This
// heuristic is model-driven and symmetric: it asks "which languages does THIS model support"
// (from name/path pattern), not "is the selected language Norwegian". Produces only a soft
// warning; never changes actual STT behavior.
function _sttModelSupportedLangs(modelRef: string): string[] | "all" {
  const ref = modelRef.toLowerCase();
  if (ref.includes("nb-whisper")) return ["nb"]; // NbAiLab specialization (also nn, not a kare_language option)
  if (ref.endsWith(".en")) return ["en"];         // English-only Whisper distill
  return "all";                                   // standard multilingual Whisper — no known restriction
}

function JobLine({ job }: { job: MemoryEmbedJob }) {
  const { t } = useTranslation();
  if (job.status === "error") {
    return (
      <p className="text-xs text-red-400 flex items-center gap-1">
        <XCircle className="h-3 w-3 shrink-0" />
        {t(`settings.llm.stt_err_${job.error}`, { defaultValue: t("settings.llm.memembed_err_generic") })}
        {job.detail ? <span className="font-mono opacity-70"> — {job.detail}</span> : null}
      </p>
    );
  }
  if (job.status === "done") {
    const key = job.kind === "download" ? "settings.llm.stt_download_done" : "settings.llm.stt_switch_done";
    return <p className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> {t(key)}</p>;
  }
  let text = t(`settings.llm.memembed_phase_${job.phase}`, { defaultValue: t(`settings.llm.stt_phase_${job.phase}`, { defaultValue: job.phase }) });
  if (job.phase === "download" && job.total_bytes > 0) {
    text += ` ${Math.round(job.bytes / 1e6)} / ${Math.round(job.total_bytes / 1e6)} MB (${Math.min(100, Math.round((job.bytes / job.total_bytes) * 100))} %)`;
  }
  return <p className="text-xs text-muted-foreground flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" /> {text}</p>;
}

export function WhisperCard({ data, kareLanguage, onSaved }: { data: VoiceServicesData; kareLanguage: string; onSaved: () => void }) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed]     = useState(true);
  const [sttEnabled, setSttEnabled]   = useState(data.stt_enabled);
  const [backend, setBackend]         = useState(data.stt_backend);
  const [modelDir, setModelDir]       = useState(data.model_dir);
  const [modelDirEdited, setModelDirEdited] = useState(false);
  const [computeType, setComputeType] = useState(data.compute_type);
  const [cpuThreads, setCpuThreads]   = useState(data.cpu_threads);
  const [status, setStatus]           = useState<SttModelsStatus | null>(null);
  const [custom, setCustom]           = useState("");
  const [confirmRepo, setConfirmRepo] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [restarting, setRestarting]   = useState(false);
  const [restartOk, setRestartOk]     = useState<boolean | null>(null);
  const [restartError, setRestartError] = useState("");
  const ss = useSaveState();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wasRunning = useRef(false);

  const refresh = useCallback(() => {
    apiGetSttModelsStatus().then(st => {
      const nowRunning = st.job?.status === "running";
      if (wasRunning.current && !nowRunning && st.job?.kind === "activate") {
        // A finished switch changed the backend in the config: show it and reload the page data.
        setBackend(st.backend);
        onSaved();
      }
      wasRunning.current = nowRunning;
      setStatus(st);
    }).catch(() => {});
  }, [onSaved]);

  useEffect(() => { if (!collapsed) refresh(); }, [collapsed, refresh]);

  const running = status?.job?.status === "running";
  useEffect(() => {
    if (running && !pollRef.current) pollRef.current = setInterval(refresh, 1500);
    if (!running && pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [running, refresh]);

  const isOpenvino = backend === "openvino";
  const activeRef = isOpenvino ? (modelDir.split("/").pop() || modelDir) : (status?.active ?? data.faster_whisper_model);
  const supportedLangs = _sttModelSupportedLangs(activeRef);
  const langWarning = supportedLangs === "all" || supportedLangs.includes(kareLanguage)
    ? null
    : t("settings.llm.whisper_lang_warning", { model: activeRef, supported: supportedLangs.join("/"), lang: kareLanguage });

  const save = async () => {
    ss.saving();
    try {
      // The model itself is chosen with «Use this» below — never sent from here, so «Save»
      // cannot undo a switch made since the page loaded.
      await apiPutVoiceBackend({
        stt_backend:  backend,
        compute_type: computeType,
        cpu_threads:  cpuThreads,
        stt_enabled:  sttEnabled,
        ...(modelDirEdited ? { model_dir: modelDir } : {}),
      });
      setModelDirEdited(false);
      ss.saved();
      onSaved();
      refresh();
    } catch { ss.error(); }
  };

  const start = async (action: (repo: string) => Promise<MemoryEmbedJob>, repo: string) => {
    setActionError("");
    setConfirmRepo(null);
    try {
      await action(repo);
    } catch (e: unknown) {
      const code = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "";
      setActionError(t(`settings.llm.stt_err_${code}`, { defaultValue: t("settings.llm.memembed_err_generic") }));
    }
    refresh();
  };

  const restart = async () => {
    setRestarting(true);
    setRestartOk(null);
    setRestartError("");
    try {
      const r = await apiRestartService("voice");
      setRestartOk(r.ok);
      if (!r.ok) setRestartError(r.error ?? "");
    } catch { setRestartOk(false); }
    finally { setRestarting(false); }
  };

  const modelRow = (repo: string, tier: string | null, sizeMb: number | null, downloaded: boolean, active: boolean) => (
    <div key={repo} className={`px-3 py-2 space-y-1 ${active ? "bg-white/10" : ""}`}>
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <span className="text-sm font-medium">{tier ? t(`settings.llm.memembed_tier_${tier}`) : t("settings.llm.stt_custom_label")}</span>
          {tier && <span className="text-xs text-muted-foreground block">{t(`settings.llm.stt_note_${tier}`)}</span>}
          <span className="text-xs text-muted-foreground font-mono break-all">{repo}{sizeMb ? ` · ${sizeMb} MB` : ""}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {active && <Badge className="bg-green-700 text-white text-xs">{t("settings.llm.memembed_active")}</Badge>}
          {!active && downloaded && <Badge variant="outline" className="text-xs">{t("settings.llm.memembed_downloaded")}</Badge>}
          {!downloaded && (
            <Button size="sm" variant="outline" disabled={running} onClick={() => start(apiPostSttModelDownload, repo)}>
              <Download className="h-3 w-3 mr-1" /> {t("settings.llm.memembed_download_btn")}
            </Button>
          )}
          {!active && downloaded && (
            <Button size="sm" disabled={running} onClick={() => setConfirmRepo(repo)}>{t("settings.llm.memembed_use_btn")}</Button>
          )}
        </div>
      </div>
      {confirmRepo === repo && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200 space-y-2">
          <p>⚠️ {t("settings.llm.stt_switch_warning")}{isOpenvino ? ` ${t("settings.llm.stt_switch_from_openvino")}` : ""}</p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => start(apiPostSttModelActivate, repo)}>{t("settings.llm.stt_switch_confirm")}</Button>
            <Button size="sm" variant="outline" onClick={() => setConfirmRepo(null)}>{t("common.cancel")}</Button>
          </div>
        </div>
      )}
      {status?.job && status.job.model === repo && <JobLine job={status.job} />}
    </div>
  );

  const customJob = status?.job && !status.presets.some(p => p.repo === status.job?.model) && status.job.model !== status.custom?.repo
    ? status.job : null;

  return (
    <Card className={sttEnabled ? "" : "opacity-60"}>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div
          className="flex items-center gap-3 cursor-pointer select-none flex-1"
          onClick={() => setCollapsed(v => !v)}
        >
          <CardTitle className="text-base">{t("settings.llm.stt_title")}</CardTitle>
          <Badge variant="outline" className="text-xs font-mono">port 8011</Badge>
          <span className="text-xs text-muted-foreground">{sttEnabled ? t("common.enabled") : t("common.disabled")}</span>
        </div>
        <div className="flex items-center gap-3">
          <Switch
            checked={sttEnabled}
            onCheckedChange={v => setSttEnabled(v)}
            className="data-checked:bg-green-600 data-unchecked:bg-red-600"
            onClick={e => e.stopPropagation()}
          />
          {collapsed
            ? <ChevronDown className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(false)} />
            : <ChevronUp   className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(true)} />
          }
        </div>
      </CardHeader>
      {!collapsed && (
        <CardContent className="space-y-4 pt-0">
          <p className="text-xs text-muted-foreground">
            {t(status && ["nb", "nn", "no"].includes(status.lang) ? "settings.llm.stt_intro_nb" : "settings.llm.stt_intro_other")}
            {" "}{t("settings.llm.whisper_lang_note")}
          </p>
          {isOpenvino && status && (
            <p className="text-xs text-blue-300/90">{t("settings.llm.stt_openvino_active", { model: activeRef })}</p>
          )}

          {status === null
            ? <p className="text-xs text-muted-foreground flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" /> {t("common.loading")}</p>
            : (
              <div className="rounded-md border border-white/10 divide-y divide-white/5">
                {status.presets.map(p => modelRow(p.repo, p.tier, p.size_mb, p.downloaded, p.active))}
                {status.custom && modelRow(status.custom.repo, null, null, status.custom.downloaded, status.custom.active)}
              </div>
            )}

          <FieldRow label={t("settings.llm.stt_custom_field_label")} hint={t("settings.llm.stt_custom_field_hint")}>
            <div className="flex items-center gap-2 flex-wrap">
              <input
                value={custom}
                onChange={e => setCustom(e.target.value)}
                placeholder="NbAiLab/nb-whisper-base"
                className="flex h-9 w-80 max-w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
              />
              <Button size="sm" variant="outline" disabled={running || !custom.trim()} onClick={() => start(apiPostSttModelDownload, custom.trim())}>
                <Download className="h-3 w-3 mr-1" /> {t("settings.llm.memembed_download_btn")}
              </Button>
            </div>
            {customJob && <div className="mt-2"><JobLine job={customJob} /></div>}
          </FieldRow>
          {actionError && <p className="text-xs text-red-400">{actionError}</p>}

          {langWarning && <p className="text-xs text-amber-300/80">⚠️ {langWarning}</p>}

          <ExpertOnly>
          {status?.openvino_available && (
            <FieldRow label="Backend" hint={t("settings.llm.whisper_backend_hint")}>
              <Select value={backend} onValueChange={v => { if (v) setBackend(v); }}>
                <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="openvino">{t("settings.llm.whisper_openvino_select")}</SelectItem>
                  <SelectItem value="faster_whisper">{t("settings.llm.whisper_fw_select")}</SelectItem>
                </SelectContent>
              </Select>
            </FieldRow>
          )}
          {isOpenvino ? (
            <FieldRow label={t("settings.llm.whisper_modeldir_label")} hint={t("settings.llm.whisper_modeldir_hint")}>
              <input
                value={modelDir}
                onChange={e => { setModelDir(e.target.value); setModelDirEdited(true); }}
                placeholder="/kaare/models/voice/whisper-ov"
                className="flex h-9 w-80 max-w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
              />
            </FieldRow>
          ) : (<>
            <FieldRow label={t("settings.llm.stt_threads_label")} hint={t("settings.llm.stt_threads_hint")}>
              <Select value={String(cpuThreads)} onValueChange={v => { if (v) setCpuThreads(Number(v)); }}>
                <SelectTrigger className="w-64">
                  <SelectValue>{(v: string) => (v === "0" ? t("settings.llm.stt_threads_auto") : v)}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {CPU_THREAD_OPTIONS.map(n => (
                    <SelectItem key={n} value={String(n)}>{n === 0 ? t("settings.llm.stt_threads_auto") : String(n)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldRow>
            <FieldRow label={t("settings.llm.whisper_compute_label")} hint={t("settings.llm.whisper_compute_hint")}>
              <Select value={computeType} onValueChange={v => { if (v) setComputeType(v); }}>
                <SelectTrigger className="w-64">
                  <SelectValue>{(v: string) => t(WHISPER_COMPUTE_OPTIONS.find(o => o.value === v)?.labelKey ?? "", { defaultValue: v })}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {WHISPER_COMPUTE_OPTIONS.map(o => (
                    <SelectItem key={o.value} value={o.value}>{t(o.labelKey)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldRow>
          </>)}
          </ExpertOnly>

          <div className="flex items-center gap-3 flex-wrap">
            <Button onClick={save} disabled={ss.state === "saving"} size="sm">
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ss.state} />
            <ExpertOnly><>
              <Button onClick={restart} disabled={restarting} size="sm" variant="outline">
                {restarting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
                {t("settings.llm.restart_service")}
              </Button>
              {restartOk === true  && <span className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> {t("settings.llm.restart_restarted")}</span>}
              {restartOk === false && <span className="text-xs text-red-400 flex items-center gap-1"><XCircle className="h-3 w-3" /> {restartError || t("settings.llm.restart_generic_error")}</span>}
            </></ExpertOnly>
          </div>
          <ExpertOnly>
          <p className="text-xs text-muted-foreground">
            {t("settings.llm.restart_needed_hint")}
          </p>
          </ExpertOnly>
        </CardContent>
      )}
    </Card>
  );
}
