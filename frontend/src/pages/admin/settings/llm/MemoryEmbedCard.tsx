import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { CheckCircle2, XCircle, Loader2, ChevronDown, ChevronUp, Download } from "lucide-react";
import {
  apiGetMemoryEmbedStatus, apiPostMemoryEmbedActivate, apiPostMemoryEmbedDownload, apiPutMemoryEmbedBackend,
  type MemoryEmbedJob, type MemoryEmbedModel, type MemoryEmbedStatus,
} from "@/services/api";
import { ExpertOnly } from "../shared";
import { useSaveState } from "../useSaveState";

export type MemoryEmbedData = { enabled: boolean; model_dir: string };

// Small/medium/large first; the earlier default is only shown while it is in use or on disk.
const TIER_ORDER = ["small", "medium", "large", "legacy"] as const;

function formatMb(bytes: number) {
  return `${Math.round(bytes / 1_000_000)} MB`;
}

function JobLine({ job }: { job: MemoryEmbedJob }) {
  const { t } = useTranslation();
  if (job.status === "error") {
    return (
      <p className="text-xs text-red-400 flex items-center gap-1">
        <XCircle className="h-3 w-3 shrink-0" />
        {t(`settings.llm.memembed_err_${job.error}`, { defaultValue: t("settings.llm.memembed_err_generic") })}
        {job.detail ? <span className="font-mono opacity-70"> — {job.detail}</span> : null}
      </p>
    );
  }
  if (job.status === "done" && job.kind === "download") {
    return <p className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> {t("settings.llm.memembed_download_done")}</p>;
  }
  if (job.status === "done" && job.result) {
    return (
      <p className="text-xs text-green-400 flex items-center gap-1">
        <CheckCircle2 className="h-3 w-3 shrink-0" />
        {t("settings.llm.memembed_switch_done", { indexed: job.result.indexed, total: job.result.total })}
        {job.result.skipped_encrypted > 0 && (
          <span className="text-amber-300"> {t("settings.llm.memembed_switch_skipped", { n: job.result.skipped_encrypted })}</span>
        )}
      </p>
    );
  }
  let text = t(`settings.llm.memembed_phase_${job.phase}`, { defaultValue: job.phase });
  if (job.kind === "download" && job.total_bytes > 0) {
    text += ` ${formatMb(job.bytes)} / ${formatMb(job.total_bytes)} (${Math.min(100, Math.round((job.bytes / job.total_bytes) * 100))} %)`;
  }
  if (job.phase === "reindex" && job.progress.total) {
    text += ` ${job.progress.done ?? 0} / ${job.progress.total}`;
  }
  return <p className="text-xs text-muted-foreground flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" /> {text}</p>;
}

