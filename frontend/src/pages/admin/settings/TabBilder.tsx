import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import { apiGetImageSettings, apiPutImageSettings, apiGetImageStats, type ImageSettings, type ImageUserStats } from "@/services/api";
import { useSaveState, SaveFeedback, FieldRow } from "./shared";
import Cameras from "../Cameras";

export default function TabBilder() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<ImageSettings>({ max_per_user_count: 500, max_per_user_mb: 200 });
  const [stats, setStats] = useState<ImageUserStats[]>([]);
  const [loading, setLoading] = useState(true);
  const ss = useSaveState();

  useEffect(() => {
    Promise.all([apiGetImageSettings(), apiGetImageStats()])
      .then(([s, st]) => { setCfg(s); setStats(st); })
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    ss.saving();
    try { await apiPutImageSettings(cfg); ss.saved(); }
    catch { ss.error(); }
  };

  if (loading) return <div className="text-muted-foreground text-sm">{t("common.loading")}</div>;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.bilder.storage.title")}</CardTitle>
          <CardDescription>{t("settings.bilder.storage.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.bilder.storage.count_label")} hint={t("settings.bilder.storage.count_hint")}>
              <Input
                type="number"
                min={10}
                value={cfg.max_per_user_count}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setCfg(p => ({ ...p, max_per_user_count: Number(e.target.value) }))}
                className="w-28"
              />
            </FieldRow>
            <FieldRow label={t("settings.bilder.storage.mb_label")} hint={t("settings.bilder.storage.mb_hint")}>
              <Input
                type="number"
                min={10}
                value={cfg.max_per_user_mb}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setCfg(p => ({ ...p, max_per_user_mb: Number(e.target.value) }))}
                className="w-28"
              />
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={save} disabled={ss.state === "saving"}>
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ss.state} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("settings.bilder.usage.title")}</CardTitle>
          <CardDescription>{t("settings.bilder.usage.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          {stats.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("common.no_images")}</p>
          ) : (
            <div className="divide-y divide-border">
              {stats.map(s => {
                const pctCount = Math.min(100, Math.round((s.count / cfg.max_per_user_count) * 100));
                const pctMb    = Math.min(100, Math.round((s.mb    / cfg.max_per_user_mb)    * 100));
                const barColor = (pct: number) =>
                  pct >= 90 ? "#f87171" : pct >= 70 ? "#fb923c" : "#6366f1";
                return (
                  <div key={s.user_id} className="py-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{s.user_id}</span>
                      <div className="flex gap-4 text-xs text-muted-foreground">
                        <span>{s.count} / {cfg.max_per_user_count} {t("settings.bilder.usage.images_suffix")}</span>
                        <span>{s.mb} / {cfg.max_per_user_mb} MB</span>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <div className="flex justify-between text-xs text-muted-foreground mb-1">
                          <span>{t("common.count_label")}</span><span>{pctCount}%</span>
                        </div>
                        <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                          <div className="h-full rounded-full transition-all"
                            style={{ width: `${pctCount}%`, background: barColor(pctCount) }} />
                        </div>
                      </div>
                      <div>
                        <div className="flex justify-between text-xs text-muted-foreground mb-1">
                          <span>{t("common.space_label")}</span><span>{pctMb}%</span>
                        </div>
                        <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                          <div className="h-full rounded-full transition-all"
                            style={{ width: `${pctMb}%`, background: barColor(pctMb) }} />
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("cameras.title")}</CardTitle>
          <CardDescription>{t("cameras.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <Cameras />
        </CardContent>
      </Card>
    </div>
  );
}
