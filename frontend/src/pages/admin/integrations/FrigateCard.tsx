// Frigate connection card (on/off, URL, timeouts). Split out of the old combined Settings → Integrations tab
// (P144-s01) — self-contained so it can move as one block (I21).
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Loader2 } from "lucide-react";
import { apiGetServices, apiPutFrigate } from "@/services/api";
import { FieldRow, SaveFeedback, TestButton, ExpertOnly } from "../settings/shared";
import { useSaveState } from "../settings/useSaveState";

export default function FrigateCard() {
  const { t } = useTranslation();
  const [frigateUrl, setFrigateUrl]     = useState("");
  const [frigateTimeout, setFrigateTimeout] = useState("10");
  const [frigateSnap, setFrigateSnap]   = useState("5");
  const [frigateEnabled, setFrigateEnabled] = useState(false);
  const [topicPrefix, setTopicPrefix] = useState("frigate");  // P144-s19: moved here from the MQTT card
  const [topicKnown, setTopicKnown] = useState(false);  // an API from before s19 has no such field
  const ssFrigate = useSaveState();

  useEffect(() => {
    apiGetServices().then(d => {
      setFrigateUrl(d.frigate?.url ?? "");
      setFrigateTimeout(String(d.frigate?.timeout ?? 10));
      setFrigateSnap(String(d.frigate?.snapshot_timeout ?? 5));
      setFrigateEnabled(d.frigate?.enabled ?? false);
      setTopicPrefix(d.frigate?.mqtt_topic_prefix ?? "frigate");
      setTopicKnown(d.frigate?.mqtt_topic_prefix !== undefined);
    }).catch(() => {});
  }, []);

  const saveFrigate = async () => {
    ssFrigate.saving();
    try {
      const saveRes = await apiPutFrigate({ url: frigateUrl, timeout: Number(frigateTimeout), snapshot_timeout: Number(frigateSnap), enabled: frigateEnabled,
        ...(topicKnown ? { mqtt_topic_prefix: topicPrefix.trim() || "frigate" } : {}) });
      ssFrigate.saved(saveRes?.applies);
    } catch { ssFrigate.error(); }
  };


  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>{t("settings.integrasjoner.frigate.title")}</CardTitle>
            <CardDescription>{t("settings.integrasjoner.frigate.description")}</CardDescription>
          </div>
          <Badge variant="outline" className="text-muted-foreground">{t("common.optional")}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="divide-y divide-border">
          <FieldRow label={t("settings.integrasjoner.frigate.enabled_label")} hint={t("settings.integrasjoner.frigate.enabled_hint")}>
            <div className="flex items-center gap-2">
              <Switch checked={frigateEnabled} onCheckedChange={setFrigateEnabled} />
              <span className="text-sm text-muted-foreground">{frigateEnabled ? t("common.enabled") : t("common.disabled")}</span>
            </div>
          </FieldRow>
          <FieldRow label={t("settings.integrasjoner.frigate.url_label")} hint={t("settings.integrasjoner.frigate.url_hint")}>
            <div className="flex gap-2">
              <Input value={frigateUrl} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFrigateUrl(e.target.value)} placeholder="http://192.168.0.x:5000" />
              <TestButton url={frigateUrl} />
            </div>
          </FieldRow>
          <ExpertOnly><>
          <FieldRow label={t("settings.integrasjoner.frigate.timeout_label")} hint={t("settings.integrasjoner.frigate.timeout_hint")}>
            <Input type="number" value={frigateTimeout} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFrigateTimeout(e.target.value)} className="w-24" />
          </FieldRow>
          <FieldRow label={t("settings.integrasjoner.frigate.snap_label")} hint={t("settings.integrasjoner.frigate.snap_hint")}>
            <Input type="number" value={frigateSnap} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFrigateSnap(e.target.value)} className="w-24" />
          </FieldRow>
          {topicKnown && <FieldRow label={t("settings.integrasjoner.frigate.topic_label")} hint={t("settings.integrasjoner.frigate.topic_hint")}>
            <Input value={topicPrefix} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTopicPrefix(e.target.value)} placeholder="frigate" className="w-48" />
          </FieldRow>}
          </></ExpertOnly>
        </div>
        <div className="flex items-center gap-3 mt-4">
          <Button onClick={saveFrigate} disabled={ssFrigate.state === "saving"}>
            {ssFrigate.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t("common.save")}
          </Button>
          <SaveFeedback state={ssFrigate.state} applies={ssFrigate.applies} />
        </div>
      </CardContent>
    </Card>
  );
}
