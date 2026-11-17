import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loader2 } from "lucide-react";
import { apiGetScreens, apiPutScreens } from "@/services/api";
import { SaveFeedback, ExpertOnly } from "./shared";
import { useSaveState } from "./useSaveState";

// P151 (D11): Settings → Screens. One switch for KTSB's screen page (live camera and cards on TVs, Nest Hubs and
// browsers), how long a live camera runs, and Frigate's RTSP port (expert). Saved values take effect at once.
export default function TabSkjermer() {
  const { t } = useTranslation();
  const ss = useSaveState();
  const [enabled, setEnabled] = useState(false);
  const [minutes, setMinutes] = useState(2);
  const [port, setPort] = useState(8554);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGetScreens()
      .then(d => { setEnabled(d.enabled); setMinutes(d.live_minutes); setPort(d.rtsp_port); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    ss.saving();
    try {
      await apiPutScreens({ enabled, live_minutes: minutes, rtsp_port: port });
      ss.saved();
    } catch {
      ss.error();
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground p-4">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>{t("common.loading")}</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.screens.master.title")}</CardTitle>
          <CardDescription>{t("settings.screens.master.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            <Switch checked={enabled} onCheckedChange={setEnabled} id="screens-enabled" />
            <Label htmlFor="screens-enabled" className="cursor-pointer">{t("settings.screens.master.toggle")}</Label>
          </div>
        </CardContent>
      </Card>

      <Card style={{ opacity: enabled ? 1 : 0.5, pointerEvents: enabled ? "auto" : "none" }}>
        <CardHeader>
          <CardTitle>{t("settings.screens.live.title")}</CardTitle>
          <CardDescription>{t("settings.screens.live.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <Label htmlFor="screens-minutes">{t("settings.screens.live.minutes_label")}</Label>
          <Input id="screens-minutes" type="number" min={0.5} max={240} step={0.5} value={minutes}
                 onChange={e => setMinutes(parseFloat(e.target.value) || 2)} style={{ maxWidth: 140 }} />
          <p className="text-xs text-muted-foreground">{t("settings.screens.live.minutes_hint")}</p>
        </CardContent>
      </Card>

      <ExpertOnly>
        <Card style={{ opacity: enabled ? 1 : 0.5, pointerEvents: enabled ? "auto" : "none" }}>
          <CardHeader>
            <CardTitle>{t("settings.screens.rtsp.title")}</CardTitle>
            <CardDescription>{t("settings.screens.rtsp.description")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Input type="number" min={1} max={65535} value={port}
                   onChange={e => setPort(parseInt(e.target.value, 10) || 8554)} style={{ maxWidth: 140 }} />
          </CardContent>
        </Card>
      </ExpertOnly>

      <div className="flex items-center gap-4">
        <Button onClick={save} disabled={ss.state === "saving"}>
          {ss.state === "saving" ? (<><Loader2 className="mr-2 h-4 w-4 animate-spin" />{t("common.saving")}</>) : t("common.save")}
        </Button>
        <SaveFeedback state={ss.state} />
      </div>
    </div>
  );
}
