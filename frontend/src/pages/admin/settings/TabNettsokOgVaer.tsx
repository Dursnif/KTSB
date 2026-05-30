import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Loader2 } from "lucide-react";
import {
  apiGetWeather, apiPutWeather, apiGetWebsearch, apiPutWebsearch,
  apiGetSecrets, apiPutSecret, apiGetTrustedSources, apiPutTrustedSources,
  type WeatherProvider, type WeatherConfig, type WebsearchConfig, type TrustedSources,
} from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback, MaskedInput } from "./shared";

const WEATHER_PROVIDERS: { value: WeatherProvider; label: string; needsKey: boolean; keyField?: "owm" | "wapi" | "pirate" }[] = [
  { value: "met.no",         label: "met.no (gratis, norsk, ingen nøkkel)",            needsKey: false },
  { value: "open-meteo",     label: "Open-Meteo (gratis, global, ingen nøkkel)",       needsKey: false },
  { value: "openweathermap", label: "OpenWeatherMap (global, API-nøkkel påkrevd)",     needsKey: true,  keyField: "owm" },
  { value: "weatherapi",     label: "WeatherAPI.com (global, API-nøkkel påkrevd)",     needsKey: true,  keyField: "wapi" },
  { value: "pirateweather",  label: "PirateWeather (global, gratis nøkkel påkrevd)",   needsKey: true,  keyField: "pirate" },
];

