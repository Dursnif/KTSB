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
  apiGetBlockedSources, apiPutBlockedSources, apiGetLaw, apiPutLaw, apiRefreshLaw,
  type WeatherProvider, type WeatherConfig, type WebsearchConfig, type TrustedSources,
  type TrustedEntry, type SourceType, type TrustLevel, type LawSettings,
} from "@/services/api";
import { FieldRow, SaveFeedback, MaskedInput, ExpertOnly } from "./shared";
import { useSaveState } from "./useSaveState";

const WEATHER_PROVIDERS: { value: WeatherProvider; labelKey: string; needsKey: boolean; keyField?: "owm" | "wapi" | "pirate" }[] = [
  { value: "met.no",         labelKey: "settings.nettsok.weather.provider_met",        needsKey: false },
  { value: "open-meteo",     labelKey: "settings.nettsok.weather.provider_openmeteo",  needsKey: false },
  { value: "openweathermap", labelKey: "settings.nettsok.weather.provider_owm",        needsKey: true,  keyField: "owm" },
  { value: "weatherapi",     labelKey: "settings.nettsok.weather.provider_wapi",       needsKey: true,  keyField: "wapi" },
  { value: "pirateweather",  labelKey: "settings.nettsok.weather.provider_pirate",     needsKey: true,  keyField: "pirate" },
];

