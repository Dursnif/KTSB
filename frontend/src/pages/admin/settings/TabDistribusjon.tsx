import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { apiGetCapabilities, apiPutCapabilities, type CapabilitiesConfig, type ServiceEntry } from "@/services/api";
import { useSaveState, SaveFeedback } from "./shared";

const DISTRIBUTION_PRESETS: { id: string; emoji: string; services: string[] }[] = [
  {
    id: "full",
    emoji: "🖥",
    services: ["Jing + Jang", "BGE-M3 + wiki (8,6 GB)", "Qdrant", "Voice", "Agenter", "Intent", "STM/LTM"],
  },
  {
    id: "medium",
    emoji: "⚡",
    services: ["BGE-M3 + wiki (8,6 GB)", "Qdrant", "Voice", "Agenter", "Intent", "STM/LTM"],
  },
  {
    id: "letvekt",
    emoji: "🪶",
    services: ["Qdrant", "Voice", "Agenter", "Intent", "STM/LTM"],
  },
];

const V1_UNAVAILABLE_SERVICES = new Set(["voice", "jing", "jang"]);

export default function TabDistribusjon() {
  const { t } = useTranslation();
  const [caps, setCaps] = useState<CapabilitiesConfig | null>(null);
  const [profile, setProfile] = useState("");
  const [servicesOpen, setServicesOpen] = useState(false);
  const ss = useSaveState();

  useEffect(() => {
    apiGetCapabilities().then(d => {
      setCaps(d);
      setProfile(d.distribution_profile ?? "");
    }).catch(() => {});
  }, []);

  const applyPreset = (presetId: string) => {
    setProfile(presetId);
    setCaps(prev => prev ? { ...prev, distribution_profile: presetId } : prev);
  };

  const toggleDomain = (key: string, enabled: boolean) => {
    setCaps(prev => {
      if (!prev) return prev;
      return { ...prev, domains: { ...prev.domains, [key]: { ...prev.domains[key], enabled } } };
    });
  };

  const toggleService = (key: string, enabled: boolean) => {
    setCaps(prev => {
      if (!prev) return prev;
      const services = prev.services ?? {};
      return { ...prev, services: { ...services, [key]: { ...(services[key] as ServiceEntry), enabled } } };
    });
  };

  const save = async () => {
    if (!caps) return;
    ss.saving();
    try {
      await apiPutCapabilities({ domains: caps.domains, distribution_profile: profile, services: caps.services });
      ss.saved();
    } catch { ss.error(); }
  };

  if (!caps) return <div className="text-muted-foreground text-sm p-4">{t("common.loading")}</div>;

  const serviceEntries = Object.entries(caps.services ?? {});

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.distribusjon.profile.title")}</CardTitle>
          <CardDescription>{t("settings.distribusjon.profile.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
            {DISTRIBUTION_PRESETS.map(p => (
              <button
                key={p.id}
                onClick={() => applyPreset(p.id)}
                className={`text-left p-4 rounded-lg border transition-colors ${profile === p.id ? "border-primary bg-primary/10" : "border-border hover:border-muted-foreground"}`}
              >
                <div className="text-2xl mb-1">{p.emoji}</div>
                <div className="font-semibold text-sm">{t(`settings.distribusjon.presets.${p.id}.label`, p.id)}</div>
                <div className="text-xs text-muted-foreground mt-1">{t(`settings.distribusjon.presets.${p.id}.description`)}</div>
              </button>
            ))}
          </div>

          <Separator className="my-4" />

          <p className="text-sm font-medium mb-3">{t("settings.distribusjon.profile.domains_label")}</p>
          <div className="divide-y divide-border">
            {Object.entries(caps.domains).map(([key, domain]) => (
              <div key={key} className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium capitalize">{key.replace(/_/g, " ")}</p>
                  {domain.notes && <p className="text-xs text-muted-foreground">{domain.notes}</p>}
                </div>
                <Switch checked={domain.enabled} onCheckedChange={v => toggleDomain(key, v)} className="data-checked:bg-green-600 data-unchecked:bg-red-600" />
              </div>
            ))}
          </div>

          {serviceEntries.length > 0 && (
            <>
              <Separator className="my-4" />
              <button
                onClick={() => setServicesOpen(v => !v)}
                className="flex items-center gap-2 text-sm font-medium w-full text-left hover:text-foreground transition-colors"
              >
                {servicesOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                {t("settings.distribusjon.services.title")}
              </button>
              {servicesOpen && (
                <div className="divide-y divide-border mt-3">
                  {serviceEntries.map(([key, svc]) => {
                    const unavailable = V1_UNAVAILABLE_SERVICES.has(key);
                    return (
                      <div key={key} className={`flex items-center justify-between py-3 ${unavailable ? "opacity-60" : ""}`}>
                        <div>
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-medium capitalize">{key.replace(/_/g, " ")}</p>
                            {unavailable && (
                              <Badge variant="outline" className="text-xs text-muted-foreground">
                                {t("settings.distribusjon.services.v1_unavailable")}
                              </Badge>
                            )}
                          </div>
                          {svc.notes && <p className="text-xs text-muted-foreground">{svc.notes}</p>}
                        </div>
                        <Switch
                          checked={svc.enabled}
                          onCheckedChange={v => toggleService(key, v)}
                          disabled={unavailable}
                          className="data-checked:bg-green-600 data-unchecked:bg-red-600"
                        />
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}

          <div className="flex items-center gap-3 mt-4">
            <Button onClick={save} disabled={ss.state === "saving"}>
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ss.state} />
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            {t("settings.distribusjon.profile.hot_reload_hint")}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
