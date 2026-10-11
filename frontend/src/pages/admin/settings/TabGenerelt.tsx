import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import axios from "axios";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Loader2 } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { apiGetLanguage, apiPutLanguage, apiGetNetwork, apiPutNetwork } from "@/services/api";
import { BASE, token } from "./constants";
import { FieldRow, SaveFeedback, ExpertOnly } from "./shared";
import { useSaveState } from "./useSaveState";
import LogsAndDataCard from "./LogsAndDataCard";

type Location = { city: string; postal_code: string; country: string; lat: number; lon: number; timezone: string };

const LANGUAGE_OPTS = ["nb", "en", "de"] as const;

const KARE_LANGUAGE_OPTS: { code: string; label: string }[] = [
  { code: "nb", label: "Norsk" },
  { code: "en", label: "English" },
  { code: "de", label: "Deutsch" },
  { code: "fr", label: "Français" },
  { code: "es", label: "Español" },
  { code: "sv", label: "Svenska" },
  { code: "da", label: "Dansk" },
  { code: "nl", label: "Nederlands" },
  { code: "fi", label: "Suomi" },
  { code: "it", label: "Italiano" },
  { code: "pl", label: "Polski" },
  { code: "pt", label: "Português" },
  { code: "ru", label: "Русский" },
  { code: "zh", label: "中文" },
  { code: "ja", label: "日本語" },
  { code: "ar", label: "العربية" },
];

