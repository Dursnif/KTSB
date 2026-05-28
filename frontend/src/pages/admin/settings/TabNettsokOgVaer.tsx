import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Loader2 } from "lucide-react";
import {
  apiGetWeather, apiPutWeather, apiGetWebsearch, apiPutWebsearch,
  apiGetSecrets, apiPutSecret, apiGetTrustedSources, apiPutTrustedSources,
  type WeatherProvider, type WeatherConfig, type WebsearchConfig, type TrustedSources,
} from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback, MaskedInput } from "./shared";

const WEATHER_PROVIDERS: { value: WeatherProvider; label: string; needsKey: boolean; keyField?: "owm" | "wapi" }[] = [
  { value: "met.no",        label: "met.no (gratis, norsk, ingen nøkkel)",         needsKey: false },
  { value: "open-meteo",    label: "Open-Meteo (gratis, global, ingen nøkkel)",    needsKey: false },
  { value: "openweathermap",label: "OpenWeatherMap (global, API-nøkkel påkrevd)",  needsKey: true,  keyField: "owm" },
  { value: "weatherapi",    label: "WeatherAPI.com (global, API-nøkkel påkrevd)",  needsKey: true,  keyField: "wapi" },
];

export default function TabNettsokOgVaer() {
  const { t } = useTranslation();
  const [weather, setWeather]     = useState<WeatherConfig | null>(null);
  const [provider, setProvider]   = useState<WeatherProvider>("met.no");
  const [forecastDays, setForecastDays] = useState(2);
  const [owmKey, setOwmKey]       = useState("");
  const [wapiKey, setWapiKey]     = useState("");
  const ssWeather = useSaveState();

  const [, setWs]                 = useState<WebsearchConfig | null>(null);
  const [wsLocal, setWsLocal]     = useState<WebsearchConfig>({
    provider: "ddg", fallback: "ddg",
    fetch_count: 10, max_results: 3, content_max: 3000,
    searxng_url: "", brave_country: "NO", brave_search_lang: "nb",
  });
  const ssWs = useSaveState();

  const [braveKey, setBraveKey]   = useState("");
  const [braveStatus, setBraveStatus] = useState<{ is_set: boolean; masked: string } | null>(null);
  const ssBrave = useSaveState();

  const [sources, setSources]     = useState<TrustedSources>({});
  const [newCat, setNewCat]       = useState("");
  const [addingDomain, setAddingDomain] = useState<Record<string, boolean>>({});
  const [newDomainInput, setNewDomainInput] = useState<Record<string, { domain: string; beskrivelse: string }>>({});
  const ssSources = useSaveState();

  useEffect(() => {
    apiGetWeather().then(d => {
      setWeather(d);
      setProvider(d.provider);
      setForecastDays(d.forecast_days);
    }).catch(() => {});
    apiGetSecrets().then(s => setBraveStatus(s.brave)).catch(() => {});
    apiGetWebsearch().then(d => { setWs(d); setWsLocal(d); }).catch(() => {});
    apiGetTrustedSources().then(setSources).catch(() => {});
  }, []);

  const saveWeather = async () => {
    ssWeather.saving();
    try {
      await apiPutWeather({
        provider,
        forecast_days: forecastDays,
        ...(owmKey  ? { openweathermap_key: owmKey }  : {}),
        ...(wapiKey ? { weatherapi_key:     wapiKey } : {}),
      });
      setOwmKey(""); setWapiKey("");
      const fresh = await apiGetWeather();
      setWeather(fresh);
      ssWeather.saved();
    } catch { ssWeather.error(); }
  };

  const saveWs = async () => {
    ssWs.saving();
    try {
      await apiPutWebsearch(wsLocal);
      ssWs.saved();
    } catch { ssWs.error(); }
  };

  const saveBrave = async () => {
    if (!braveKey) return;
    ssBrave.saving();
    try {
      await apiPutSecret("brave", braveKey);
      setBraveKey("");
      const s = await apiGetSecrets();
      setBraveStatus(s.brave);
      ssBrave.saved();
    } catch { ssBrave.error(); }
  };

  const saveSources = async () => {
    ssSources.saving();
    try {
      await apiPutTrustedSources(sources);
      ssSources.saved();
    } catch { ssSources.error(); }
  };

  const removeDomain = (cat: string, idx: number) => {
    setSources(prev => {
      const updated = { ...prev, [cat]: prev[cat].filter((_, i) => i !== idx) };
      if (updated[cat].length === 0) {
        const { [cat]: _, ...rest } = updated;
        return rest;
      }
      return updated;
    });
  };

  const addDomain = (cat: string) => {
    const nd = newDomainInput[cat] ?? { domain: "", beskrivelse: "" };
    if (!nd.domain.trim()) return;
    setSources(prev => ({
      ...prev,
      [cat]: [...(prev[cat] ?? []), { domain: nd.domain.trim().toLowerCase(), beskrivelse: nd.beskrivelse.trim() }],
    }));
    setNewDomainInput(prev => ({ ...prev, [cat]: { domain: "", beskrivelse: "" } }));
    setAddingDomain(prev => ({ ...prev, [cat]: false }));
  };

  const addCategory = () => {
    const key = newCat.trim().toLowerCase().replace(/\s+/g, "_");
    if (!key || key in sources) return;
    setSources(prev => ({ ...prev, [key]: [] }));
    setNewCat("");
  };

  const providerInfo = WEATHER_PROVIDERS.find(p => p.value === provider);

  return (
    <div className="space-y-6">

      {/* ── Vær ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.nettsok.weather.title")}</CardTitle>
          <CardDescription>{t("settings.nettsok.weather.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.nettsok.weather.provider_label")} hint={t("settings.nettsok.weather.provider_hint")}>
              <Select value={provider} onValueChange={(v: string | null) => { if (v) setProvider(v as WeatherProvider); }}>
                <SelectTrigger className="w-80">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {WEATHER_PROVIDERS.map(p => (
                    <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldRow>

            <FieldRow label={t("settings.nettsok.weather.days_label")} hint={t("settings.nettsok.weather.days_hint")}>
              <Input
                type="number"
                min={1} max={7}
                value={forecastDays}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setForecastDays(Number(e.target.value))}
                className="w-20"
              />
            </FieldRow>

            {provider === "openweathermap" && (
              <FieldRow
                label="OpenWeatherMap API-nøkkel"
                hint={weather?.openweathermap_key_set
                  ? t("settings.llm.api_key_set_hint", { masked: weather.openweathermap_key_masked })
                  : "Ikke satt — hent gratis nøkkel på openweathermap.org"}
              >
                <MaskedInput value={owmKey} onChange={setOwmKey} placeholder={weather?.openweathermap_key_set ? "••• (oppdater)" : "Lim inn nøkkel"} />
              </FieldRow>
            )}

            {provider === "weatherapi" && (
              <FieldRow
                label="WeatherAPI API-nøkkel"
                hint={weather?.weatherapi_key_set
                  ? t("settings.llm.api_key_set_hint", { masked: weather.weatherapi_key_masked })
                  : "Ikke satt — hent gratis nøkkel på weatherapi.com"}
              >
                <MaskedInput value={wapiKey} onChange={setWapiKey} placeholder={weather?.weatherapi_key_set ? "••• (oppdater)" : "Lim inn nøkkel"} />
              </FieldRow>
            )}

            {providerInfo?.needsKey && (
              <div className="py-2">
                <p className="text-xs text-amber-400">{t("settings.nettsok.weather.needs_key_warning")}</p>
              </div>
            )}
          </div>

          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveWeather} disabled={ssWeather.state === "saving"}>
              {ssWeather.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssWeather.state} />
          </div>
        </CardContent>
      </Card>

      {/* ── Nettsøk ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.nettsok.websearch.title")}</CardTitle>
          <CardDescription>{t("settings.nettsok.websearch.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.nettsok.websearch.provider_label")} hint={t("settings.nettsok.websearch.provider_hint")}>
              <Select value={wsLocal.provider} onValueChange={(v: string | null) => { if (v) setWsLocal(p => ({ ...p, provider: v })); }}>
                <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ddg">DuckDuckGo (DDG)</SelectItem>
                  <SelectItem value="searxng">SearXNG (self-hosted)</SelectItem>
                  <SelectItem value="brave">Brave Search</SelectItem>
                </SelectContent>
              </Select>
            </FieldRow>
            <FieldRow label={t("settings.nettsok.websearch.fallback_label")} hint={t("settings.nettsok.websearch.fallback_hint")}>
              <Select value={wsLocal.fallback} onValueChange={(v: string | null) => { if (v) setWsLocal(p => ({ ...p, fallback: v })); }}>
                <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ddg">DuckDuckGo (DDG)</SelectItem>
                  <SelectItem value="searxng">SearXNG</SelectItem>
                  <SelectItem value="brave">Brave Search</SelectItem>
                </SelectContent>
              </Select>
            </FieldRow>
            {wsLocal.provider === "searxng" && (
              <FieldRow label={t("settings.nettsok.websearch.searxng_url_label")} hint={t("settings.nettsok.websearch.searxng_url_hint")}>
                <Input
                  value={wsLocal.searxng_url}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setWsLocal(p => ({ ...p, searxng_url: e.target.value }))}
                  placeholder="http://localhost:8888"
                  className="w-72"
                />
              </FieldRow>
            )}
            {([
              ["fetch_count",  "settings.nettsok.websearch.fetch_count_label",  "settings.nettsok.websearch.fetch_count_hint"],
              ["max_results",  "settings.nettsok.websearch.max_results_label",  "settings.nettsok.websearch.max_results_hint"],
              ["content_max",  "settings.nettsok.websearch.content_max_label",  "settings.nettsok.websearch.content_max_hint"],
            ] as [keyof WebsearchConfig, string, string][]).map(([k, labelKey, hintKey]) => (
              <FieldRow key={k} label={t(labelKey)} hint={t(hintKey)}>
                <Input
                  type="number"
                  value={wsLocal[k] as number ?? ""}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setWsLocal(p => ({ ...p, [k]: Number(e.target.value) }))}
                  className="w-28 font-mono text-sm"
                  step="1"
                />
              </FieldRow>
            ))}
            {wsLocal.provider === "brave" && (
              <>
                <FieldRow label={t("settings.nettsok.websearch.brave_country_label")} hint={t("settings.nettsok.websearch.brave_country_hint")}>
                  <Input
                    value={wsLocal.brave_country}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setWsLocal(p => ({ ...p, brave_country: e.target.value.toUpperCase() }))}
                    className="w-20 font-mono text-sm"
                    maxLength={2}
                  />
                </FieldRow>
                <FieldRow label={t("settings.nettsok.websearch.brave_lang_label")} hint={t("settings.nettsok.websearch.brave_lang_hint")}>
                  <Input
                    value={wsLocal.brave_search_lang}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setWsLocal(p => ({ ...p, brave_search_lang: e.target.value.toLowerCase() }))}
                    className="w-20 font-mono text-sm"
                    maxLength={5}
                  />
                </FieldRow>
              </>
            )}
          </div>
          <Separator className="my-2" />
          <FieldRow
            label={t("settings.nettsok.websearch.brave_key_label")}
            hint={braveStatus?.is_set ? t("settings.llm.api_key_set_hint", { masked: braveStatus.masked }) : t("settings.nettsok.websearch.brave_key_unset_hint")}
          >
            <div className="flex gap-2 items-center">
              <MaskedInput value={braveKey} onChange={setBraveKey} placeholder={braveStatus?.is_set ? "••• (oppdater)" : "Lim inn nøkkel"} />
              <Button onClick={saveBrave} disabled={ssBrave.state === "saving" || !braveKey} variant="outline">
                {ssBrave.state === "saving" ? <Loader2 className="h-4 w-4 animate-spin" /> : t("common.update")}
              </Button>
              <SaveFeedback state={ssBrave.state} />
            </div>
          </FieldRow>

          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveWs} disabled={ssWs.state === "saving"}>
              {ssWs.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssWs.state} />
            <p className="text-xs text-muted-foreground ml-2">{t("settings.nettsok.websearch.hot_reload_hint")}</p>
          </div>
        </CardContent>
      </Card>

      {/* ── Vitlistede nettsider ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.nettsok.trusted.title")}</CardTitle>
          <CardDescription>{t("settings.nettsok.trusted.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          {/* Read-only overview */}
          <div
            className="rounded-md bg-muted/40 border border-border font-mono text-xs p-3 mb-5 overflow-y-auto"
            style={{ height: "14rem" }}
          >
            {Object.keys(sources).length === 0 ? (
              <span className="text-muted-foreground">{t("common.no_websites")}</span>
            ) : (
              Object.entries(sources).map(([cat, entries]) => (
                <div key={cat} className="mb-2">
                  <div className="text-muted-foreground uppercase tracking-wide mb-0.5">{cat}</div>
                  {entries.map((e, i) => (
                    <div key={i} className="pl-3 text-blue-400">
                      {e.domain}
                      {e.beskrivelse && <span className="text-muted-foreground"> — {e.beskrivelse}</span>}
                    </div>
                  ))}
                </div>
              ))
            )}
          </div>

          <div className="space-y-5">
            {Object.entries(sources).map(([cat, entries]) => (
              <div key={cat} className="border border-border rounded-md overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 bg-muted/30">
                  <span className="text-xs font-mono font-semibold text-muted-foreground">{cat}</span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-6 text-xs gap-1"
                    onClick={() => setAddingDomain(p => ({ ...p, [cat]: !p[cat] }))}
                  >
                    {t("common.add_domain")}
                  </Button>
                </div>

                <div className="divide-y divide-border">
                  {entries.map((entry, idx) => (
                    <div key={idx} className="flex items-center gap-3 px-3 py-2">
                      <span className="font-mono text-sm text-blue-400 min-w-[160px]">{entry.domain}</span>
                      <span className="text-xs text-muted-foreground flex-1">{entry.beskrivelse || "—"}</span>
                      <button
                        onClick={() => removeDomain(cat, idx)}
                        className="text-destructive/50 hover:text-destructive text-xs px-1"
                      >
                        ✕
                      </button>
                    </div>
                  ))}

                  {addingDomain[cat] && (
                    <div className="flex items-center gap-2 px-3 py-2 bg-muted/20">
                      <div className="flex flex-col gap-0.5">
                        <Input
                          placeholder="domene.no"
                          value={newDomainInput[cat]?.domain ?? ""}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                            setNewDomainInput(p => ({ ...p, [cat]: { ...(p[cat] ?? { domain: "", beskrivelse: "" }), domain: e.target.value } }))
                          }
                          className="w-40 font-mono text-xs h-7"
                        />
                        <span className="text-[10px] text-muted-foreground px-0.5">{t("settings.nettsok.trusted.domain_format_hint")}</span>
                      </div>
                      <Input
                        placeholder={t("settings.nettsok.trusted.short_desc_placeholder")}
                        value={newDomainInput[cat]?.beskrivelse ?? ""}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setNewDomainInput(p => ({ ...p, [cat]: { ...(p[cat] ?? { domain: "", beskrivelse: "" }), beskrivelse: e.target.value } }))
                        }
                        className="flex-1 text-xs h-7"
                      />
                      <Button size="sm" className="h-7 text-xs" onClick={() => addDomain(cat)}>{t("common.add")}</Button>
                      <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setAddingDomain(p => ({ ...p, [cat]: false }))}>{t("common.cancel")}</Button>
                    </div>
                  )}

                  {entries.length === 0 && !addingDomain[cat] && (
                    <p className="text-xs text-muted-foreground px-3 py-2">{t("common.no_domains")}</p>
                  )}
                </div>
              </div>
            ))}

            {/* Add new category */}
            <div className="flex items-center gap-2 pt-2">
              <Input
                placeholder="ny_kategori_nøkkel"
                value={newCat}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewCat(e.target.value)}
                className="w-52 font-mono text-xs"
                onKeyDown={(e) => { if (e.key === "Enter") addCategory(); }}
              />
              <Button variant="outline" size="sm" onClick={addCategory} disabled={!newCat.trim()}>
                {t("common.new_category")}
              </Button>
            </div>
          </div>

          <div className="flex items-center gap-3 mt-5">
            <Button onClick={saveSources} disabled={ssSources.state === "saving"}>
              {ssSources.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save_all")}
            </Button>
            <SaveFeedback state={ssSources.state} />
          </div>
          <p className="text-xs text-muted-foreground mt-2">{t("settings.nettsok.trusted.hot_reload_hint")}</p>
        </CardContent>
      </Card>
    </div>
  );
}
