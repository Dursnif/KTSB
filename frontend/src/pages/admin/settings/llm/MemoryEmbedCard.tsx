import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { CheckCircle2, XCircle, Loader2, ChevronDown, ChevronUp } from "lucide-react";
import { apiPutMemoryEmbedBackend } from "@/services/api";
import { useSaveState, ExpertOnly } from "../shared";

export type MemoryEmbedData = { enabled: boolean; model_dir: string };

export function MemoryEmbedCard({ data, onSaved }: { data: MemoryEmbedData; onSaved: () => void }) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(true);
  const [enabled, setEnabled]     = useState(data.enabled);
  const [modelDir, setModelDir]   = useState(data.model_dir);
  const ss = useSaveState();

  const save = async () => {
    ss.saving();
    try {
      await apiPutMemoryEmbedBackend({ enabled, model_dir: modelDir });
      ss.saved();
      onSaved();
    } catch { ss.error(); }
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
        <CardContent className="space-y-5 pt-0">
          <ExpertOnly><>
          <div className="rounded-md border border-blue-500/20 bg-blue-500/5 px-3 py-2 text-xs text-blue-300/90">
            {t("settings.llm.memembed_intro", { model: "paraphrase-multilingual-MiniLM-L12-v2" })}{" "}
            {t("settings.llm.memembed_files")} <code>model.onnx</code>, <code>tokenizer.json</code>
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">{t("settings.llm.memembed_model_dir_label")}</Label>
            <input
              value={modelDir}
              onChange={e => setModelDir(e.target.value)}
              placeholder="/kaare/state/models/semantic-embed"
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-xs shadow-sm font-mono"
            />
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