export default function TabGenerelt() {
  const { t, i18n } = useTranslation();
  const [lok, setLok] = useState<Location>({ city: "", postal_code: "", country: "", lat: 0, lon: 0, timezone: "" });
  const [lang, setLang] = useState<string>("nb");
  const [kareLang, setKareLang] = useState<string>("nb");
  const ssLoc  = useSaveState();
  const ssLang = useSaveState();
  const ssNet  = useSaveState();
  // Network and access: lists edited as comma-separated text
  const [netSubnets, setNetSubnets] = useState("");
  const [netVpn, setNetVpn] = useState("");
  const [netProxies, setNetProxies] = useState("");
  const [netDocs, setNetDocs] = useState(false);
  const [netError, setNetError] = useState("");

  useEffect(() => {
    axios.get(`${BASE}/api/settings`, { headers: { Authorization: `Bearer ${token()}` } })
      .then(r => { if (r.data.location) setLok(r.data.location); })
      .catch(() => {});
    apiGetLanguage().then(r => {
      setLang(r.language || "nb");
      setKareLang(r.kare_language || r.language || "nb");
    }).catch(() => {});
    apiGetNetwork().then(n => {
      setNetSubnets(n.local_subnets.join(", "));
      setNetVpn(n.vpn_subnet);
      setNetProxies(n.trusted_proxies.join(", "));
      setNetDocs(n.api_docs);
    }).catch(() => {});
  }, []);

  const splitList = (v: string) => v.split(/[\s,]+/).map(x => x.trim()).filter(Boolean);

  const saveNet = async () => {
    ssNet.saving();
    setNetError("");
    try {
      const n = await apiPutNetwork({
        local_subnets: splitList(netSubnets),
        vpn_subnet: netVpn.trim(),
        trusted_proxies: splitList(netProxies),
        api_docs: netDocs,
      });
      setNetSubnets(n.local_subnets.join(", "));
      setNetProxies(n.trusted_proxies.join(", "));
      ssNet.saved();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setNetError(detail ?? "");
      ssNet.error();
    }
  };

  const saveLoc = async () => {
    ssLoc.saving();
    try {
      await axios.put(`${BASE}/api/settings/location`, lok, { headers: { Authorization: `Bearer ${token()}` } });
      ssLoc.saved();
    } catch { ssLoc.error(); }
  };

  const saveLang = async () => {
    ssLang.saving();
    try {
      await apiPutLanguage(lang, kareLang);
      localStorage.setItem("kaare_lang", lang);
      i18n.changeLanguage(lang);
      ssLang.saved();
    } catch { ssLang.error(); }
  };

  const set = (k: keyof Location, v: string) =>
    setLok(p => ({ ...p, [k]: ["lat", "lon"].includes(k) ? Number(v) : v }));

  const locationFields: [keyof Location, string, string, string][] = [
    ["city",        t("settings.generelt.location.fields.city.label"),        "text",   t("settings.generelt.location.fields.city.hint")],
    ["postal_code", t("settings.generelt.location.fields.postal_code.label"), "text",   t("settings.generelt.location.fields.postal_code.hint")],
    ["country",     t("settings.generelt.location.fields.country.label"),     "text",   t("settings.generelt.location.fields.country.hint")],
    ["lat",         t("settings.generelt.location.fields.lat.label"),         "number", t("settings.generelt.location.fields.lat.hint")],
    ["lon",         t("settings.generelt.location.fields.lon.label"),         "number", t("settings.generelt.location.fields.lon.hint")],
    ["timezone",    t("settings.generelt.location.fields.timezone.label"),    "text",   t("settings.generelt.location.fields.timezone.hint")],
  ];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.generelt.location.title")}</CardTitle>
          <CardDescription>{t("settings.generelt.location.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            {locationFields.map(([key, label, type, hint]) => (
              <FieldRow key={key} label={label} hint={hint}>
                <Input
                  type={type}
                  value={String(lok[key] ?? "")}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => set(key, e.target.value)}
                  step={type === "number" ? "any" : undefined}
                />
              </FieldRow>
            ))}
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveLoc} disabled={ssLoc.state === "saving"}>
              {ssLoc.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssLoc.state} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("settings.generelt.language.title")}</CardTitle>
          <CardDescription>{t("settings.generelt.language.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.generelt.language.gui_label")} hint={t("settings.generelt.language.gui_hint")}>
              <Select value={lang} onValueChange={(v) => { if (v) setLang(v); }}>
                <SelectTrigger className="w-[220px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANGUAGE_OPTS.map(code => (
                    <SelectItem key={code} value={code}>
                      {t(`settings.generelt.language.options.${code}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldRow>
            <FieldRow label={t("settings.generelt.language.kare_label")} hint={t("settings.generelt.language.kare_hint")}>
              <Select value={kareLang} onValueChange={(v) => { if (v) setKareLang(v); }}>
                <SelectTrigger className="w-[220px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {KARE_LANGUAGE_OPTS.map(({ code, label }) => (
                    <SelectItem key={code} value={code}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveLang} disabled={ssLang.state === "saving"}>
              {ssLang.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssLang.state} />
          </div>
        </CardContent>
      </Card>

      <ExpertOnly>
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.generelt.network.title")}</CardTitle>
            <CardDescription>{t("settings.generelt.network.description")}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="divide-y divide-border">
              <FieldRow label={t("settings.generelt.network.local_subnets_label")} hint={t("settings.generelt.network.local_subnets_hint")}>
                <Input value={netSubnets} placeholder="192.168.0.0/24" onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNetSubnets(e.target.value)} />
              </FieldRow>
              <FieldRow label={t("settings.generelt.network.vpn_subnet_label")} hint={t("settings.generelt.network.vpn_subnet_hint")}>
                <Input value={netVpn} placeholder="10.0.0.0/24" onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNetVpn(e.target.value)} />
              </FieldRow>
              <FieldRow label={t("settings.generelt.network.trusted_proxies_label")} hint={t("settings.generelt.network.trusted_proxies_hint")}>
                <Input value={netProxies} placeholder="caddy, 127.0.0.1" onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNetProxies(e.target.value)} />
              </FieldRow>
              <FieldRow label={t("settings.generelt.network.api_docs_label")} hint={t("settings.generelt.network.api_docs_hint")}>
                <Switch checked={netDocs} onCheckedChange={(v: boolean) => setNetDocs(v)} />
              </FieldRow>
            </div>
            <div className="flex items-center gap-3 mt-4">
              <Button onClick={saveNet} disabled={ssNet.state === "saving"}>
                {ssNet.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {t("common.save")}
              </Button>
              <SaveFeedback state={ssNet.state} />
              {netError && <span className="text-sm text-destructive">{netError}</span>}
            </div>
          </CardContent>
        </Card>
      </ExpertOnly>

      <ExpertOnly>
        <LogsAndDataCard />
      </ExpertOnly>
    </div>
  );
}
