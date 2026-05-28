import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CheckCircle2, XCircle, Loader2, RotateCcw, ChevronDown, ChevronUp } from "lucide-react";
import { apiPutEmbeddingBackend, apiRestartService } from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback } from "../shared";

export type EmbServicesData = {
  device: string;
  hf_model: string;
  model_path: string;
  emb_enabled: boolean;
};

const EMBEDDING_PRESETS: { model: string; dim: number; size: string; note: string }[] = [
  { model: "BAAI/bge-m3",                                                 dim: 1024, size: "~570 MB", note: "Standard (tett + spare) — brukes nå" },
  { model: "intfloat/multilingual-e5-large",                              dim: 1024, size: "~560 MB", note: "Alternativ multilingual" },
  { model: "sentence-transformers/paraphrase-multilingual-mpnet-base-v2", dim: 768,  size: "~280 MB", note: "Lettvekt alternativ" },
];

const KNOWN_DIMS: Record<string, number> = {
  "BAAI/bge-m3": 1024,
  "intfloat/multilingual-e5-large": 1024,
  "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": 768,
};

export function EmbeddingCard({ data, onSaved }: { data: EmbServicesData; onSaved: () => void }) {
  const [collapsed, setCollapsed]     = useState(true);
  const [embEnabled, setEmbEnabled]   = useState(data.emb_enabled);
  const [device, setDevice]           = useState(data.device);
  const [hfModel, setHfModel]         = useState(data.hf_model);
  const [modelPath, setModelPath]     = useState(data.model_path);
  const [restarting, setRestarting]   = useState(false);
  const [restartOk, setRestartOk]     = useState<boolean | null>(null);
  const ss = useSaveState();

  const isOpenvino = device === "NPU" || device === "CPU";
  const currentDim = KNOWN_DIMS[hfModel] ?? null;
  const defaultDim = KNOWN_DIMS[data.hf_model] ?? 1024;
  const dimChanged = currentDim !== null && currentDim !== defaultDim;

  const save = async () => {
    ss.saving();
    try {
      await apiPutEmbeddingBackend({
        device,
        hf_model:    hfModel,
        model_path:  isOpenvino ? modelPath : undefined,
        emb_enabled: embEnabled,
      });
      ss.saved();
      onSaved();
    } catch { ss.error(); }
  };

  const restart = async () => {
    setRestarting(true);
    setRestartOk(null);
    try {
      const r = await apiRestartService("embedding");
      setRestartOk(r.ok);
    } catch { setRestartOk(false); }
    finally { setRestarting(false); }
  };

  return (
    <Card className={embEnabled ? "" : "opacity-60"}>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div
          className="flex items-center gap-3 cursor-pointer select-none flex-1"
          onClick={() => setCollapsed(v => !v)}
        >
          <CardTitle className="text-base">BGE-M3 (Innvektinger)</CardTitle>
          <Badge variant="outline" className="text-xs font-mono">port 11446</Badge>
          <span className="text-xs text-muted-foreground">{embEnabled ? "aktiv" : "deaktivert"}</span>
        </div>
        <div className="flex items-center gap-3">
          <Switch
            checked={embEnabled}
            onCheckedChange={v => setEmbEnabled(v)}
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
          <div className="rounded-md border border-blue-500/20 bg-blue-500/5 px-3 py-2 text-xs text-blue-300/90">
            ℹ️ <strong>BAAI/bge-m3</strong> er konvertert til OpenVINO IR-format og lever lokalt på <code>/mnt/wiki/bge-m3-ov/</code>. Modellen brukes av NPU- og CPU-backend uten nettilgang.
          </div>

          <FieldRow label="Backend" hint="torch laster modellen automatisk fra HuggingFace og fungerer på AMD/Intel/Apple Silicon. OpenVINO krever ferdig konvertert IR-modell og Intel NPU/iGPU.">
            <Select value={device} onValueChange={v => { if (v) setDevice(v); }}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="NPU">OpenVINO NPU (Intel) — nåværende</SelectItem>
                <SelectItem value="CPU">OpenVINO CPU (Intel)</SelectItem>
                <SelectItem value="torch">CPU universell (torch, auto-nedlasting)</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>

          <FieldRow
            label="HuggingFace-modell"
            hint={isOpenvino ? "Modellnavn (brukes som referanse for tokenizer-lastning). For OpenVINO brukes IR-modellen i stien nedenfor." : "Modellnavn på HuggingFace. Lastes automatisk ned ved første oppstart."}
          >
            <input
              value={hfModel}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHfModel(e.target.value)}
              placeholder="BAAI/bge-m3"
              className="flex h-9 w-64 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
            />
          </FieldRow>
          <div className="rounded-md border border-white/10 divide-y divide-white/5 text-sm">
            {EMBEDDING_PRESETS.map(p => (
              <button
                key={p.model}
                type="button"
                onClick={() => setHfModel(p.model)}
                className={`w-full flex items-center justify-between px-3 py-2 text-left hover:bg-white/5 transition-colors ${hfModel === p.model ? "bg-white/10" : ""}`}
              >
                <span className="font-mono text-xs text-foreground truncate max-w-[18rem]">{p.model}</span>
                <span className="flex gap-3 text-xs text-muted-foreground shrink-0 ml-2">
                  <span className="font-mono">{p.dim}-dim</span>
                  <span>{p.size}</span>
                  <span className={p.note.includes("nå") ? "text-green-400" : ""}>{p.note}</span>
                </span>
              </button>
            ))}
          </div>

          {currentDim && (
            <p className="text-xs text-muted-foreground">
              Vektordimensjon for valgt modell: <strong>{currentDim}</strong>
            </p>
          )}

          {dimChanged && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              ⚠️ Du bytter fra <strong>{defaultDim}-dim</strong> til <strong>{currentDim}-dim</strong>. Qdrant-kolleksjonene <code>kaare_memory</code> og <code>wiki_no</code> må slettes og bygges om — dette sletter all lagret hukommelse.
            </div>
          )}

          {isOpenvino && (
            <FieldRow label="Lokal modellsti (OpenVINO IR)" hint="Sti til ferdig konvertert OpenVINO IR-modell. Inneholder openvino_model.xml og openvino_model.bin.">
              <input
                value={modelPath}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelPath(e.target.value)}
                placeholder="/mnt/wiki/bge-m3-ov"
                className="flex h-9 w-64 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
              />
            </FieldRow>
          )}

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
