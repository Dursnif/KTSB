import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loader2 } from "lucide-react";
import { apiGetNotifications, apiPutNotifications } from "@/services/api";
import { useSaveState, SaveFeedback } from "./shared";

export default function TabVarsler() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const ss = useSaveState();

  const [enabled, setEnabled] = useState(true);
  const [haEntity, setHaEntity] = useState("");
  const [minScore, setMinScore] = useState(0.5);
  const [minConf, setMinConf] = useState(0.0);
  const [cameras, setCameras] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGetNotifications()
      .then(d => {
        setEnabled(d.enabled ?? true);
        setHaEntity(d.ha_notify_entity ?? "");
        setMinScore(d.normalcy_min_score ?? 0.5);
        setMinConf(d.normalcy_min_confidence ?? 0.0);
        setCameras((d.normalcy_cameras ?? []).join(", "));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    ss.saving();
    try {
      const camList = cameras
        .split(",")
        .map(s => s.trim())
        .filter(Boolean);
      await apiPutNotifications({
        enabled,
        ha_notify_entity: haEntity,
        normalcy_min_score: minScore,
        normalcy_min_confidence: minConf,
        normalcy_cameras: camList,
      });
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

      {/* Master toggle */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.varsler.master.title")}</CardTitle>
          <CardDescription>{t("settings.varsler.master.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              id="notif-enabled"
            />
            <Label htmlFor="notif-enabled" className="cursor-pointer">
              {t("settings.varsler.master.toggle")}
            </Label>
          </div>
        </CardContent>
      </Card>

      {/* Push entity */}
      <Card style={{ opacity: enabled ? 1 : 0.5, pointerEvents: enabled ? "auto" : "none" }}>
        <CardHeader>
          <CardTitle>{t("settings.varsler.push.title")}</CardTitle>
          <CardDescription>{t("settings.varsler.push.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <Label>{t("settings.varsler.push.entity_label")}</Label>
          <Input
            value={haEntity}
            onChange={e => setHaEntity(e.target.value)}
            placeholder="notify.mobile_app_..."
          />
          <p className="text-xs text-muted-foreground">{t("settings.varsler.push.entity_hint")}</p>
        </CardContent>
      </Card>

      {/* Normalcy thresholds */}
      <Card style={{ opacity: enabled ? 1 : 0.5, pointerEvents: enabled ? "auto" : "none" }}>
        <CardHeader>
          <CardTitle>{t("settings.varsler.normalcy.title")}</CardTitle>
          <CardDescription>{t("settings.varsler.normalcy.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>{t("settings.varsler.normalcy.min_score_label")}</Label>
            <Input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={minScore}
              onChange={e => setMinScore(parseFloat(e.target.value) || 0)}
            />
          </div>
          <div className="space-y-2">
            <Label>{t("settings.varsler.normalcy.min_confidence_label")}</Label>
            <Input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={minConf}
              onChange={e => setMinConf(parseFloat(e.target.value) || 0)}
            />
          </div>
        </CardContent>
      </Card>

      {/* Camera filter */}
      <Card style={{ opacity: enabled ? 1 : 0.5, pointerEvents: enabled ? "auto" : "none" }}>
        <CardHeader>
          <CardTitle>{t("settings.varsler.normalcy.cameras_label")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Input
            value={cameras}
            onChange={e => setCameras(e.target.value)}
            placeholder={t("settings.varsler.normalcy.cameras_hint")}
          />
          <p className="text-xs text-muted-foreground">{t("settings.varsler.normalcy.cameras_hint")}</p>
        </CardContent>
      </Card>

      {/* Actions */}
      <div className="flex items-center gap-4">
        <Button onClick={save} disabled={ss.state === "saving"}>
          {ss.state === "saving" ? (
            <><Loader2 className="mr-2 h-4 w-4 animate-spin" />{t("common.saving")}</>
          ) : t("common.save")}
        </Button>
        <SaveFeedback state={ss.state} />
        <Button variant="outline" onClick={() => navigate("/admin/security")}>
          {t("settings.varsler.events_link")}
        </Button>
      </div>

    </div>
  );
}