export default function TabNettsokOgVaer() {
  const { t } = useTranslation();
  const [weather, setWeather]     = useState<WeatherConfig | null>(null);
  const [provider, setProvider]   = useState<WeatherProvider>("met.no");
  const [forecastDays, setForecastDays] = useState(2);
  const [owmKey, setOwmKey]       = useState("");
  const [wapiKey, setWapiKey]     = useState("");
  const [pirateKey, setPirateKey] = useState("");
  const [showFeelsLike, setShowFeelsLike] = useState(false);
  const [showUvIndex, setShowUvIndex]     = useState(false);
  const [showSunTimes, setShowSunTimes]   = useState(false);
  const [showAlerts, setShowAlerts]       = useState(true);
  const [showAirQuality, setShowAirQuality] = useState(false);
  const [useHaSensors, setUseHaSensors]   = useState(false);
  const [showTides, setShowTides]                   = useState(false);
  const [tideProvider, setTideProvider]             = useState<string>("auto");
  const [stormglassKey, setStormglassKey]           = useState("");
  const [useCameraForWeather, setUseCameraForWeather] = useState(false);
  const [weatherCamera, setWeatherCamera]           = useState("");
  const [haTempEntity, setHaTempEntity]           = useState("");
  const [haWindEntity, setHaWindEntity]           = useState("");
  const [haWindGustEntity, setHaWindGustEntity]   = useState("");
  const [haWindDirEntity, setHaWindDirEntity]     = useState("");
  const [haPrecipEntity, setHaPrecipEntity]       = useState("");
  const [haPrecipLastHourEntity, setHaPrecipLastHourEntity] = useState("");
  const [haPrecipTodayEntity, setHaPrecipTodayEntity]       = useState("");
  const [haHumidityEntity, setHaHumidityEntity]   = useState("");
  const [haPressureEntity, setHaPressureEntity]   = useState("");
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
      setShowFeelsLike(d.show_feels_like ?? false);
      setShowUvIndex(d.show_uv_index ?? false);
      setShowSunTimes(d.show_sun_times ?? false);
      setShowAlerts(d.show_alerts ?? true);
      setShowAirQuality(d.show_air_quality ?? false);
      setUseHaSensors(d.use_ha_sensors ?? false);
      setShowTides(d.show_tides ?? false);
      setTideProvider(d.tide_provider ?? "auto");
      setUseCameraForWeather(d.use_camera_for_weather ?? false);
      setWeatherCamera(d.weather_camera ?? "");
      setHaTempEntity(d.ha_temp_entity ?? "");
      setHaWindEntity(d.ha_wind_entity ?? "");
      setHaWindGustEntity(d.ha_wind_gust_entity ?? "");
      setHaWindDirEntity(d.ha_wind_direction_entity ?? "");
      setHaPrecipEntity(d.ha_precip_entity ?? "");
      setHaPrecipLastHourEntity(d.ha_precip_last_hour_entity ?? "");
      setHaPrecipTodayEntity(d.ha_precip_today_entity ?? "");
      setHaHumidityEntity(d.ha_humidity_entity ?? "");
      setHaPressureEntity(d.ha_pressure_entity ?? "");
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
        forecast_days:    forecastDays,
        show_feels_like:  showFeelsLike,
        show_uv_index:    showUvIndex,
        show_sun_times:   showSunTimes,
        show_alerts:      showAlerts,
        show_air_quality: showAirQuality,
        use_ha_sensors:    useHaSensors,
        ha_temp_entity:    haTempEntity.trim(),
        ha_wind_entity:    haWindEntity.trim(),
        ha_wind_gust_entity:      haWindGustEntity.trim(),
        ha_wind_direction_entity: haWindDirEntity.trim(),
        ha_precip_entity:         haPrecipEntity.trim(),
        ha_precip_last_hour_entity: haPrecipLastHourEntity.trim(),
        ha_precip_today_entity:   haPrecipTodayEntity.trim(),
        ha_humidity_entity: haHumidityEntity.trim(),
        ha_pressure_entity: haPressureEntity.trim(),
        show_tides:             showTides,
        tide_provider:          tideProvider,
        use_camera_for_weather: useCameraForWeather,
        weather_camera:         weatherCamera.trim(),
        ...(owmKey        ? { openweathermap_key: owmKey }        : {}),
        ...(wapiKey       ? { weatherapi_key:     wapiKey }       : {}),
        ...(pirateKey     ? { pirateweather_key:  pirateKey }     : {}),
        ...(stormglassKey ? { stormglass_key:     stormglassKey } : {}),
      });
      setOwmKey(""); setWapiKey(""); setPirateKey(""); setStormglassKey("");
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

            {provider === "pirateweather" && (
              <FieldRow
                label="PirateWeather API-nøkkel"
                hint={weather?.pirateweather_key_set
                  ? t("settings.llm.api_key_set_hint", { masked: weather.pirateweather_key_masked })
                  : "Ikke satt — registrer gratis på pirate-weather.apiable.io"}
              >
                <MaskedInput value={pirateKey} onChange={setPirateKey} placeholder={weather?.pirateweather_key_set ? "••• (oppdater)" : "Lim inn nøkkel"} />
              </FieldRow>
            )}

            {providerInfo?.needsKey && (
              <div className="py-2">
                <p className="text-xs text-amber-400">{t("settings.nettsok.weather.needs_key_warning")}</p>
              </div>
            )}

            <FieldRow label={t("settings.nettsok.weather.feels_like_label")} hint={t("settings.nettsok.weather.feels_like_hint")}>
              <Switch checked={showFeelsLike} onCheckedChange={setShowFeelsLike} />
            </FieldRow>

            {provider === "open-meteo" && (
              <FieldRow label={t("settings.nettsok.weather.uv_label")} hint={t("settings.nettsok.weather.uv_hint")}>
                <Switch checked={showUvIndex} onCheckedChange={setShowUvIndex} />
              </FieldRow>
            )}

            <FieldRow label={t("settings.nettsok.weather.sun_label")} hint={t("settings.nettsok.weather.sun_hint")}>
              <Switch checked={showSunTimes} onCheckedChange={setShowSunTimes} />
            </FieldRow>

            {provider === "met.no" && (
              <FieldRow label={t("settings.nettsok.weather.alerts_label")} hint={t("settings.nettsok.weather.alerts_hint")}>
                <Switch checked={showAlerts} onCheckedChange={setShowAlerts} />
              </FieldRow>
            )}

            <FieldRow label={t("settings.nettsok.weather.air_quality_label")} hint={t("settings.nettsok.weather.air_quality_hint")}>
              <Switch checked={showAirQuality} onCheckedChange={setShowAirQuality} />
            </FieldRow>

            <FieldRow label={t("settings.nettsok.weather.tides_label")} hint={t("settings.nettsok.weather.tides_hint")}>
              <Switch checked={showTides} onCheckedChange={setShowTides} />
            </FieldRow>

            {showTides && (
              <>
                <FieldRow label={t("settings.nettsok.weather.tide_provider_label")} hint={t("settings.nettsok.weather.tide_provider_hint")}>
                  <Select value={tideProvider} onValueChange={(v: string | null) => { if (v) setTideProvider(v); }}>
                    <SelectTrigger className="w-52">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">auto (Kartverket → Stormglass)</SelectItem>
                      <SelectItem value="kartverket">Kartverket (norsk kyst)</SelectItem>
                      <SelectItem value="stormglass">Stormglass (global)</SelectItem>
                    </SelectContent>
                  </Select>
                </FieldRow>
                {(tideProvider === "stormglass" || tideProvider === "auto") && (
                  <FieldRow
                    label={t("settings.nettsok.weather.stormglass_key_label")}
                    hint={weather?.stormglass_key_set
                      ? t("settings.llm.api_key_set_hint", { masked: weather.stormglass_key_masked })
                      : t("settings.nettsok.weather.stormglass_key_unset_hint")}
                  >
                    <MaskedInput value={stormglassKey} onChange={setStormglassKey} placeholder={weather?.stormglass_key_set ? "••• (oppdater)" : "Lim inn nøkkel"} />
                  </FieldRow>
                )}
              </>
            )}

            <FieldRow label={t("settings.nettsok.weather.camera_weather_label")} hint={t("settings.nettsok.weather.camera_weather_hint")}>
              <Switch checked={useCameraForWeather} onCheckedChange={setUseCameraForWeather} />
            </FieldRow>

            {useCameraForWeather && (
              <FieldRow label={t("settings.nettsok.weather.camera_name_label")} hint={t("settings.nettsok.weather.camera_name_hint")}>
                <Input
                  value={weatherCamera}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setWeatherCamera(e.target.value)}
                  placeholder="pip_kamera"
                  className="w-64 font-mono text-sm"
                />
              </FieldRow>
            )}

            <FieldRow label={t("settings.nettsok.weather.ha_sensors_label")} hint={t("settings.nettsok.weather.ha_sensors_hint")}>
              <Switch checked={useHaSensors} onCheckedChange={setUseHaSensors} />
            </FieldRow>

            {useHaSensors && (
              <>
                <FieldRow label={t("settings.nettsok.weather.ha_temp_label")} hint={t("settings.nettsok.weather.ha_temp_hint")}>
                  <Input
                    value={haTempEntity}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaTempEntity(e.target.value)}
                    placeholder="sensor.outdoor_temperature"
                    className="w-80 font-mono text-sm"
                  />
                </FieldRow>
                <FieldRow label={t("settings.nettsok.weather.ha_wind_label")} hint={t("settings.nettsok.weather.ha_wind_hint")}>
                  <Input
                    value={haWindEntity}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaWindEntity(e.target.value)}
                    placeholder="sensor.outdoor_wind_speed"
                    className="w-80 font-mono text-sm"
                  />
                </FieldRow>
                <FieldRow label={t("settings.nettsok.weather.ha_wind_gust_label")} hint={t("settings.nettsok.weather.ha_wind_gust_hint")}>
                  <Input
                    value={haWindGustEntity}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaWindGustEntity(e.target.value)}
                    placeholder="sensor.outdoor_wind_gust"
                    className="w-80 font-mono text-sm"
                  />
                </FieldRow>
                <FieldRow label={t("settings.nettsok.weather.ha_wind_dir_label")} hint={t("settings.nettsok.weather.ha_wind_dir_hint")}>
                  <Input
                    value={haWindDirEntity}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaWindDirEntity(e.target.value)}
                    placeholder="sensor.outdoor_wind_direction"
                    className="w-80 font-mono text-sm"
                  />
                </FieldRow>
                <FieldRow label={t("settings.nettsok.weather.ha_precip_label")} hint={t("settings.nettsok.weather.ha_precip_hint")}>
                  <Input
                    value={haPrecipEntity}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaPrecipEntity(e.target.value)}
                    placeholder="sensor.outdoor_precipitation"
                    className="w-80 font-mono text-sm"
                  />
                </FieldRow>
                <FieldRow label={t("settings.nettsok.weather.ha_precip_last_hour_label")} hint={t("settings.nettsok.weather.ha_precip_last_hour_hint")}>
                  <Input
                    value={haPrecipLastHourEntity}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaPrecipLastHourEntity(e.target.value)}
                    placeholder="sensor.outdoor_precipitation_last_hour"
                    className="w-80 font-mono text-sm"
                  />
                </FieldRow>
                <FieldRow label={t("settings.nettsok.weather.ha_precip_today_label")} hint={t("settings.nettsok.weather.ha_precip_today_hint")}>
                  <Input
                    value={haPrecipTodayEntity}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaPrecipTodayEntity(e.target.value)}
                    placeholder="sensor.outdoor_precipitation_today"
                    className="w-80 font-mono text-sm"
                  />
                </FieldRow>
                <FieldRow label={t("settings.nettsok.weather.ha_humidity_label")} hint={t("settings.nettsok.weather.ha_humidity_hint")}>
                  <Input
                    value={haHumidityEntity}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaHumidityEntity(e.target.value)}
                    placeholder="sensor.outdoor_humidity"
                    className="w-80 font-mono text-sm"
                  />
                </FieldRow>
                <FieldRow label={t("settings.nettsok.weather.ha_pressure_label")} hint={t("settings.nettsok.weather.ha_pressure_hint")}>
                  <Input
                    value={haPressureEntity}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaPressureEntity(e.target.value)}
                    placeholder="sensor.outdoor_pressure"
                    className="w-80 font-mono text-sm"
                  />
                </FieldRow>
              </>
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
