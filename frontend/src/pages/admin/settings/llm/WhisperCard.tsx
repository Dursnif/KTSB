import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CheckCircle2, XCircle, Loader2, RotateCcw, ChevronDown, ChevronUp } from "lucide-react";
import { apiPutVoiceBackend, apiRestartService } from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback, ExpertOnly } from "../shared";

export type VoiceServicesData = {
  stt_backend: string;
  faster_whisper_model: string;
  compute_type: string;
  stt_enabled: boolean;
  model_dir: string;
};

const WHISPER_FW_PRESETS: { model: string; size: string; noteKey: string }[] = [
  { model: "large-v3",       size: "~3 GB",    noteKey: "settings.llm.whisper_fw_preset_best" },
  { model: "large-v3-turbo", size: "~1.6 GB",  noteKey: "settings.llm.whisper_fw_preset_faster" },
  { model: "medium",         size: "~1.5 GB",  noteKey: "settings.llm.whisper_fw_preset_balanced" },
  { model: "small",          size: "~0.5 GB",  noteKey: "settings.llm.whisper_fw_preset_fast_less_accurate" },
];

const WHISPER_OV_PRESETS: { model_dir: string; labelKey: string; size: string; noteKey: string }[] = [
  {
    model_dir: "/mnt/ai_disk/models/voice/nb-whisper-large-ov",
    labelKey: "settings.llm.whisper_ov_preset_label",
    size: "~1.5 GB",
    noteKey: "settings.llm.whisper_ov_preset_note",
  },
];

const WHISPER_COMPUTE_OPTIONS = [
  { value: "int8",          labelKey: "settings.llm.whisper_compute_int8" },
  { value: "float16",       labelKey: "settings.llm.whisper_compute_float16" },
  { value: "int8_float16",  labelKey: "settings.llm.whisper_compute_int8_float16" },
  { value: "float32",       labelKey: "settings.llm.whisper_compute_float32" },
];

// STT language is driven by kare_language alone (P101) — no separate switch here. This
// heuristic is model-driven and symmetric: it asks "which languages does THIS model support"
// (from name/path pattern), not "is the selected language Norwegian". No language is an
// implicit default here — nb is treated the same as en/de/anything else. Produces only a soft
// warning; never changes actual STT behavior.
function _sttModelSupportedLangs(backend: string, modelDir: string, fwModel: string): string[] | "all" {
  const modelRef = (backend === "openvino" ? modelDir : fwModel).toLowerCase();
  if (modelRef.includes("nb-whisper")) {
    return ["nb"]; // NbAiLab specialization (also nn, but nn isn't a kare_language option)
  }
  if (modelRef.endsWith(".en")) {
    return ["en"]; // English-only Whisper distill
  }
  return "all"; // standard multilingual Whisper (large-v3 etc.) — no known restriction
}