// Fixed choices per whitelisted domain (P121 D8) — the probability behind them lives in code/config.
const SOURCE_TYPES: SourceType[] = ["public_authority", "encyclopedia", "newsroom", "research", "official", "other_serious"];
const TRUST_LEVELS: TrustLevel[] = ["high", "normal", "low"];
const EMPTY_ENTRY = { domain: "", description: "", type: "other_serious" as SourceType, trust: "normal" as TrustLevel };

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
    provider: "searxng", fallback: "",
    fetch_count: 10, max_results: 3, content_max: 3000,
    searxng_url: "", brave_country: "", brave_search_lang: "",
    search_total_timeout_s: 60, fetch_timeout_s: 8, max_download_bytes: 3000000,
    per_host_interval_s: 1, page_cache_ttl_s: 21600, page_cache_max_entries: 200,
    trusted_slots: 2, open_web: true, official_timeout_s: 20, background_timeout_s: 180, caution: "balanced",
  });
  const ssWs = useSaveState();

  const [braveKey, setBraveKey]   = useState("");
  const [braveStatus, setBraveStatus] = useState<{ is_set: boolean; masked: string } | null>(null);
  const ssBrave = useSaveState();

  const [sources, setSources]     = useState<TrustedSources>({});
  const [newCat, setNewCat]       = useState("");
  const [addingDomain, setAddingDomain] = useState<Record<string, boolean>>({});
  const [newDomainInput, setNewDomainInput] = useState<Record<string, typeof EMPTY_ENTRY>>({});
  const ssSources = useSaveState();
  const [blocked, setBlocked]     = useState<string[]>([]);
  const [newBlocked, setNewBlocked] = useState("");
  const ssBlocked = useSaveState();
  const ssOpenWeb = useSaveState();
  const [law, setLaw]             = useState<LawSettings | null>(null);
  const ssLaw = useSaveState();

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
    apiGetBlockedSources().then(setBlocked).catch(() => {});
    apiGetLaw().then(setLaw).catch(() => {});
  }, []);

  // While Lovdata is being downloaded/indexed, refresh the status every few seconds.
  useEffect(() => {
    if (!law?.refreshing) return;
    const id = setInterval(() => { apiGetLaw().then(setLaw).catch(() => {}); }, 4000);
    return () => clearInterval(id);
  }, [law?.refreshing]);

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
        return Object.fromEntries(Object.entries(updated).filter(([k]) => k !== cat));
      }
      return updated;
    });
  };

  const addDomain = (cat: string) => {
    const entry = newDomainInput[cat] ?? EMPTY_ENTRY;
    if (!entry.domain.trim()) return;
    setSources(prev => ({
      ...prev,
      [cat]: [...(prev[cat] ?? []), { domain: entry.domain.trim().toLowerCase(), description: entry.description.trim(), type: entry.type, trust: entry.trust }],
    }));
    setNewDomainInput(prev => ({ ...prev, [cat]: { ...EMPTY_ENTRY } }));
    setAddingDomain(prev => ({ ...prev, [cat]: false }));
  };

  const addCategory = () => {
    const key = newCat.trim().toLowerCase().replace(/\s+/g, "_");
    if (!key || key in sources) return;
    setSources(prev => ({ ...prev, [key]: [] }));
    setNewCat("");
  };

  const updateEntry = (cat: string, idx: number, patch: Partial<TrustedEntry>) => {
    setSources(prev => ({ ...prev, [cat]: prev[cat].map((e, i) => (i === idx ? { ...e, ...patch } : e)) }));
  };

  const saveCaution = async (value: WebsearchConfig["caution"]) => {
    setWsLocal(p => ({ ...p, caution: value }));
    ssOpenWeb.saving();
    try {
      await apiPutWebsearch({ caution: value });
      ssOpenWeb.saved();
    } catch { ssOpenWeb.error(); }
  };

  const toggleLaw = async (on: boolean) => {
    ssLaw.saving();
    try {
      await apiPutLaw(on);
      setLaw(await apiGetLaw());
      ssLaw.saved();
    } catch { ssLaw.error(); }
  };

  const refreshLaw = async () => {
    try {
      await apiRefreshLaw();
      setLaw(await apiGetLaw());
    } catch { ssLaw.error(); }
  };

  const toggleOpenWeb = async (on: boolean) => {
    setWsLocal(p => ({ ...p, open_web: on }));
    ssOpenWeb.saving();
    try {
      await apiPutWebsearch({ open_web: on });
      ssOpenWeb.saved();
    } catch { ssOpenWeb.error(); }
  };

  const saveBlocked = async (list: string[]) => {
    ssBlocked.saving();
    try {
      const r = await apiPutBlockedSources(list);
      setBlocked(r.blocked);
      ssBlocked.saved();
    } catch { ssBlocked.error(); }
  };

  const addBlocked = () => {
    const d = newBlocked.trim().toLowerCase();
    if (!d || blocked.includes(d)) return;
    setNewBlocked("");
    void saveBlocked([...blocked, d]);
  };

  const providerInfo = WEATHER_PROVIDERS.find(p => p.value === provider);

  return (
    <div className="space-y-6">

      {/* ── Weather ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.nettsok.weather.title")}</CardTitle>
          <CardDescription>{t("settings.nettsok.weather.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <ExpertOnly><>
            <FieldRow label={t("settings.nettsok.weather.provider_label")} hint={t("settings.nettsok.weather.provider_hint")}>
              <Select value={provider} onValueChange={(v: string | null) => { if (v) setProvider(v as WeatherProvider); }}>
                <SelectTrigger className="w-80">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {WEATHER_PROVIDERS.map(p => (
                    <SelectItem key={p.value} value={p.value}>{t(p.labelKey)}</SelectItem>
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
                label={t("settings.nettsok.weather.owm_key_label")}
                hint={weather?.openweathermap_key_set
                  ? t("settings.llm.api_key_set_hint", { masked: weather.openweathermap_key_masked })
                  : t("settings.nettsok.weather.owm_key_unset_hint")}
              >
                <MaskedInput value={owmKey} onChange={setOwmKey} placeholder={weather?.openweathermap_key_set ? t("common.key_update_placeholder") : t("common.key_paste_placeholder")} />
              </FieldRow>
            )}

            {provider === "weatherapi" && (
              <FieldRow
                label={t("settings.nettsok.weather.wapi_key_label")}
                hint={weather?.weatherapi_key_set
                  ? t("settings.llm.api_key_set_hint", { masked: weather.weatherapi_key_masked })
                  : t("settings.nettsok.weather.wapi_key_unset_hint")}
              >
                <MaskedInput value={wapiKey} onChange={setWapiKey} placeholder={weather?.weatherapi_key_set ? t("common.key_update_placeholder") : t("common.key_paste_placeholder")} />
              </FieldRow>
            )}

            {provider === "pirateweather" && (
              <FieldRow
                label={t("settings.nettsok.weather.pirate_key_label")}
                hint={weather?.pirateweather_key_set
                  ? t("settings.llm.api_key_set_hint", { masked: weather.pirateweather_key_masked })
                  : t("settings.nettsok.weather.pirate_key_unset_hint")}
              >
                <MaskedInput value={pirateKey} onChange={setPirateKey} placeholder={weather?.pirateweather_key_set ? t("common.key_update_placeholder") : t("common.key_paste_placeholder")} />
              </FieldRow>
            )}

            {providerInfo?.needsKey && (
              <div className="py-2">
                <p className="text-xs text-amber-400">{t("settings.nettsok.weather.needs_key_warning")}</p>
              </div>
            )}
            </></ExpertOnly>

            <div className="py-3">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-x-6 gap-y-3">
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

                <FieldRow label={t("settings.nettsok.weather.camera_weather_label")} hint={t("settings.nettsok.weather.camera_weather_hint")}>
                  <Switch checked={useCameraForWeather} onCheckedChange={setUseCameraForWeather} />
                </FieldRow>

                <FieldRow label={t("settings.nettsok.weather.ha_sensors_label")} hint={t("settings.nettsok.weather.ha_sensors_hint")}>
                  <Switch checked={useHaSensors} onCheckedChange={setUseHaSensors} />
                </FieldRow>
              </div>
            </div>

            {showTides && (
              <ExpertOnly><>
                <FieldRow label={t("settings.nettsok.weather.tide_provider_label")} hint={t("settings.nettsok.weather.tide_provider_hint")}>
                  <Select value={tideProvider} onValueChange={(v: string | null) => { if (v) setTideProvider(v); }}>
                    <SelectTrigger className="w-52">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">{t("settings.nettsok.weather.tide_auto_label")}</SelectItem>
                      <SelectItem value="kartverket">{t("settings.nettsok.weather.tide_kartverket_label")}</SelectItem>
                      <SelectItem value="stormglass">{t("settings.nettsok.weather.tide_stormglass_label")}</SelectItem>
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
                    <MaskedInput value={stormglassKey} onChange={setStormglassKey} placeholder={weather?.stormglass_key_set ? t("common.key_update_placeholder") : t("common.key_paste_placeholder")} />
                  </FieldRow>
                )}
              </></ExpertOnly>
            )}

            {useCameraForWeather && (
              <ExpertOnly>
              <FieldRow label={t("settings.nettsok.weather.camera_name_label")} hint={t("settings.nettsok.weather.camera_name_hint")}>
                <Input
                  value={weatherCamera}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setWeatherCamera(e.target.value)}
                  placeholder="pip_kamera"
                  className="w-64 font-mono text-sm"
                />
              </FieldRow>
              </ExpertOnly>
            )}

            {useHaSensors && (
              <ExpertOnly>
              <div className="py-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6">
                  <FieldRow label={t("settings.nettsok.weather.ha_temp_label")} hint={t("settings.nettsok.weather.ha_temp_hint")}>
                    <Input
                      value={haTempEntity}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaTempEntity(e.target.value)}
                      placeholder="sensor.outdoor_temperature"
                      className="w-full font-mono text-sm"
                    />
                  </FieldRow>
                  <FieldRow label={t("settings.nettsok.weather.ha_wind_label")} hint={t("settings.nettsok.weather.ha_wind_hint")}>
                    <Input
                      value={haWindEntity}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaWindEntity(e.target.value)}
                      placeholder="sensor.outdoor_wind_speed"
                      className="w-full font-mono text-sm"
                    />
                  </FieldRow>
                  <FieldRow label={t("settings.nettsok.weather.ha_wind_gust_label")} hint={t("settings.nettsok.weather.ha_wind_gust_hint")}>
                    <Input
                      value={haWindGustEntity}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaWindGustEntity(e.target.value)}
                      placeholder="sensor.outdoor_wind_gust"
                      className="w-full font-mono text-sm"
                    />
                  </FieldRow>
                  <FieldRow label={t("settings.nettsok.weather.ha_wind_dir_label")} hint={t("settings.nettsok.weather.ha_wind_dir_hint")}>
                    <Input
                      value={haWindDirEntity}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaWindDirEntity(e.target.value)}
                      placeholder="sensor.outdoor_wind_direction"
                      className="w-full font-mono text-sm"
                    />
                  </FieldRow>
                  <FieldRow label={t("settings.nettsok.weather.ha_precip_label")} hint={t("settings.nettsok.weather.ha_precip_hint")}>
                    <Input
                      value={haPrecipEntity}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaPrecipEntity(e.target.value)}
                      placeholder="sensor.outdoor_precipitation"
                      className="w-full font-mono text-sm"
                    />
                  </FieldRow>
                  <FieldRow label={t("settings.nettsok.weather.ha_precip_last_hour_label")} hint={t("settings.nettsok.weather.ha_precip_last_hour_hint")}>
                    <Input
                      value={haPrecipLastHourEntity}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaPrecipLastHourEntity(e.target.value)}
                      placeholder="sensor.outdoor_precipitation_last_hour"
                      className="w-full font-mono text-sm"
                    />
                  </FieldRow>
                  <FieldRow label={t("settings.nettsok.weather.ha_precip_today_label")} hint={t("settings.nettsok.weather.ha_precip_today_hint")}>
                    <Input
                      value={haPrecipTodayEntity}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaPrecipTodayEntity(e.target.value)}
                      placeholder="sensor.outdoor_precipitation_today"
                      className="w-full font-mono text-sm"
                    />
                  </FieldRow>
                  <FieldRow label={t("settings.nettsok.weather.ha_humidity_label")} hint={t("settings.nettsok.weather.ha_humidity_hint")}>
                    <Input
                      value={haHumidityEntity}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaHumidityEntity(e.target.value)}
                      placeholder="sensor.outdoor_humidity"
                      className="w-full font-mono text-sm"
                    />
                  </FieldRow>
                  <FieldRow label={t("settings.nettsok.weather.ha_pressure_label")} hint={t("settings.nettsok.weather.ha_pressure_hint")}>
                    <Input
                      value={haPressureEntity}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaPressureEntity(e.target.value)}
                      placeholder="sensor.outdoor_pressure"
                      className="w-full font-mono text-sm"
                    />
                  </FieldRow>
                </div>
              </div>
              </ExpertOnly>
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

      {/* ── Web search ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.nettsok.websearch.title")}</CardTitle>
          <CardDescription>{t("settings.nettsok.websearch.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ExpertOnly><>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.nettsok.websearch.provider_label")} hint={t("settings.nettsok.websearch.provider_hint")}>
              <Select value={wsLocal.provider} onValueChange={(v: string | null) => { if (v) setWsLocal(p => ({ ...p, provider: v })); }}>
                <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="searxng">SearXNG (self-hosted)</SelectItem>
                  <SelectItem value="brave">Brave Search</SelectItem>
                </SelectContent>
              </Select>
            </FieldRow>
            <FieldRow label={t("settings.nettsok.websearch.fallback_label")} hint={t("settings.nettsok.websearch.fallback_hint")}>
              <Select value={wsLocal.fallback || "none"} onValueChange={(v: string | null) => { if (v) setWsLocal(p => ({ ...p, fallback: v === "none" ? "" : v })); }}>
                <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t("settings.nettsok.websearch.fallback_none")}</SelectItem>
                  <SelectItem value="searxng">SearXNG</SelectItem>
                  <SelectItem value="brave">Brave Search</SelectItem>
                </SelectContent>
              </Select>
            </FieldRow>
            {(wsLocal.provider === "searxng" || wsLocal.fallback === "searxng") && (
              <FieldRow label={t("settings.nettsok.websearch.searxng_url_label")} hint={t("settings.nettsok.websearch.searxng_url_hint")}>
                <Input
                  value={wsLocal.searxng_url}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setWsLocal(p => ({ ...p, searxng_url: e.target.value }))}
                  placeholder="http://searxng:8080"
                  className="w-72"
                />
              </FieldRow>
            )}
            {([
              ["fetch_count",  "settings.nettsok.websearch.fetch_count_label",  "settings.nettsok.websearch.fetch_count_hint"],
              ["max_results",  "settings.nettsok.websearch.max_results_label",  "settings.nettsok.websearch.max_results_hint"],
              ["content_max",  "settings.nettsok.websearch.content_max_label",  "settings.nettsok.websearch.content_max_hint"],
              // P121 Phase 1 — time, size, pacing and cache limits (expert section)
              ["search_total_timeout_s", "settings.nettsok.websearch.total_timeout_label", "settings.nettsok.websearch.total_timeout_hint"],
              ["fetch_timeout_s",        "settings.nettsok.websearch.fetch_timeout_label", "settings.nettsok.websearch.fetch_timeout_hint"],
              ["max_download_bytes",     "settings.nettsok.websearch.max_bytes_label",     "settings.nettsok.websearch.max_bytes_hint"],
              ["per_host_interval_s",    "settings.nettsok.websearch.host_interval_label", "settings.nettsok.websearch.host_interval_hint"],
              ["page_cache_ttl_s",       "settings.nettsok.websearch.cache_ttl_label",     "settings.nettsok.websearch.cache_ttl_hint"],
              ["page_cache_max_entries", "settings.nettsok.websearch.cache_max_label",     "settings.nettsok.websearch.cache_max_hint"],
              ["trusted_slots",          "settings.nettsok.websearch.trusted_slots_label", "settings.nettsok.websearch.trusted_slots_hint"],
              ["official_timeout_s",     "settings.nettsok.websearch.official_timeout_label", "settings.nettsok.websearch.official_timeout_hint"],
              ["background_timeout_s",   "settings.nettsok.websearch.background_timeout_label", "settings.nettsok.websearch.background_timeout_hint"],
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
              <MaskedInput value={braveKey} onChange={setBraveKey} placeholder={braveStatus?.is_set ? t("common.key_update_placeholder") : t("common.key_paste_placeholder")} />
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
          <div className="text-xs text-muted-foreground mt-2">
            {t("settings.nettsok.websearch.paid_calls_label")}{" "}
            {Object.keys(wsLocal.paid_calls_today ?? {}).length === 0
              ? "0"
              : Object.entries(wsLocal.paid_calls_today ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ")}
          </div>
          </></ExpertOnly>
        </CardContent>
      </Card>

      {/* ── Norsk lov fra Lovdatas åpne datasett (P123) ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.nettsok.law.title")}</CardTitle>
          <CardDescription>{t("settings.nettsok.law.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <FieldRow label={t("settings.nettsok.law.enabled_label")} hint={t("settings.nettsok.law.enabled_hint")}>
            <div className="flex items-center gap-3">
              <Switch checked={!!law?.enabled} onCheckedChange={(v: boolean) => { void toggleLaw(v); }} />
              <SaveFeedback state={ssLaw.state} />
            </div>
          </FieldRow>
          <div className="flex items-center gap-3 mt-3 text-xs text-muted-foreground">
            <span>
              {law?.status.built_at
                ? t("settings.nettsok.law.status", { date: law.status.built_at, docs: law.status.documents, paras: law.status.paragraphs })
                : t("settings.nettsok.law.status_none")}
            </span>
            <Button size="sm" variant="outline" onClick={() => { void refreshLaw(); }} disabled={!!law?.refreshing}>
              {law?.refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : t("settings.nettsok.law.refresh")}
            </Button>
          </div>
          {law?.attribution && <p className="text-[11px] text-muted-foreground mt-3">{law.attribution}</p>}
        </CardContent>
      </Card>

      {/* ── Vitlistede nettsider ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.nettsok.trusted.title")}</CardTitle>
          <CardDescription>{t("settings.nettsok.trusted.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          {/* Open web on/off (P121 Phase 2) — saved immediately */}
          <FieldRow label={t("settings.nettsok.trusted.open_web_label")} hint={t("settings.nettsok.trusted.open_web_hint")}>
            <div className="flex items-center gap-3">
              <Switch checked={wsLocal.open_web} onCheckedChange={(v: boolean) => { void toggleOpenWeb(v); }} />
              <SaveFeedback state={ssOpenWeb.state} />
            </div>
          </FieldRow>
          {/* How careful Kåre's probability words are (P121 D8) — saved immediately */}
          <FieldRow label={t("settings.nettsok.trusted.caution_label")} hint={t("settings.nettsok.trusted.caution_hint")}>
            <div className="flex gap-2">
              {(["careful", "balanced", "open"] as const).map(c => (
                <Button key={c} size="sm" variant={wsLocal.caution === c ? "default" : "outline"} onClick={() => { void saveCaution(c); }}>
                  {t(`settings.nettsok.trusted.caution_${c}`)}
                </Button>
              ))}
            </div>
          </FieldRow>
          <Separator className="my-3" />
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
                      {e.description && <span className="text-muted-foreground"> — {e.description}</span>}
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
                      <span className="text-xs text-muted-foreground flex-1">{entry.description || "—"}</span>
                      <Select value={entry.type ?? "other_serious"} onValueChange={(v: string | null) => { if (v) updateEntry(cat, idx, { type: v as SourceType }); }}>
                        <SelectTrigger className="w-44 h-7 text-xs"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {SOURCE_TYPES.map(st => <SelectItem key={st} value={st}>{t(`settings.nettsok.trusted.type_${st}`)}</SelectItem>)}
                        </SelectContent>
                      </Select>
                      <Select value={entry.trust ?? "normal"} onValueChange={(v: string | null) => { if (v) updateEntry(cat, idx, { trust: v as TrustLevel }); }}>
                        <SelectTrigger className="w-28 h-7 text-xs"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {TRUST_LEVELS.map(tl => <SelectItem key={tl} value={tl}>{t(`settings.nettsok.trusted.trust_${tl}`)}</SelectItem>)}
                        </SelectContent>
                      </Select>
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
                            setNewDomainInput(p => ({ ...p, [cat]: { ...(p[cat] ?? EMPTY_ENTRY), domain: e.target.value } }))
                          }
                          className="w-40 font-mono text-xs h-7"
                        />
                        <span className="text-[10px] text-muted-foreground px-0.5">{t("settings.nettsok.trusted.domain_format_hint")}</span>
                      </div>
                      <Input
                        placeholder={t("settings.nettsok.trusted.short_desc_placeholder")}
                        value={newDomainInput[cat]?.description ?? ""}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setNewDomainInput(p => ({ ...p, [cat]: { ...(p[cat] ?? EMPTY_ENTRY), description: e.target.value } }))
                        }
                        className="flex-1 text-xs h-7"
                      />
                      <Select value={newDomainInput[cat]?.type ?? "other_serious"} onValueChange={(v: string | null) => { if (v) setNewDomainInput(p => ({ ...p, [cat]: { ...(p[cat] ?? EMPTY_ENTRY), type: v as SourceType } })); }}>
                        <SelectTrigger className="w-44 h-7 text-xs"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {SOURCE_TYPES.map(st => <SelectItem key={st} value={st}>{t(`settings.nettsok.trusted.type_${st}`)}</SelectItem>)}
                        </SelectContent>
                      </Select>
                      <Select value={newDomainInput[cat]?.trust ?? "normal"} onValueChange={(v: string | null) => { if (v) setNewDomainInput(p => ({ ...p, [cat]: { ...(p[cat] ?? EMPTY_ENTRY), trust: v as TrustLevel } })); }}>
                        <SelectTrigger className="w-28 h-7 text-xs"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {TRUST_LEVELS.map(tl => <SelectItem key={tl} value={tl}>{t(`settings.nettsok.trusted.trust_${tl}`)}</SelectItem>)}
                        </SelectContent>
                      </Select>
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
                placeholder={t("settings.nettsok.trusted.new_category_placeholder")}
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

          {/* Blocked domains (P121 Phase 2) — never used, saved immediately */}
          <Separator className="my-4" />
          <div className="text-sm font-medium">{t("settings.nettsok.trusted.blocked_title")}</div>
          <p className="text-xs text-muted-foreground mb-2">{t("settings.nettsok.trusted.blocked_hint")}</p>
          <div className="flex flex-wrap gap-2 mb-2">
            {blocked.length === 0 && <span className="text-xs text-muted-foreground">{t("common.no_domains")}</span>}
            {blocked.map(d => (
              <span key={d} className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 font-mono text-xs">
                {d}
                <button onClick={() => { void saveBlocked(blocked.filter(x => x !== d)); }} className="text-destructive/60 hover:text-destructive">✕</button>
              </span>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <Input
              placeholder="domene.no"
              value={newBlocked}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNewBlocked(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") addBlocked(); }}
              className="w-52 font-mono text-xs h-8"
            />
            <Button variant="outline" size="sm" onClick={addBlocked} disabled={!newBlocked.trim()}>{t("common.add")}</Button>
            <SaveFeedback state={ssBlocked.state} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