export function MemoryEmbedCard({ data, onSaved }: { data: MemoryEmbedData; onSaved: () => void }) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(true);
  const [enabled, setEnabled]     = useState(data.enabled);
  const [modelDir, setModelDir]   = useState(data.model_dir);
  // Only a folder the user typed is saved — otherwise «Save» would write back the folder from
  // when the page loaded and undo a model switch made since.
  const [modelDirEdited, setModelDirEdited] = useState(false);
  const [status, setStatus]       = useState<MemoryEmbedStatus | null>(null);
  const [confirmKey, setConfirmKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const ss = useSaveState();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const wasRunning = useRef(false);
  const refresh = useCallback(() => {
    apiGetMemoryEmbedStatus().then(st => {
      const nowRunning = st.job?.status === "running";
      if (wasRunning.current && !nowRunning) {
        // A finished switch changed the config: show the new folder and reload the page data.
        const active = st.models.find(m => m.active);
        if (active) setModelDir(active.model_dir);
        setModelDirEdited(false);
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

  const save = async () => {
    ss.saving();
    try {
      await apiPutMemoryEmbedBackend(modelDirEdited ? { enabled, model_dir: modelDir } : { enabled });
      setModelDirEdited(false);
      ss.saved();
      onSaved();
      refresh();
    } catch { ss.error(); }
  };

  const start = async (action: (m: string) => Promise<MemoryEmbedJob>, key: string) => {
    setActionError("");
    setConfirmKey(null);
    try {
      await action(key);
    } catch (e: unknown) {
      const code = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "";
      setActionError(t(`settings.llm.memembed_err_${code}`, { defaultValue: t("settings.llm.memembed_err_generic") }));
    }
    refresh();
  };

  const visible = (status?.models ?? [])
    .filter(m => m.tier !== "legacy" || m.active || m.downloaded)
    .sort((a, b) => TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier));

  const row = (m: MemoryEmbedModel) => {
    const viaService = m.backend === "embedding_service";
    const blocked = viaService && !status?.embedding_service_enabled;
    // Chosen in the config but not what the embed service runs (e.g. after an upgrade, or the
    // service is off): offer «Use this» so one click downloads → starts → indexes.
    const serving = m.active && (viaService ? !!status?.enabled : status?.enabled === true && status?.service_dim === m.dim);
    const canUse = m.downloaded && (!m.active || !serving);
    return (
      <div key={m.key} className={`px-3 py-2 space-y-1 ${m.active ? "bg-white/10" : ""}`}>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <span className="text-sm font-medium">{t(`settings.llm.memembed_tier_${m.tier}`)}</span>
            <span className="text-xs text-muted-foreground block">{t(`settings.llm.memembed_note_${m.key}`)}</span>
            <span className="text-xs text-muted-foreground font-mono">
              {viaService ? "BGE-M3" : m.repo.split("/")[1]} · {m.dim}-dim{m.size_mb ? ` · ${m.size_mb} MB` : ""}
              {m.points != null ? ` · ${t("settings.llm.memembed_points", { n: m.points })}` : ""}
            </span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {serving && <Badge className="bg-green-700 text-white text-xs">{t("settings.llm.memembed_active")}</Badge>}
            {m.active && !serving && <Badge variant="outline" className="text-xs text-amber-300">{t("settings.llm.memembed_selected")}</Badge>}
            {!m.active && !viaService && m.downloaded && <Badge variant="outline" className="text-xs">{t("settings.llm.memembed_downloaded")}</Badge>}
            {!viaService && !m.downloaded && (
              <Button size="sm" variant="outline" disabled={running} onClick={() => start(apiPostMemoryEmbedDownload, m.key)}>
                <Download className="h-3 w-3 mr-1" /> {t("settings.llm.memembed_download_btn")}
              </Button>
            )}
            {canUse && (
              <Button size="sm" disabled={running || blocked} onClick={() => setConfirmKey(m.key)}>
                {t("settings.llm.memembed_use_btn")}
              </Button>
            )}
          </div>
        </div>
        {blocked && <p className="text-xs text-amber-300/80">{t("settings.llm.memembed_needs_embedding")}</p>}
        {confirmKey === m.key && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200 space-y-2">
            <p>⚠️ {t("settings.llm.memembed_switch_warning")}</p>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => start(apiPostMemoryEmbedActivate, m.key)}>{t("settings.llm.memembed_switch_confirm")}</Button>
              <Button size="sm" variant="outline" onClick={() => setConfirmKey(null)}>{t("common.cancel")}</Button>
            </div>
          </div>
        )}
        {status?.job && status.job.model === m.key && <JobLine job={status.job} />}
      </div>
    );
  };

  return (
    <Card className={enabled ? "" : "opacity-60"}>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div
          className="flex items-center gap-3 cursor-pointer select-none flex-1"
          onClick={() => setCollapsed(v => !v)}
        >
          <CardTitle className="text-base">{t("settings.llm.memembed_title")}</CardTitle>
          <Badge variant="outline" className="text-xs font-mono">port 11500</Badge>
          <span className="text-xs text-muted-foreground">{enabled ? t("common.enabled") : t("common.disabled")}</span>
        </div>
        <div className="flex items-center gap-3">
          <Switch
            checked={enabled}
            onCheckedChange={v => setEnabled(v)}
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
          <p className="text-xs text-muted-foreground">{t("settings.llm.memembed_intro2")}</p>

          {status === null
            ? <p className="text-xs text-muted-foreground flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" /> {t("common.loading")}</p>
            : <div className="rounded-md border border-white/10 divide-y divide-white/5">{visible.map(row)}</div>}
          {actionError && <p className="text-xs text-red-400">{actionError}</p>}

          <ExpertOnly><>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">{t("settings.llm.memembed_model_dir_label")}</Label>
            <input
              value={modelDir}
              onChange={e => { setModelDir(e.target.value); setModelDirEdited(true); }}
              placeholder={status?.models.find(m => m.active)?.model_dir ?? ""}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-xs shadow-sm font-mono"
            />
            <p className="text-xs text-muted-foreground">{t("settings.llm.memembed_model_dir_hint")}</p>
          </div>
          </></ExpertOnly>
          <div className="flex items-center gap-3">
            <Button size="sm" onClick={save} disabled={ss.state === "saving"}>
              {ss.state === "saving" ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
              {t("common.save")}
            </Button>
            {ss.state === "saved"  && <span className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> {t("common.saved")}</span>}
            {ss.state === "error"  && <span className="text-xs text-red-400 flex items-center gap-1"><XCircle className="h-3 w-3" /> {t("common.error")}</span>}
          </div>
          <ExpertOnly>
          <p className="text-xs text-muted-foreground">
            {t("settings.llm.memembed_autostart_hint")}
          </p>
          </ExpertOnly>
        </CardContent>
      )}
    </Card>
  );
}
