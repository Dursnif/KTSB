import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { CheckCircle2, XCircle, Loader2, RotateCcw } from "lucide-react";
import { apiGetVramOverview, apiPutDockerControl, type DockerControlSettings, type VramEntry } from "@/services/api";

export function LlmInfraCard({ dockerControl, onDockerControlChange }: {
  dockerControl: DockerControlSettings;
  onDockerControlChange: (v: boolean) => void;
}) {
  const { t } = useTranslation();
  const [vramEntries, setVramEntries] = useState<VramEntry[]>([]);
  const [vramLoading, setVramLoading] = useState(false);
  const [toggling, setToggling] = useState(false);

  const loadVram = async () => {
    setVramLoading(true);
    try {
      const res = await apiGetVramOverview();
      setVramEntries(res.entries);
    } catch { /* ignore */ } finally {
      setVramLoading(false);
    }
  };

  useEffect(() => { loadVram(); }, []);

  const handleToggle = async (v: boolean) => {
    setToggling(true);
    try {
      await apiPutDockerControl(v);
      onDockerControlChange(v);
    } catch { /* ignore */ } finally {
      setToggling(false);
    }
  };

  const formatVram = (bytes: number) => {
    if (bytes === 0) return null;
    return `${(bytes / 1073741824).toFixed(1)} GiB`;
  };

  return (
    <Card className="border-border bg-card">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{t("settings.llm.infra_card_title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Docker control toggle */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label className="text-sm font-medium">{t("settings.llm.docker_control")}</Label>
            <p className="text-xs text-muted-foreground">{t("settings.llm.docker_control_hint")}</p>
          </div>
          <div className="flex items-center gap-3">
            {dockerControl.socket_available
              ? <span className="flex items-center gap-1 text-xs text-green-500"><CheckCircle2 className="h-3 w-3" />{t("settings.llm.docker_socket_ok")}</span>
              : <span className="flex items-center gap-1 text-xs text-destructive"><XCircle className="h-3 w-3" />{t("settings.llm.docker_socket_missing")}</span>
            }
            <Switch
              checked={dockerControl.allow_docker_control}
              onCheckedChange={handleToggle}
              disabled={toggling}
            />
          </div>
        </div>

        <Separator />

        {/* VRAM overview */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-sm font-medium">{t("settings.llm.vram_overview")}</Label>
            <Button variant="ghost" size="sm" onClick={loadVram} disabled={vramLoading} className="h-7 px-2 text-xs">
              {vramLoading ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RotateCcw className="h-3 w-3 mr-1" />}
              {t("settings.llm.vram_refresh")}
            </Button>
          </div>
          {vramEntries.length === 0 && !vramLoading && (
            <p className="text-xs text-muted-foreground">{t("settings.llm.vram_no_models")}</p>
          )}
          {vramEntries.length > 0 && (
            <div className="rounded-md border border-border overflow-hidden text-xs">
              <table className="w-full">
                <thead>
                  <tr className="bg-muted/40 text-muted-foreground">
                    <th className="text-left px-3 py-2 font-medium">{t("settings.llm.vram_col_role")}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("settings.llm.vram_col_model")}</th>
                    <th className="text-right px-3 py-2 font-medium">{t("settings.llm.vram_col_vram")}</th>
                  </tr>
                </thead>
                <tbody>
                  {vramEntries.map((e, i) => (
                    <tr key={i} className="border-t border-border">
                      <td className="px-3 py-2 font-mono text-muted-foreground">{e.role}</td>
                      <td className="px-3 py-2 truncate max-w-[200px]" title={e.model}>{e.model}</td>
                      <td className="px-3 py-2 text-right">
                        {e.on_cpu
                          ? <span className="text-amber-500 font-medium">⚠ {t("settings.llm.vram_on_cpu")}</span>
                          : <span className="text-green-500">{formatVram(e.vram_bytes)}</span>
                        }
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
