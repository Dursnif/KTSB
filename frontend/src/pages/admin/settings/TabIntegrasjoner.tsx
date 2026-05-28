import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Loader2 } from "lucide-react";
import { apiGetServices, apiPutFrigate, apiPutPlex, apiGetPlexToken, apiPutPlexToken } from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback, TestButton, MaskedInput } from "./shared";

export default function TabIntegrasjoner() {
  const { t } = useTranslation();
  const [frigateUrl, setFrigateUrl]     = useState("");
  const [frigateTimeout, setFrigateTimeout] = useState("10");
  const [frigateSnap, setFrigateSnap]   = useState("5");
  const [frigateEnabled, setFrigateEnabled] = useState(false);
  const ssFrigate = useSaveState();

  const [plexUrl, setPlexUrl]           = useState("");
  const [plexTimeout, setPlexTimeout]   = useState("10");
  const [plexToken, setPlexToken]       = useState("");
  const [plexTokenInfo, setPlexTokenInfo] = useState<{ is_set: boolean; masked: string } | null>(null);
  const ssPlex    = useSaveState();
  const ssPlexTok = useSaveState();

  useEffect(() => {
    apiGetServices().then(d => {
      setFrigateUrl(d.frigate?.url ?? "");
      setFrigateTimeout(String(d.frigate?.timeout ?? 10));
      setFrigateSnap(String(d.frigate?.snapshot_timeout ?? 5));
      setFrigateEnabled(d.frigate?.enabled ?? false);
      setPlexUrl(d.plex?.url ?? "");
      setPlexTimeout(String(d.plex?.timeout ?? 10));
    }).catch(() => {});
    apiGetPlexToken().then(d => setPlexTokenInfo(d)).catch(() => {});
  }, []);

  const saveFrigate = async () => {
    ssFrigate.saving();
    try {
      await apiPutFrigate({ url: frigateUrl, timeout: Number(frigateTimeout), snapshot_timeout: Number(frigateSnap), enabled: frigateEnabled });
      ssFrigate.saved();
    } catch { ssFrigate.error(); }
  };

  const savePlex = async () => {
    ssPlex.saving();
    try {
      await apiPutPlex({ url: plexUrl, timeout: Number(plexTimeout) });
      ssPlex.saved();
    } catch { ssPlex.error(); }
  };

  const savePlexToken = async () => {
    ssPlexTok.saving();
    try {
      await apiPutPlexToken(plexToken);
      setPlexToken("");
      const info = await apiGetPlexToken();
      setPlexTokenInfo(info);
      ssPlexTok.saved();
    } catch { ssPlexTok.error(); }
  };

  return (
    <div className="space-y-6">
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
                <Switch checked={frigateEnabled} onCheckedChange={setFrigateEnabled} className="data-checked:bg-green-600 data-unchecked:bg-red-600" />
                <span className="text-sm text-muted-foreground">{frigateEnabled ? t("common.enabled") : t("common.disabled")}</span>
              </div>
            </FieldRow>
            <FieldRow label={t("settings.integrasjoner.frigate.url_label")} hint={t("settings.integrasjoner.frigate.url_hint")}>
              <div className="flex gap-2">
                <Input value={frigateUrl} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFrigateUrl(e.target.value)} placeholder="http://192.168.0.100:5000" />
                <TestButton url={frigateUrl} />
              </div>
            </FieldRow>
            <FieldRow label={t("settings.integrasjoner.frigate.timeout_label")} hint={t("settings.integrasjoner.frigate.timeout_hint")}>
              <Input type="number" value={frigateTimeout} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFrigateTimeout(e.target.value)} className="w-24" />
            </FieldRow>
            <FieldRow label={t("settings.integrasjoner.frigate.snap_label")} hint={t("settings.integrasjoner.frigate.snap_hint")}>
              <Input type="number" value={frigateSnap} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFrigateSnap(e.target.value)} className="w-24" />
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveFrigate} disabled={ssFrigate.state === "saving"}>
              {ssFrigate.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssFrigate.state} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>{t("settings.integrasjoner.plex.title")}</CardTitle>
              <CardDescription>{t("settings.integrasjoner.plex.description")}</CardDescription>
            </div>
            <Badge variant="outline" className="text-muted-foreground">{t("common.optional")}</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.integrasjoner.plex.url_label")} hint={t("settings.integrasjoner.plex.url_hint")}>
              <div className="flex gap-2">
                <Input value={plexUrl} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPlexUrl(e.target.value)} placeholder="http://192.168.0.156:32400" />
                <TestButton url={plexUrl} />
              </div>
            </FieldRow>
            <FieldRow label={t("settings.integrasjoner.plex.timeout_label")} hint={t("settings.integrasjoner.plex.timeout_hint")}>
              <Input type="number" value={plexTimeout} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPlexTimeout(e.target.value)} className="w-24" />
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={savePlex} disabled={ssPlex.state === "saving"}>
              {ssPlex.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssPlex.state} />
          </div>

          <Separator className="my-5" />

          <div className="space-y-2">
            <Label className="text-sm font-medium">{t("settings.integrasjoner.plex.token_label")}</Label>
            {plexTokenInfo && (
              <p className="text-xs text-muted-foreground mb-2">
                {plexTokenInfo.is_set ? t("common.set_masked", { masked: plexTokenInfo.masked }) : t("common.not_set")}
              </p>
            )}
            <MaskedInput value={plexToken} onChange={setPlexToken} placeholder={t("settings.integrasjoner.plex.token_placeholder")} />
            <p className="text-xs text-amber-400 mt-1">{t("settings.integrasjoner.plex.token_warning")}</p>
            <div className="flex items-center gap-3 mt-2">
              <Button onClick={savePlexToken} disabled={!plexToken || ssPlexTok.state === "saving"} size="sm">
                {ssPlexTok.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {t("common.update_token")}
              </Button>
              <SaveFeedback state={ssPlexTok.state} />
            </div>
          </div>
        </CardContent>
      </Card>

    </div>
  );
}
