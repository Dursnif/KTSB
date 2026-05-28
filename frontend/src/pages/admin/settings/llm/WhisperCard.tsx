import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CheckCircle2, XCircle, Loader2, RotateCcw, ChevronDown, ChevronUp } from "lucide-react";
import { apiPutVoiceBackend, apiRestartService } from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback } from "../shared";

export type VoiceServicesData = {
  stt_backend: string;
  faster_whisper_model: string;
  compute_type: string;
  language: string;
  stt_enabled: boolean;
  model_dir: string;
};

const WHISPER_FW_PRESETS: { model: string; size: string; note: string }[] = [
  { model: "large-v3",       size: "~3 GB",    note: "Beste kvalitet (OpenAI)" },
  { model: "large-v3-turbo", size: "~1.6 GB",  note: "Raskere, nesten like god" },
  { model: "medium",         size: "~1.5 GB",  note: "God balanse" },
  { model: "small",          size: "~0.5 GB",  note: "Rask, lavere nøyaktighet" },
];

const WHISPER_OV_PRESETS: { model_dir: string; label: string; size: string; note: string }[] = [
  {
    model_dir: "/mnt/ai_disk/models/voice/nb-whisper-large-ov",
    label: "NbAiLab/nb-whisper-large (OpenVINO IR, konvertert)",
    size: "~1.5 GB",
    note: "Norsk (Nasjonalbiblioteket) — brukes nå",
  },
];

const WHISPER_COMPUTE_OPTIONS = [
  { value: "int8",          label: "int8 – standard (anbefalt)" },
  { value: "float16",       label: "float16 – høy presisjon (GPU)" },
  { value: "int8_float16",  label: "int8_float16 – rask på GPU" },
  { value: "float32",       label: "float32 – CPU-vennlig" },
];

const WHISPER_LANGUAGE_OPTIONS = [
  { value: "no",   label: "Norsk (no) — anbefalt for NbAiLab-modellen" },
  { value: "nn",   label: "Nynorsk (nn)" },
  { value: "auto", label: "Auto-detect" },
  { value: "en",   label: "English (en)" },
];

export function WhisperCard({ data, onSaved }: { data: VoiceServicesData; onSaved: () => void }) {
  const [collapsed, setCollapsed]     = useState(true);
  const [sttEnabled, setSttEnabled]   = useState(data.stt_enabled);
  const [backend, setBackend]         = useState(data.stt_backend);
  const [model, setModel]             = useState(data.faster_whisper_model);
  const [modelDir, setModelDir]       = useState(data.model_dir);
  const [computeType, setComputeType] = useState(data.compute_type);
  const [language, setLanguage]       = useState(data.language);
  const [restarting, setRestarting]   = useState(false);
  const [restartOk, setRestartOk]     = useState<boolean | null>(null);
  const ss = useSaveState();

  const isOpenvino = backend === "openvino";

  const save = async () => {
    ss.saving();
    try {
      await apiPutVoiceBackend({
        stt_backend:          backend,
        faster_whisper_model: model,
        compute_type:         computeType,
        language,
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
    try {
      const r = await apiRestartService("voice");
      setRestartOk(r.ok);
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
          <span className="text-xs text-muted-foreground">{sttEnabled ? "aktiv" : "deaktivert"}</span>
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
          <FieldRow label="Backend" hint="faster_whisper fungerer på alle plattformer (CPU/GPU). openvino krever Intel Arc GPU og OpenVINO GenAI — brukes med NbAiLab/nb-whisper-large.">
            <Select value={backend} onValueChange={v => { if (v) setBackend(v); }}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="openvino">OpenVINO GPU (Intel Arc) — nåværende</SelectItem>
                <SelectItem value="faster_whisper">CPU universell (faster-whisper)</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>

          {isOpenvino ? (
            <>
              <FieldRow label="Modellmappe (OpenVINO IR)" hint="Sti til ferdig konvertert OpenVINO IR-modell. Inneholder openvino_encoder_model.xml, openvino_decoder_model.xml osv.">
                <input
                  value={modelDir}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelDir(e.target.value)}
                  placeholder="/mnt/ai_disk/models/voice/nb-whisper-large-ov"
                  className="flex h-9 w-80 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                />
              </FieldRow>
              <div className="rounded-md border border-white/10 divide-y divide-white/5 text-sm">
                {WHISPER_OV_PRESETS.map(p => (
                  <button
                    key={p.model_dir}
                    type="button"
                    onClick={() => setModelDir(p.model_dir)}
                    className={`w-full flex items-start justify-between px-3 py-2 text-left hover:bg-white/5 transition-colors ${modelDir === p.model_dir ? "bg-white/10" : ""}`}
                  >
                    <div>
                      <span className="font-mono text-xs text-foreground block">{p.label}</span>
                      <span className="text-xs text-muted-foreground font-mono">{p.model_dir}</span>
                    </div>
                    <span className="flex gap-4 text-xs text-muted-foreground shrink-0 ml-3 mt-0.5">
                      <span>{p.size}</span>
                      <span className="text-green-400">{p.note}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-amber-300/80">
                ℹ️ <strong>NbAiLab/nb-whisper-large</strong> er OpenVINO IR-konvertert og lever lokalt på disk. Modellen støtter <code>&lt;|no|&gt;</code> og <code>&lt;|nn|&gt;</code> — <strong>ikke</strong> <code>&lt;|nb|&gt;</code>. Sett språk til <em>Norsk (no)</em>.
              </p>
            </>
          ) : (
            <>
              <FieldRow label="Modell" hint="HuggingFace-modellnavn eller sti til CTranslate2-konvertert modell. Lastes ned automatisk ved første oppstart.">
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
                      <span>{p.note}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                <strong>Norsk-optimalisert (NbAiLab):</strong> Bruk <code>NbAiLab/nb-whisper-large</code> med OpenVINO-backend (konvertert modell finnes allerede lokalt). For faster-whisper må den konverteres: <code className="bg-white/5 px-1 rounded">ct2-transformers-converter --model NbAiLab/nb-whisper-large --output_dir /sti/til/modell</code>
              </p>
              <FieldRow label="Beregningstype" hint="int8 anbefales for de fleste. float16/int8_float16 krever GPU. float32 fungerer på CPU uten spesial-maskinvare.">
                <Select value={computeType} onValueChange={v => { if (v) setComputeType(v); }}>
                  <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {WHISPER_COMPUTE_OPTIONS.map(o => (
                      <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FieldRow>
            </>
          )}

          <FieldRow label="Språk" hint="Norsk (no) er standard og anbefalt. NbAiLab-modellen støtter <|no|> og <|nn|> — ikke <|nb|>.">
            <Select value={language} onValueChange={v => { if (v) setLanguage(v); }}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                {WHISPER_LANGUAGE_OPTIONS.map(o => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FieldRow>

          <div className="flex items-center gap-3 flex-wrap">
            <Button onClick={save} disabled={ss.state === "saving"} size="sm">
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Lagre
            </Button>
            <Button onClick={restart} disabled={restarting} size="sm" variant="outline">
              {restarting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
              Restart tjeneste
            </Button>
            <SaveFeedback state={ss.state} />
            {restartOk === true  && <span className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> Restartet</span>}
            {restartOk === false && <span className="text-xs text-red-400 flex items-center gap-1"><XCircle className="h-3 w-3" /> Feil ved restart</span>}
          </div>
          <p className="text-xs text-muted-foreground">
            Restart er nødvendig for at endringer skal tre i kraft.
          </p>
        </CardContent>
      )}
    </Card>
  );
}
