import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import { apiGetModelsDir, apiPutModelsDir } from "@/services/api";
import { FieldRow, SaveFeedback } from "../shared";
import { useSaveState } from "../useSaveState";

/** Root folder for models downloaded from the GUI (settings.yaml models_dir). Expert only. */
export function ModelStorageCard() {
  const { t } = useTranslation();
  const [dir, setDir] = useState("");
  const ss = useSaveState();

  useEffect(() => { apiGetModelsDir().then(r => setDir(r.models_dir)).catch(() => {}); }, []);

  const save = async () => {
    ss.saving();
    try { await apiPutModelsDir(dir.trim()); ss.saved(); } catch { ss.error(); }
  };

  return (
    <Card>
      <CardHeader className="py-3"><CardTitle className="text-base">{t("settings.llm.models_dir_title")}</CardTitle></CardHeader>
      <CardContent className="pt-0">
        <FieldRow label={t("settings.llm.models_dir_label")} hint={t("settings.llm.models_dir_hint")}>
          <div className="flex items-center gap-3 flex-wrap">
            <input
              value={dir}
              onChange={e => setDir(e.target.value)}
              placeholder="/kaare/models"
              className="flex h-9 w-80 max-w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
            />
            <Button size="sm" onClick={save} disabled={ss.state === "saving" || !dir.trim()}>
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ss.state} />
          </div>
        </FieldRow>
      </CardContent>
    </Card>
  );
}
