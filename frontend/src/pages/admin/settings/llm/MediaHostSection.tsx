import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2 } from "lucide-react";
import { apiGetMediaHost, apiPutMediaHost } from "@/services/api";
import { SaveFeedback } from "../shared";
import { useSaveState } from "../useSaveState";
import { suggestedMediaHost } from "@/lib/network";

// LAN address speakers (ESP32, Chromecast, HA media players …) fetch Kåre's audio from (services.yaml voice.media_host).
export function MediaHostSection() {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const [detected, setDetected] = useState("");
  const [inContainer, setInContainer] = useState(false);
  const [applied, setApplied] = useState<boolean | null>(null);
  const ss = useSaveState();

  useEffect(() => {
    apiGetMediaHost().then(d => {
      setValue(d.media_host);
      setDetected(d.detected);
      setInContainer(d.in_container);
    }).catch(() => {});
  }, []);

  const suggestion = suggestedMediaHost() || detected;

  const save = async () => {
    ss.saving();
    try {
      const r = await apiPutMediaHost(value.trim());
      setApplied(r.applied);
      ss.saved();
    } catch { ss.error(); }
  };

  return (
    <div className="space-y-2 rounded-md border border-white/10 p-3">
      <p className="text-sm font-medium text-foreground">{t("settings.llm.media_host_label")}</p>
      <p className="text-xs text-muted-foreground">
        {inContainer ? t("settings.llm.media_host_hint_docker") : t("settings.llm.media_host_hint", { detected: detected || "—" })}
      </p>
      <div className="flex gap-2 flex-wrap">
        <Input value={value} onChange={e => setValue(e.target.value)} placeholder="192.168.0.x" className="max-w-xs" />
        {suggestion && suggestion !== value && (
          <Button size="sm" variant="outline" onClick={() => setValue(suggestion)}>
            {t("settings.llm.media_host_use", { host: suggestion })}
          </Button>
        )}
        <Button size="sm" onClick={save} disabled={ss.state === "saving"}>
          {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {t("common.save")}
        </Button>
        <SaveFeedback state={ss.state} />
      </div>
      {applied === false && ss.state === "saved" && (
        <p className="text-xs text-muted-foreground">{t("settings.llm.media_host_not_applied")}</p>
      )}
    </div>
  );
}