export function WhisperCard({ data, kareLanguage, onSaved }: { data: VoiceServicesData; kareLanguage: string; onSaved: () => void }) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed]     = useState(true);
  const [sttEnabled, setSttEnabled]   = useState(data.stt_enabled);
  const [backend, setBackend]         = useState(data.stt_backend);
  const [model, setModel]             = useState(data.faster_whisper_model);
  const [modelDir, setModelDir]       = useState(data.model_dir);
  const [computeType, setComputeType] = useState(data.compute_type);
  const [restarting, setRestarting]   = useState(false);
  const [restartOk, setRestartOk]     = useState<boolean | null>(null);
  const [restartError, setRestartError] = useState("");
  const ss = useSaveState();

  const isOpenvino = backend === "openvino";
  const supportedLangs = _sttModelSupportedLangs(backend, modelDir, model);
  const langWarning = supportedLangs === "all" || supportedLangs.includes(kareLanguage)
    ? null
    : t("settings.llm.whisper_lang_warning", {
        model: isOpenvino ? (modelDir.split("/").pop() || modelDir) : model,
        supported: supportedLangs.join("/"),
        lang: kareLanguage,
      });

  const save = async () => {
    ss.saving();
    try {
      await apiPutVoiceBackend({
        stt_backend:          backend,
        faster_whisper_model: model,
        compute_type:         computeType,
        stt_enabled:          sttEnabled,
        model_dir:            modelDir,
      });
      ss.saved();
      onSaved();
    } catch { ss.error(); }
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

  return (
    <Card className={sttEnabled ? "" : "opacity-60"}>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div
          className="flex items-center gap-3 cursor-pointer select-none flex-1"
          onClick={() => setCollapsed(v => !v)}
        >
          <CardTitle className="text-base">Whisper (Tale-til-tekst)</CardTitle>
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
        <CardContent className="space-y-5 pt-0">
          <ExpertOnly>
          <FieldRow label="Backend" hint={t("settings.llm.whisper_backend_hint")}>
            <Select value={backend} onValueChange={v => { if (v) setBackend(v); }}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="openvino">{t("settings.llm.whisper_openvino_select")}</SelectItem>
                <SelectItem value="faster_whisper">{t("settings.llm.whisper_fw_select")}</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>
          </ExpertOnly>

          {isOpenvino ? (
            <>
              <ExpertOnly>
              <FieldRow label={t("settings.llm.whisper_modeldir_label")} hint={t("settings.llm.whisper_modeldir_hint")}>
                <input
                  value={modelDir}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelDir(e.target.value)}
                  placeholder="/mnt/ai_disk/models/voice/nb-whisper-large-ov"
                  className="flex h-9 w-80 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                />
              </FieldRow>
              </ExpertOnly>
              <div className="rounded-md border border-white/10 divide-y divide-white/5 text-sm">
                {WHISPER_OV_PRESETS.map(p => (
                  <button
                    key={p.model_dir}
                    type="button"
                    onClick={() => setModelDir(p.model_dir)}
                    className={`w-full flex items-start justify-between px-3 py-2 text-left hover:bg-white/5 transition-colors ${modelDir === p.model_dir ? "bg-white/10" : ""}`}
                  >
                    <div>
                      <span className="font-mono text-xs text-foreground block">{t(p.labelKey)}</span>
                      <span className="text-xs text-muted-foreground font-mono">{p.model_dir}</span>
                    </div>
                    <span className="flex gap-4 text-xs text-muted-foreground shrink-0 ml-3 mt-0.5">
                      <span>{p.size}</span>
                      <span className="text-green-400">{t(p.noteKey)}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-amber-300/80">
                ℹ️ {t("settings.llm.whisper_ov_disclaimer", {
                  model: "NbAiLab/nb-whisper-large",
                  tokenNo: "<|no|>",
                  tokenNn: "<|nn|>",
                  tokenNb: "<|nb|>",
                })}
              </p>
            </>
          ) : (
            <>
              <FieldRow label={t("settings.llm.whisper_model_label")} hint={t("settings.llm.whisper_model_hint")}>
                <input
                  value={model}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModel(e.target.value)}
                  placeholder="large-v3"
                  className="flex h-9 w-64 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                />
              </FieldRow>
              <div className="rounded-md border border-white/10 divide-y divide-white/5 text-sm">
                {WHISPER_FW_PRESETS.map(p => (
                  <button
                    key={p.model}
                    type="button"
                    onClick={() => setModel(p.model)}
                    className={`w-full flex items-center justify-between px-3 py-2 text-left hover:bg-white/5 transition-colors ${model === p.model ? "bg-white/10" : ""}`}
                  >
                    <span className="font-mono text-xs text-foreground">{p.model}</span>
                    <span className="flex gap-4 text-xs text-muted-foreground">
                      <span>{p.size}</span>
                      <span>{t(p.noteKey)}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                <strong>{t("settings.llm.whisper_fw_norsk_optimized_title")}</strong>{" "}
                {t("settings.llm.whisper_fw_norsk_optimized_body", {
                  model: "NbAiLab/nb-whisper-large",
                  cmd: "ct2-transformers-converter --model NbAiLab/nb-whisper-large --output_dir /path/to/model",
                })}
              </p>
              <ExpertOnly>
              <FieldRow label={t("settings.llm.whisper_compute_label")} hint={t("settings.llm.whisper_compute_hint")}>
                <Select value={computeType} onValueChange={v => { if (v) setComputeType(v); }}>
                  <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {WHISPER_COMPUTE_OPTIONS.map(o => (
                      <SelectItem key={o.value} value={o.value}>{t(o.labelKey)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FieldRow>
              </ExpertOnly>
            </>
          )}

          <p className="text-xs text-muted-foreground">
            {t("settings.llm.whisper_lang_note")}
          </p>
          {langWarning && (
            <p className="text-xs text-amber-300/80">⚠️ {langWarning}</p>
          )}

          <div className="flex items-center gap-3 flex-wrap">
            <Button onClick={save} disabled={ss.state === "saving"} size="sm">
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Lagre
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
