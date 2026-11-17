// Plex connection card (URL, timeout, token, listener status). Split out of the old combined Settings →
// Integrations tab (P144-s01) — self-contained so it can move as one block (I21).
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Label } from "@/components/ui/label";
import { Loader2 } from "lucide-react";
import { apiGetServices, apiPutPlex, apiGetPlexToken, apiPutPlexToken, apiGetPlexStatus, type PlexListenerState } from "@/services/api";
import { FieldRow, SaveFeedback, TestButton, MaskedInput, ExpertOnly } from "../settings/shared";
import { useSaveState } from "../settings/useSaveState";

export default function PlexCard() {
  const { t } = useTranslation();
  const [plexUrl, setPlexUrl]           = useState("");
  const [plexTimeout, setPlexTimeout]   = useState("10");
  const [plexToken, setPlexToken]       = useState("");
  const [plexTokenInfo, setPlexTokenInfo] = useState<{ is_set: boolean; masked: string } | null>(null);
  const ssPlex    = useSaveState();
  const ssPlexTok = useSaveState();
  const [plexState, setPlexState] = useState<PlexListenerState | null>(null);

  // P140: the listener's connection state; poll gently so a saved URL/token shows its effect
  useEffect(() => {
    let alive = true;
    const load = () => apiGetPlexStatus().then(d => { if (alive) setPlexState(d.state); }).catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    apiGetServices().then(d => {
      setPlexUrl(d.plex?.url ?? "");
      setPlexTimeout(String(d.plex?.timeout ?? 10));
    }).catch(() => {});
    apiGetPlexToken().then(d => setPlexTokenInfo(d)).catch(() => {});
  }, []);

  const savePlex = async () => {
    ssPlex.saving();
    try {
      const saveRes = await apiPutPlex({ url: plexUrl, timeout: Number(plexTimeout) });
      ssPlex.saved(saveRes?.applies);
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
              <Input value={plexUrl} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPlexUrl(e.target.value)} placeholder="http://192.168.0.x:32400" />
              <TestButton url={plexUrl} />
            </div>
          </FieldRow>
          <ExpertOnly>
          <FieldRow label={t("settings.integrasjoner.plex.timeout_label")} hint={t("settings.integrasjoner.plex.timeout_hint")}>
            <Input type="number" value={plexTimeout} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPlexTimeout(e.target.value)} className="w-24" />
          </FieldRow>
          </ExpertOnly>
        </div>
        <div className="flex items-center gap-3 mt-4">
          <Button onClick={savePlex} disabled={ssPlex.state === "saving"}>
            {ssPlex.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t("common.save")}
          </Button>
          <SaveFeedback state={ssPlex.state} applies={ssPlex.applies} />
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

        {plexState && (
          <p className="text-xs mt-4" data-testid="plex-status">
            <span className="text-muted-foreground">{t("settings.integrasjoner.plex.status_label")}: </span>
            <span className={
              plexState === "connected" ? "text-emerald-400"
                : plexState === "token_rejected" || plexState === "unreachable" ? "text-red-400"
                : "text-muted-foreground"
            }>
              {t(`settings.integrasjoner.plex.status_${plexState}`)}
            </span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
