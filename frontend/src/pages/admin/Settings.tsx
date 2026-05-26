import { useEffect, useState, useCallback, useRef } from "react";
import Cameras from "./Cameras";
import { useTranslation } from "react-i18next";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
  CheckCircle2, XCircle, Loader2, Eye, EyeOff, Wifi, WifiOff, RotateCcw, ChevronDown, ChevronUp, Search,
} from "lucide-react";
import axios from "axios";
import {
  apiGetLlmSettings, apiPutLlmRole, apiRestartVllmDocker,
  apiGetServices, apiPutHa, apiPutMqtt, apiPutFrigate, apiPutPlex, apiPutEmbeddingBackend, apiPutMemoryEmbedBackend, apiPutVoiceBackend,
  apiGetHaToken, apiPutHaToken,
  apiGetHaBridge, apiPutHaBridge,
  apiGetSecrets, apiPutSecret,
  apiGetPlexToken, apiPutPlexToken,
  apiTestConnection,
  apiGetWeather, apiPutWeather,
  apiGetWebsearch, apiPutWebsearch,
  apiGetTrustedSources, apiPutTrustedSources,
  apiGetReflectionSettings, apiPutReflectionSettings,
  apiGetReflectionMeetingSettings, apiPutReflectionMeetingSettings,
  apiGetDevMeetingSettings, apiPutDevMeetingSettings,
  apiGetImageSettings, apiPutImageSettings, apiGetImageStats,
  apiGetKareSettings, apiPutKareSettings,
  apiGetCapabilities, apiPutCapabilities,
  apiGetVpnSettings, apiPutVpnSettings,
  apiGetLanguage, apiPutLanguage,
  apiGetAgentTools, apiPutAgentTools,
  apiGetMeetingRoles, apiPutMeetingRoles,
  apiGetGpus, apiGetOllamaModels, apiDeleteOllamaModel, apiPullModel, apiGetPullStatus,
  apiDiscoverOllama,
  apiRestartService,
  type GpuInfo, type OllamaPullStatus, type OllamaEnvConfig,
  type LlmRoleConfig, type VllmDockerConfig,
  type WeatherProvider, type WeatherConfig, type WebsearchConfig, type TrustedSources,
  type ReflectionConfig, type ImageSettings, type ImageUserStats,
  type ContributorMode, type CapabilitiesConfig, type ServiceEntry, type PersonalityMode,
  type AgentToolsConfig, type MeetingRolesConfig,
  type ReflectionMeetingSettings, type DevMeetingSettings,
} from "@/services/api";

const BASE = `http://${window.location.hostname}:8000`;
const token = () => sessionStorage.getItem("kaare_token");


type SaveState = "idle" | "saving" | "saved" | "error";

function useSaveState() {
  const [state, setState] = useState<SaveState>("idle");
  const saved = () => { setState("saved"); setTimeout(() => setState("idle"), 3000); };
  const error = () => { setState("error"); setTimeout(() => setState("idle"), 4000); };
  return { state, saving: () => setState("saving"), saved, error };
}

function SaveFeedback({ state }: { state: SaveState }) {
  const { t } = useTranslation();
  if (state === "saving") return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />;
  if (state === "saved")  return <span className="flex items-center gap-1 text-sm text-green-500"><CheckCircle2 className="h-4 w-4" /> {t("common.saved")}</span>;
  if (state === "error")  return <span className="flex items-center gap-1 text-sm text-destructive"><XCircle className="h-4 w-4" /> {t("common.error")}</span>;
  return null;
}

function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[200px_1fr] items-start gap-4 py-3">
      <div>
        <Label className="text-sm font-medium">{label}</Label>
        {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function TestButton({ url, disabled }: { url: string; disabled?: boolean }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const test = async () => {
    if (!url) return;
    setStatus("testing");
    try {
      const r = await apiTestConnection(url);
      setStatus(r.ok ? "ok" : "fail");
    } catch {
      setStatus("fail");
    }
    setTimeout(() => setStatus("idle"), 5000);
  };
  return (
    <Button variant="outline" size="sm" onClick={test} disabled={disabled || !url || status === "testing"} className="gap-2">
      {status === "testing" ? <Loader2 className="h-3 w-3 animate-spin" /> :
       status === "ok"      ? <Wifi className="h-3 w-3 text-green-500" /> :
       status === "fail"    ? <WifiOff className="h-3 w-3 text-destructive" /> :
                              <Wifi className="h-3 w-3" />}
      {status === "testing" ? t("common.testing") : status === "ok" ? "OK" : status === "fail" ? t("common.error") : t("common.test")}
    </Button>
  );
}

function MaskedInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative flex items-center">
      <Input
        type={show ? "text" : "password"}
        value={value}
        onChange={(e: React.ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        placeholder={placeholder ?? "••••••••"}
        className="pr-10 font-mono text-sm"
      />
      <button
        type="button"
        onClick={() => setShow(s => !s)}
        className="absolute right-3 text-muted-foreground hover:text-foreground"
      >
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}


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

function TabGenerelt() {
  const { t, i18n } = useTranslation();
  const [lok, setLok] = useState<Location>({ city: "", postal_code: "", country: "", lat: 0, lon: 0, timezone: "" });
  const [lang, setLang] = useState<string>("nb");
  const [kareLang, setKareLang] = useState<string>("nb");
  const ssLoc  = useSaveState();
  const ssLang = useSaveState();

  useEffect(() => {
    axios.get(`${BASE}/api/settings`, { headers: { Authorization: `Bearer ${token()}` } })
      .then(r => { if (r.data.location) setLok(r.data.location); })
      .catch(() => {});
    apiGetLanguage().then(r => {
      setLang(r.language || "nb");
      setKareLang(r.kare_language || r.language || "nb");
    }).catch(() => {});
  }, []);

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
    </div>
  );
}


function TabHomeAssistant() {
  const { t } = useTranslation();
  const [haUrl, setHaUrl]       = useState("");
  const [haTimeout, setHaTimeout] = useState("5");
  const [haToken, setHaToken]   = useState("");
  const [tokenInfo, setTokenInfo] = useState<{ is_set: boolean; masked: string } | null>(null);
  const [bridgeLogUrl, setBridgeLogUrl] = useState("");
  const [bridgeTimeout, setBridgeTimeout] = useState("5");
  const [bridgeActions, setBridgeActions] = useState("");

  const ssGateway = useSaveState();
  const ssToken   = useSaveState();
  const ssBridge  = useSaveState();

  useEffect(() => {
    apiGetServices().then(d => {
      setHaUrl(d.home_assistant.url);
      setHaTimeout(String(d.home_assistant.timeout));
    }).catch(() => {});
    apiGetHaToken().then(setTokenInfo).catch(() => {});
    apiGetHaBridge().then(d => {
      setBridgeLogUrl(d.log_url);
      setBridgeTimeout(d.timeout);
      setBridgeActions(d.allowed_actions);
    }).catch(() => {});
  }, []);

  const saveGateway = async () => {
    ssGateway.saving();
    try {
      await apiPutHa({ url: haUrl, timeout: Number(haTimeout) });
      ssGateway.saved();
    } catch { ssGateway.error(); }
  };

  const saveToken = async () => {
    ssToken.saving();
    try {
      await apiPutHaToken(haToken);
      setHaToken("");
      const updated = await apiGetHaToken();
      setTokenInfo(updated);
      ssToken.saved();
    } catch { ssToken.error(); }
  };

  const saveBridge = async () => {
    ssBridge.saving();
    try {
      await apiPutHaBridge({ log_url: bridgeLogUrl, timeout: bridgeTimeout, allowed_actions: bridgeActions });
      ssBridge.saved();
    } catch { ssBridge.error(); }
  };

  return (
    <div className="space-y-6">
      {/* Gateway */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.ha.gateway.title")}</CardTitle>
          <CardDescription>{t("settings.ha.gateway.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.ha.gateway.url_label")} hint={t("settings.ha.gateway.url_hint")}>
              <div className="flex gap-2">
                <Input value={haUrl} onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setHaUrl(e.target.value)} placeholder="http://192.168.0.x:8123" />
                <TestButton url={haUrl} />
              </div>
            </FieldRow>
            <FieldRow label={t("settings.ha.gateway.timeout_label")} hint={t("settings.ha.gateway.timeout_hint")}>
              <Input type="number" value={haTimeout} onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setHaTimeout(e.target.value)} className="w-24" />
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveGateway} disabled={ssGateway.state === "saving"}>
              {ssGateway.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssGateway.state} />
          </div>
        </CardContent>
      </Card>

      {/* HA Gateway — avansert */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.ha.advanced.title")}</CardTitle>
          <CardDescription>{t("settings.ha.advanced.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.ha.advanced.log_url_label")} hint={t("settings.ha.advanced.log_url_hint")}>
              <Input value={bridgeLogUrl} onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setBridgeLogUrl(e.target.value)} placeholder="http://127.0.0.1:8000/api/ha_log" />
            </FieldRow>
            <FieldRow label={t("settings.ha.advanced.timeout_label")}>
              <Input type="number" value={bridgeTimeout} onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setBridgeTimeout(e.target.value)} className="w-24" />
            </FieldRow>
            <FieldRow label={t("settings.ha.advanced.allowed_label")} hint={t("settings.ha.advanced.allowed_hint")}>
              <Input value={bridgeActions} onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setBridgeActions(e.target.value)} className="font-mono text-xs" />
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveBridge} disabled={ssBridge.state === "saving"}>
              {ssBridge.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssBridge.state} />
          </div>
        </CardContent>
      </Card>

      {/* HA Token */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.ha.token.title")}</CardTitle>
          <CardDescription>
            {t("settings.ha.token.description")}
            {tokenInfo && (
              <span className="ml-2">
                {tokenInfo.is_set
                  ? <Badge variant="outline" className="text-green-500 border-green-500/30">{t("common.set_masked", { masked: tokenInfo.masked })}</Badge>
                  : <Badge variant="destructive">{t("common.not_set")}</Badge>}
              </span>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <FieldRow label={t("settings.ha.token.new_label")} hint={t("settings.ha.token.new_hint")}>
            <MaskedInput value={haToken} onChange={setHaToken} placeholder="eyJhbG..." />
          </FieldRow>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveToken} disabled={ssToken.state === "saving" || !haToken}>
              {ssToken.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.update_token")}
            </Button>
            <SaveFeedback state={ssToken.state} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}


function TabMqtt() {
  const { t } = useTranslation();
  const [host, setHost] = useState("");
  const [port, setPort] = useState("1883");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [tlsEnabled, setTlsEnabled] = useState(false);
  const [topicPrefix, setTopicPrefix] = useState("frigate");
  const [clientId, setClientId] = useState("");
  const [reconnectInterval, setReconnectInterval] = useState("30");
  const ssMqtt = useSaveState();

  const [vpnHost, setVpnHost] = useState("");
  const [vpnPort, setVpnPort] = useState("51820");
  const ssVpn = useSaveState();

  useEffect(() => {
    apiGetServices().then(d => {
      setHost(d.mqtt.host);
      setPort(String(d.mqtt.port));
      setUsername(d.mqtt.username ?? "");
      setTlsEnabled(d.mqtt.tls_enabled ?? false);
      setTopicPrefix(d.mqtt.topic_prefix ?? "frigate");
      setClientId(d.mqtt.client_id ?? "");
      setReconnectInterval(String(d.mqtt.reconnect_interval ?? 30));
    }).catch(() => {});
    apiGetVpnSettings().then(d => {
      setVpnHost(d.duckdns_host);
      setVpnPort(String(d.wg_port));
    }).catch(() => {});
  }, []);

  const saveMqtt = async () => {
    ssMqtt.saving();
    try {
      const payload: Record<string, string | number | boolean> = {
        host,
        port: Number(port),
        username,
        tls_enabled: tlsEnabled,
        topic_prefix: topicPrefix,
        client_id: clientId,
        reconnect_interval: Number(reconnectInterval),
      };
      if (password) payload.password = password;
      await apiPutMqtt(payload);
      setPassword("");
      ssMqtt.saved();
    } catch { ssMqtt.error(); }
  };

  const saveVpn = async () => {
    ssVpn.saving();
    try {
      await apiPutVpnSettings({ duckdns_host: vpnHost, wg_port: Number(vpnPort) });
      ssVpn.saved();
    } catch { ssVpn.error(); }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.mqtt.card.title")}</CardTitle>
          <CardDescription>{t("settings.mqtt.card.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.mqtt.card.host_label")} hint={t("settings.mqtt.card.host_hint")}>
              <div className="flex gap-2">
                <Input value={host} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHost(e.target.value)} placeholder="192.168.0.100" />
                <TestButton url={`http://${host}:${port}`} />
              </div>
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.port_label")} hint={t("settings.mqtt.card.port_hint")}>
              <Input type="number" value={port} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPort(e.target.value)} className="w-28" />
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.username_label")} hint={t("settings.mqtt.card.username_hint")}>
              <Input value={username} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setUsername(e.target.value)} placeholder="mqtt_user" />
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.password_label")} hint={t("settings.mqtt.card.password_hint")}>
              <div className="flex gap-2">
                <Input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPassword(e.target.value)}
                  placeholder="(uendret)"
                />
                <Button variant="ghost" size="icon" onClick={() => setShowPassword(v => !v)}>
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
              </div>
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.tls_label")} hint={t("settings.mqtt.card.tls_hint")}>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="mqtt-tls"
                  checked={tlsEnabled}
                  onChange={e => setTlsEnabled(e.target.checked)}
                  className="h-4 w-4 accent-primary cursor-pointer"
                />
                <label htmlFor="mqtt-tls" className="text-sm cursor-pointer select-none">{t("settings.mqtt.card.tls_enable")}</label>
              </div>
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.topic_label")} hint={t("settings.mqtt.card.topic_hint")}>
              <Input value={topicPrefix} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTopicPrefix(e.target.value)} placeholder="frigate" className="w-48" />
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.client_label")} hint={t("settings.mqtt.card.client_hint")}>
              <Input value={clientId} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setClientId(e.target.value)} placeholder="(auto)" className="w-48" />
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.reconnect_label")} hint={t("settings.mqtt.card.reconnect_hint")}>
              <Input type="number" value={reconnectInterval} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setReconnectInterval(e.target.value)} className="w-28" />
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveMqtt} disabled={ssMqtt.state === "saving"}>
              {ssMqtt.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssMqtt.state} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("settings.mqtt.vpn.title")}</CardTitle>
          <CardDescription>{t("settings.mqtt.vpn.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.mqtt.vpn.host_label")} hint={t("settings.mqtt.vpn.host_hint")}>
              <Input value={vpnHost} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setVpnHost(e.target.value)} placeholder="mitt-navn.duckdns.org" />
            </FieldRow>
            <FieldRow label={t("settings.mqtt.vpn.port_label")} hint={t("settings.mqtt.vpn.port_hint")}>
              <Input type="number" value={vpnPort} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setVpnPort(e.target.value)} className="w-28" />
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveVpn} disabled={ssVpn.state === "saving"}>
              {ssVpn.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssVpn.state} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}


const PROVIDER_OPTIONS = [
  { value: "ollama" },
  { value: "vllm" },
  { value: "openai" },
  { value: "nvidia" },
  { value: "huggingface" },
  { value: "openvino" },
  { value: "other" },
];

const LLM_ROLE_LABELS: Record<string, { port?: string }> = {
  default:     { port: "11440 (vLLM)" },
  miss_kare:   { port: "11445" },
  pettersmart: { port: "11445" },
  library:     { port: "11447" },
  fallback:    { port: "11445" },
  cloud:       { port: "Ekstern provider" },
  image_edit:  { port: "Ekstern API" },
};

const THINK_OPTIONS = [
  { value: "__unset__", labelKey: "settings.llm.think_unset" },
  { value: "true",      labelKey: "settings.llm.think_enabled" },
  { value: "false",     labelKey: "settings.llm.think_disabled" },
];

const AGENT_TOGGLEABLE = ["miss_kare", "pettersmart", "library", "fallback", "cloud", "image_edit"];

function LlmRoleCard({ role, config, onSaved, allConfigs }: { role: string; config: LlmRoleConfig; onSaved: () => void; allConfigs: Record<string, LlmRoleConfig> }) {
  const { t } = useTranslation();
  const [local, setLocal] = useState<LlmRoleConfig>({ ...config });
  const [modelName, setModelName] = useState(config.model ?? "");
  const [agentEnabled, setAgentEnabled] = useState(config.enabled ?? true);
  const [collapsed, setCollapsed] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [dockerRestarting, setDockerRestarting] = useState(false);
  const [dockerStatus, setDockerStatus] = useState<"idle" | "ok" | "error">("idle");
  const ss = useSaveState();

  const toggleEnabled = async (v: boolean) => {
    setAgentEnabled(v);
    try {
      await apiPutLlmRole(role, { enabled: v } as Parameters<typeof apiPutLlmRole>[1]);
    } catch {
      setAgentEnabled(!v);
    }
  };
  const info = LLM_ROLE_LABELS[role] ?? {};
  const roleLabel = t(`settings.llm.role_labels.${role}`, role);
  const isToggleable = AGENT_TOGGLEABLE.includes(role);

  const isOllama = local.provider === "ollama" || local.provider === "openvino";
  const isVllm   = local.provider === "vllm";
  const isLocal  = isOllama || isVllm;

  const [manageOpen, setManageOpen]               = useState(false);
  const [gpus, setGpus]                           = useState<GpuInfo[]>([]);
  const [gpusLoaded, setGpusLoaded]               = useState(false);
  const [installedModels, setInstalledModels]     = useState<string[]>([]);
  const [pullModelInput, setPullModelInput]        = useState("");
  const [pullStatus, setPullStatus]               = useState<OllamaPullStatus | null>(null);
  const [deletingModel, setDeletingModel]         = useState<string | null>(null);
  const [gpuSaving, setGpuSaving]                 = useState(false);
  const pollingRef                                 = useRef<ReturnType<typeof setInterval> | null>(null);

  const gpuIdValue: number =
    isVllm ? (local.vllm_docker?.gpu_id ?? -1) : (local.gpu_id ?? -1);

  const setGpuId = (val: number) => {
    if (isVllm) setDockerOpt("gpu_id", val === -1 ? undefined : val);
    else setLocal(p => ({ ...p, gpu_id: val === -1 ? undefined : val }));
  };

  const handleManageToggle = async (open: boolean) => {
    setManageOpen(open);
    if (open && !gpusLoaded) {
      const [gpuList, models] = await Promise.all([
        apiGetGpus().catch(() => []),
        isOllama ? apiGetOllamaModels(role).catch(() => []) : Promise.resolve([]),
      ]);
      setGpus(gpuList);
      setGpusLoaded(true);
      setInstalledModels(models);
    } else if (open && isOllama) {
      apiGetOllamaModels(role).then(setInstalledModels).catch(() => {});
    }
  };

  const startPull = async () => {
    if (!pullModelInput.trim()) return;
    setPullStatus({ pulling: true, status: "Starter…", completed: 0, total: 0, error: null });
    try {
      await apiPullModel(role, pullModelInput.trim());
    } catch {
      setPullStatus({ pulling: false, status: "", completed: 0, total: 0, error: "Kunne ikke starte nedlasting" });
      return;
    }
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingRef.current = setInterval(async () => {
      try {
        const st = await apiGetPullStatus(role);
        setPullStatus(st);
        if (!st.pulling) {
          clearInterval(pollingRef.current!);
          pollingRef.current = null;
          if (!st.error) {
            apiGetOllamaModels(role).then(setInstalledModels).catch(() => {});
            setPullModelInput("");
          }
        }
      } catch { /* ignore transient network errors */ }
    }, 500);
  };

  const deleteModel = async (model: string) => {
    setDeletingModel(model);
    try {
      await apiDeleteOllamaModel(role, model);
      setInstalledModels(prev => prev.filter(m => m !== model));
    } finally {
      setDeletingModel(null);
    }
  };

  const saveGpu = async () => {
    setGpuSaving(true);
    try {
      if (isVllm) {
        await apiPutLlmRole(role, { vllm_docker: { ...local.vllm_docker } } as Parameters<typeof apiPutLlmRole>[1]);
      } else {
        await apiPutLlmRole(role, { gpu_id: local.gpu_id ?? null } as Parameters<typeof apiPutLlmRole>[1]);
      }
    } finally {
      setGpuSaving(false);
    }
  };

  const [ollamaEnv, setOllamaEnv] = useState<OllamaEnvConfig>(config.ollama_env ?? {});
  const [envSaving, setEnvSaving] = useState(false);

  const [discovering, setDiscovering] = useState(false);
  const [discoverResults, setDiscoverResults] = useState<{ url: string; models: string[] }[] | null>(null);

  const setEnv = <K extends keyof OllamaEnvConfig>(k: K, v: OllamaEnvConfig[K]) =>
    setOllamaEnv(prev => ({ ...prev, [k]: v }));

  const saveOllamaEnv = async () => {
    setEnvSaving(true);
    try {
      await apiPutLlmRole(role, { ollama_env: ollamaEnv } as Parameters<typeof apiPutLlmRole>[1]);
    } finally {
      setEnvSaving(false);
    }
  };

  const handleDiscover = async () => {
    setDiscovering(true);
    setDiscoverResults(null);
    try {
      const { found } = await apiDiscoverOllama();
      if (found.length === 1) {
        setLocal(p => ({ ...p, base_url: found[0].url }));
        setDiscoverResults(null);
      } else {
        setDiscoverResults(found);
      }
    } catch {
      setDiscoverResults([]);
    } finally {
      setDiscovering(false);
    }
  };

  const pulling = pullStatus?.pulling ?? false;
  const pullPct = pullStatus && pullStatus.total > 0
    ? Math.round((pullStatus.completed / pullStatus.total) * 100) : 0;
  const toGB = (b: number) => b > 0 ? (b / 1e9).toFixed(1) + " GB" : "–";

  // Detect whether this role shares its Ollama model with another role (num_ctx must match)
  const sharedWithRole: string | undefined = role === "fallback"
    ? Object.entries(allConfigs).find(([r, c]) => r !== role && c.model_role === local.model_role)?.[0]
    : undefined;
  const numCtxLocked = sharedWithRole !== undefined;
  const sharedRoleLabel = sharedWithRole
    ? t(`settings.llm.role_labels.${sharedWithRole}`, sharedWithRole)
    : "";

  const setOpt = (k: string, v: number) =>
    setLocal(p => ({ ...p, options: { ...(p.options ?? {}), [k]: v } }));

  const setDockerOpt = (k: keyof VllmDockerConfig, v: unknown) =>
    setLocal(p => ({ ...p, vllm_docker: { ...(p.vllm_docker ?? {}), [k]: v } }));

  const restartDocker = async () => {
    setDockerRestarting(true);
    setDockerStatus("idle");
    try {
      const r = await apiRestartVllmDocker(role);
      setDockerStatus(r.ok ? "ok" : "error");
    } catch {
      setDockerStatus("error");
    } finally {
      setDockerRestarting(false);
      setTimeout(() => setDockerStatus("idle"), 6000);
    }
  };

  const save = async () => {
    ss.saving();
    try {
      const payload: Record<string, unknown> = {
        provider: local.provider,
        base_url: local.base_url,
        model: modelName,
        timeout: local.timeout ?? null,
      };
      if (isOllama) {
        const thinkRaw = (local as unknown as Record<string, unknown>)["_thinkStr"] as string | undefined;
        if (thinkRaw && thinkRaw !== "__unset__") {
          payload.think = thinkRaw === "true" ? true : false;
        } else if (thinkRaw === "__unset__") {
          payload.think = null;
        }
        const opts = { ...(local.options ?? {}) };
        if (numCtxLocked) delete (opts as Record<string, number>).num_ctx;
        payload.options = opts;
      } else if (isVllm) {
        const thinkRaw = (local as unknown as Record<string, unknown>)["_thinkStr"] as string | undefined;
        if (thinkRaw && thinkRaw !== "__unset__") {
          payload.think = thinkRaw === "true" ? true : false;
        } else if (thinkRaw === "__unset__") {
          payload.think = null;
        }
        payload.options = { ...(local.options ?? {}) };
        payload.vllm_docker = { ...(local.vllm_docker ?? {}) };
      } else {
        payload.temperature = local.temperature;
        payload.top_p = local.top_p;
        payload.max_tokens = local.max_tokens;
        if (apiKey) payload.api_key = apiKey;
      }
      if (isToggleable) payload.enabled = agentEnabled;
      await apiPutLlmRole(role, payload as Parameters<typeof apiPutLlmRole>[1]);
      setApiKey("");
      ss.saved();
      onSaved();
    } catch { ss.error(); }
  };

  const thinkStr = (() => {
    const t = (local as unknown as Record<string, unknown>)["_thinkStr"] as string | undefined;
    if (t !== undefined) return t;
    if (local.think === true)  return "true";
    if (local.think === false) return "false";
    return "__unset__";
  })();

  const setThink = (v: string | null) => { if (v !== null) setLocal(p => ({ ...p, _thinkStr: v } as LlmRoleConfig)); };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">{roleLabel}</CardTitle>
            {info.port && <CardDescription className="text-xs">Port {info.port}</CardDescription>}
            {role === "fallback" && <p className="text-xs text-muted-foreground mt-0.5 italic">{t("settings.llm.role_fallback_desc")}</p>}
          </div>
          <div className="flex items-center gap-3">
            {isToggleable && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">{agentEnabled ? t("common.enabled") : t("common.disabled")}</span>
                <Switch checked={agentEnabled} onCheckedChange={toggleEnabled} className="data-checked:bg-green-600 data-unchecked:bg-red-600" />
              </div>
            )}
            <Badge variant="outline" className="font-mono text-xs">{role}</Badge>
          </div>
        </div>
        <div className="flex items-center justify-between pt-1">
          <span className="text-xs text-muted-foreground font-mono truncate max-w-[60%]">
            {modelName ? modelName : <span className="italic opacity-50">{t("common.no_model_set")}</span>}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setCollapsed(c => !c)}
          >
            {collapsed ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
            <span className="ml-1">{collapsed ? t("common.show") : t("common.hide")}</span>
          </Button>
        </div>
      </CardHeader>
      {!collapsed && <CardContent>
        <div className="divide-y divide-border">
          <FieldRow label={t("settings.llm.model_label")} hint={t("settings.llm.model_hint")}>
            <Input
              value={modelName}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelName(e.target.value)}
              className="font-mono text-sm"
              placeholder="f.eks. qwen3:8b"
            />
          </FieldRow>

          <FieldRow label={t("settings.llm.provider_label")} hint={t("settings.llm.provider_hint")}>
            <Select value={local.provider} onValueChange={(v: string | null) => { if (v) setLocal(p => ({ ...p, provider: v as LlmRoleConfig["provider"] })); }}>
              <SelectTrigger className="w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROVIDER_OPTIONS.map(o => (
                  <SelectItem key={o.value} value={o.value}>{t(`settings.llm.providers.${o.value}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FieldRow>

          <FieldRow label="Base URL" hint={isOllama ? t("settings.llm.base_url_hint_ollama") : t("settings.llm.base_url_hint_api")}>
            <div className="flex gap-2">
              <Input
                value={local.base_url}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setLocal(p => ({ ...p, base_url: e.target.value }))}
                className="font-mono text-sm"
              />
              <TestButton url={local.base_url} />
              {isOllama && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleDiscover}
                  disabled={discovering}
                  title="Søk etter Ollama-instanser på nettverket"
                >
                  {discovering ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
                </Button>
              )}
            </div>
            {isOllama && discoverResults !== null && (
              <div className="mt-2">
                {discoverResults.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Ingen Ollama funnet. Sjekk at Ollama er startet.</p>
                ) : (
                  <div className="flex flex-col gap-1">
                    <p className="text-xs text-muted-foreground mb-1">Fant {discoverResults.length} instans{discoverResults.length > 1 ? "er" : ""}:</p>
                    {discoverResults.map(r => (
                      <button
                        key={r.url}
                        type="button"
                        onClick={() => { setLocal(p => ({ ...p, base_url: r.url })); setDiscoverResults(null); }}
                        className="text-left text-xs px-2 py-1.5 rounded border border-border hover:bg-accent hover:text-accent-foreground transition-colors font-mono"
                      >
                        <span className="font-medium">{r.url}</span>
                        {r.models.length > 0 && (
                          <span className="text-muted-foreground ml-2">— {r.models.slice(0, 3).join(", ")}{r.models.length > 3 ? ` +${r.models.length - 3}` : ""}</span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </FieldRow>

          {!isOllama && (
            <FieldRow
              label={t("settings.llm.api_key_label")}
              hint={config.api_key_set ? t("settings.llm.api_key_set_hint", { masked: config.api_key_masked }) : t("settings.llm.api_key_unset_hint")}
            >
              <MaskedInput value={apiKey} onChange={setApiKey} placeholder={config.api_key_set ? "••• (oppdater)" : "Lim inn nøkkel"} />
            </FieldRow>
          )}

          {isOllama && (
            <FieldRow label={t("settings.llm.think_label")} hint={t("settings.llm.think_hint_ollama")}>
              <Select value={thinkStr} onValueChange={setThink}>
                <SelectTrigger className="w-56">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {THINK_OPTIONS.map(o => (
                    <SelectItem key={o.value} value={o.value}>{t(o.labelKey)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldRow>
          )}

          <FieldRow label={t("settings.llm.timeout_label")} hint={t("settings.llm.timeout_hint")}>
            <Input
              type="number"
              value={local.timeout ?? ""}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setLocal(p => ({ ...p, timeout: e.target.value ? Number(e.target.value) : null }))}
              className="w-24"
              placeholder="120"
            />
          </FieldRow>

          {isOllama && (
            <>
              <Separator className="my-1" />
              <p className="text-xs text-muted-foreground py-2">{t("settings.llm.ollama_params")}</p>
              {(["num_ctx", "num_predict", "temperature", "presence_penalty", "top_k", "top_p"] as const).map((k) => {
                const fieldKey = `settings.llm.ollama_fields.${k}`;
                if (k === "num_ctx" && numCtxLocked && sharedWithRole) {
                  const sharedVal = allConfigs[sharedWithRole]?.options?.num_ctx;
                  return (
                    <FieldRow key={k} label={t(`${fieldKey}.label`)} hint={t("settings.llm.ctx_locked_hint", { shared: sharedRoleLabel })}>
                      <div className="w-32 h-9 flex items-center px-3 rounded-md border bg-muted font-mono text-sm text-muted-foreground">
                        {sharedVal ?? "—"}
                      </div>
                    </FieldRow>
                  );
                }
                return (
                  <FieldRow key={k} label={t(`${fieldKey}.label`)} hint={t(`${fieldKey}.hint`)}>
                    <Input
                      type="number"
                      value={local.options?.[k] ?? ""}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setOpt(k, Number(e.target.value))}
                      className="w-32 font-mono text-sm"
                      step={["temperature", "presence_penalty", "top_p"].includes(k) ? "0.01" : "1"}
                    />
                  </FieldRow>
                );
              })}
            </>
          )}

          {isVllm && (
            <>
              <FieldRow label={t("settings.llm.think_label")} hint={t("settings.llm.think_hint_vllm")}>
                <Select value={thinkStr} onValueChange={setThink}>
                  <SelectTrigger className="w-56">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {THINK_OPTIONS.map(o => (
                      <SelectItem key={o.value} value={o.value}>{t(o.labelKey)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FieldRow>

              <Separator className="my-1" />
              <p className="text-xs text-muted-foreground py-2">{t("settings.llm.vllm_params")}</p>
              {(["max_tokens", "temperature", "top_p", "presence_penalty", "frequency_penalty"] as const).map((k) => {
                const step = k === "max_tokens" ? "1" : "0.01";
                return (
                  <FieldRow key={k} label={t(`settings.llm.vllm_fields.${k}.label`)} hint={t(`settings.llm.vllm_fields.${k}.hint`)}>
                    <Input
                      type="number"
                      value={local.options?.[k] ?? ""}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setOpt(k, Number(e.target.value))}
                      className="w-32 font-mono text-sm"
                      step={step}
                    />
                  </FieldRow>
                );
              })}

              <Separator className="my-1" />
              <p className="text-xs text-muted-foreground py-2">{t("settings.llm.vllm_docker_params")}</p>
              <FieldRow label={t("settings.llm.docker_fields.max_model_len.label")} hint={t("settings.llm.docker_fields.max_model_len.hint")}>
                <Input
                  type="number"
                  value={local.vllm_docker?.max_model_len ?? ""}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDockerOpt("max_model_len", Number(e.target.value))}
                  className="w-32 font-mono text-sm"
                  step="1024"
                />
              </FieldRow>
              <FieldRow label={t("settings.llm.docker_fields.gpu_memory_utilization.label")} hint={t("settings.llm.docker_fields.gpu_memory_utilization.hint")}>
                <Input
                  type="number"
                  value={local.vllm_docker?.gpu_memory_utilization ?? ""}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDockerOpt("gpu_memory_utilization", Number(e.target.value))}
                  className="w-32 font-mono text-sm"
                  step="0.01"
                  min="0.5"
                  max="1.0"
                />
              </FieldRow>
              <FieldRow label={t("settings.llm.docker_fields.kv_cache_dtype.label")} hint={t("settings.llm.docker_fields.kv_cache_dtype.hint")}>
                <Select
                  value={local.vllm_docker?.kv_cache_dtype ?? "fp8"}
                  onValueChange={(v: string | null) => { if (v) setDockerOpt("kv_cache_dtype", v); }}
                >
                  <SelectTrigger className="w-40">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="fp8">fp8</SelectItem>
                    <SelectItem value="fp16">fp16</SelectItem>
                    <SelectItem value="auto">auto</SelectItem>
                  </SelectContent>
                </Select>
              </FieldRow>
              <FieldRow label={t("settings.llm.docker_fields.max_num_seqs.label")} hint={t("settings.llm.docker_fields.max_num_seqs.hint")}>
                <Input
                  type="number"
                  value={local.vllm_docker?.max_num_seqs ?? ""}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDockerOpt("max_num_seqs", Number(e.target.value))}
                  className="w-24 font-mono text-sm"
                  step="1"
                  min="1"
                />
              </FieldRow>
            </>
          )}

          {!isOllama && !isVllm && (
            <>
              <Separator className="my-1" />
              <p className="text-xs text-muted-foreground py-2">{t("settings.llm.cloud_params")}</p>
              {(["temperature", "top_p", "max_tokens"] as const).map((k) => (
                <FieldRow key={k} label={t(`settings.llm.cloud_fields.${k}.label`)} hint={t(`settings.llm.cloud_fields.${k}.hint`)}>
                  <Input
                    type="number"
                    value={(local as Record<string, unknown>)[k] as number ?? ""}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setLocal(p => ({ ...p, [k]: Number(e.target.value) }))}
                    className="w-32 font-mono text-sm"
                    step={k !== "max_tokens" ? "0.01" : "1"}
                  />
                </FieldRow>
              ))}
            </>
          )}
        </div>

        {/* ── Administrer modell ─────────────────────────────────────────── */}
        {isLocal && (
          <>
            <Separator className="my-3" />
            <div className="flex items-center justify-between py-1">
              <div>
                <p className="text-sm font-medium">{t("settings.llm.manage_section_label")}</p>
                <p className="text-xs text-muted-foreground">{t("settings.llm.manage_section_hint")}</p>
              </div>
              <Switch checked={manageOpen} onCheckedChange={handleManageToggle} />
            </div>

            {manageOpen && (
              <div className="mt-3 space-y-3 rounded-md border border-border/50 bg-muted/30 p-3">

                {/* GPU-valg */}
                <FieldRow label={t("settings.llm.manage_gpu_label")} hint={t("settings.llm.manage_gpu_hint")}>
                  <Select
                    value={String(gpuIdValue)}
                    onValueChange={(v) => setGpuId(Number(v))}
                  >
                    <SelectTrigger className="w-56 font-mono text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="-1">{t("settings.llm.manage_gpu_cpu")}</SelectItem>
                      {gpus.map(g => (
                        <SelectItem key={g.id} value={String(g.id)}>
                          GPU {g.id} – {g.name} ({g.vram_gb} GB)
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FieldRow>

                <div className="flex items-center gap-2">
                  <Button size="sm" variant="outline" onClick={saveGpu} disabled={gpuSaving}>
                    {gpuSaving
                      ? <><Loader2 className="mr-1 h-3 w-3 animate-spin" />{t("common.saving")}</>
                      : t("settings.llm.manage_gpu_save_btn")
                    }
                  </Button>
                  <span className="text-xs text-muted-foreground">{t("settings.llm.manage_gpu_restart_note")}</span>
                </div>

                {/* Ytelsesinnstillinger — kun Ollama */}
                {isOllama && (
                  <>
                    <Separator className="my-2" />
                    <p className="text-xs font-medium text-muted-foreground mb-2">
                      {t("settings.llm.manage_perf_label")}
                    </p>

                    <FieldRow label={t("settings.llm.manage_num_threads_label")} hint={t("settings.llm.manage_num_threads_hint")}>
                      <Input
                        type="number" min={1} step={1}
                        className="w-24 font-mono text-sm"
                        placeholder={t("settings.llm.manage_auto")}
                        value={ollamaEnv.num_threads ?? ""}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setEnv("num_threads", e.target.value ? Number(e.target.value) : null)
                        }
                      />
                    </FieldRow>

                    <FieldRow label={t("settings.llm.manage_num_parallel_label")} hint={t("settings.llm.manage_num_parallel_hint")}>
                      <Input
                        type="number" min={1} step={1}
                        className="w-24 font-mono text-sm"
                        placeholder="1"
                        value={ollamaEnv.num_parallel ?? ""}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setEnv("num_parallel", e.target.value ? Number(e.target.value) : null)
                        }
                      />
                    </FieldRow>

                    <FieldRow label={t("settings.llm.manage_max_loaded_label")} hint={t("settings.llm.manage_max_loaded_hint")}>
                      <Input
                        type="number" min={1} step={1}
                        className="w-24 font-mono text-sm"
                        placeholder="1"
                        value={ollamaEnv.max_loaded_models ?? ""}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setEnv("max_loaded_models", e.target.value ? Number(e.target.value) : null)
                        }
                      />
                    </FieldRow>

                    <FieldRow label={t("settings.llm.manage_kv_cache_label")} hint={t("settings.llm.manage_kv_cache_hint")}>
                      <Select
                        value={ollamaEnv.kv_cache_type ?? "f16"}
                        onValueChange={(v) => setEnv("kv_cache_type", v)}
                      >
                        <SelectTrigger className="w-28 font-mono text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="f16">f16 (standard)</SelectItem>
                          <SelectItem value="q8_0">q8_0 (halvert)</SelectItem>
                          <SelectItem value="q4_0">q4_0 (kvartet)</SelectItem>
                        </SelectContent>
                      </Select>
                    </FieldRow>

                    <FieldRow label={t("settings.llm.manage_flash_attn_label")} hint={t("settings.llm.manage_flash_attn_hint")}>
                      <Switch
                        checked={ollamaEnv.flash_attention ?? false}
                        onCheckedChange={(v) => setEnv("flash_attention", v)}
                      />
                    </FieldRow>

                    <div className="flex items-center gap-2 mt-2">
                      <Button size="sm" variant="outline" onClick={saveOllamaEnv} disabled={envSaving}>
                        {envSaving
                          ? <><Loader2 className="mr-1 h-3 w-3 animate-spin" />{t("common.saving")}</>
                          : t("settings.llm.manage_perf_save_btn")
                        }
                      </Button>
                      <span className="text-xs text-muted-foreground">{t("settings.llm.manage_gpu_restart_note")}</span>
                    </div>
                  </>
                )}

                {/* Pull-modell — kun Ollama */}
                {isOllama && (
                  <>
                    <Separator className="my-2" />
                    <FieldRow label={t("settings.llm.manage_pull_label")} hint={t("settings.llm.manage_pull_hint")}>
                      <div className="flex gap-2">
                        <Input
                          className="font-mono text-sm w-48"
                          placeholder={t("settings.llm.manage_pull_placeholder")}
                          value={pullModelInput}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPullModelInput(e.target.value)}
                          onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => { if (e.key === "Enter" && !pulling) startPull(); }}
                          disabled={pulling}
                        />
                        <Button size="sm" onClick={startPull} disabled={pulling || !pullModelInput.trim()}>
                          {pulling
                            ? <Loader2 className="h-4 w-4 animate-spin" />
                            : t("settings.llm.manage_pull_btn")
                          }
                        </Button>
                      </div>
                    </FieldRow>

                    {/* Progressbar */}
                    {pullStatus && (pulling || pullStatus.error) && (
                      <div className="space-y-1">
                        <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-primary transition-all duration-300"
                            style={{ width: `${pullPct}%` }}
                          />
                        </div>
                        <div className="flex justify-between text-xs text-muted-foreground">
                          <span>{pullStatus.status}</span>
                          {pullStatus.total > 0 && (
                            <span>{toGB(pullStatus.completed)} / {toGB(pullStatus.total)}</span>
                          )}
                        </div>
                        {pullStatus.error && (
                          <p className="text-xs text-destructive">{pullStatus.error}</p>
                        )}
                      </div>
                    )}

                    {/* Installerte modeller */}
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-1">
                        {t("settings.llm.manage_installed_label")}
                      </p>
                      {installedModels.length === 0 ? (
                        <p className="text-xs text-muted-foreground italic">
                          {t("settings.llm.manage_installed_empty")}
                        </p>
                      ) : (
                        <div className="space-y-1">
                          {installedModels.map(m => (
                            <div key={m} className="flex items-center justify-between rounded bg-background/50 px-2 py-1">
                              <span className="font-mono text-xs">{m}</span>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                                onClick={() => deleteModel(m)}
                                disabled={deletingModel === m}
                              >
                                {deletingModel === m
                                  ? <Loader2 className="h-3 w-3 animate-spin" />
                                  : t("settings.llm.manage_delete_btn")
                                }
                              </Button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
            )}
          </>
        )}

        <div className="flex items-center gap-3 mt-4 flex-wrap">
          <Button onClick={save} disabled={ss.state === "saving"}>
            {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t("common.save")}
          </Button>
          <SaveFeedback state={ss.state} />
          {isVllm && (
            <>
              <Button
                variant="outline"
                onClick={restartDocker}
                disabled={dockerRestarting}
                className="ml-2"
              >
                {dockerRestarting
                  ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />{t("common.restarting")}</>
                  : <><RotateCcw className="mr-2 h-4 w-4" />{t("common.restart_docker")}</>
                }
              </Button>
              {dockerStatus === "ok" && <span className="flex items-center gap-1 text-sm text-green-500"><CheckCircle2 className="h-4 w-4" /> {t("common.container_restarted")}</span>}
              {dockerStatus === "error" && <span className="flex items-center gap-1 text-sm text-destructive"><XCircle className="h-4 w-4" /> {t("common.restart_failed")}</span>}
            </>
          )}
        </div>
      </CardContent>}
    </Card>
  );
}

function ImageRoleCard({ role, config, onSaved }: { role: string; config: LlmRoleConfig; onSaved: () => void }) {
  const { t } = useTranslation();
  const [local, setLocal] = useState<LlmRoleConfig>({ ...config });
  const [modelName, setModelName] = useState(config.model ?? "");
  const [modelEditName, setModelEditName] = useState(config.model_edit ?? "");
  const [agentEnabled, setAgentEnabled] = useState(config.enabled ?? true);
  const [collapsed, setCollapsed] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const ss = useSaveState();
  const info = LLM_ROLE_LABELS[role] ?? {};
  const roleLabel = t(`settings.llm.role_labels.${role}`, role);

  const toggleEnabled = async (v: boolean) => {
    setAgentEnabled(v);
    try {
      await apiPutLlmRole(role, { enabled: v } as Parameters<typeof apiPutLlmRole>[1]);
    } catch {
      setAgentEnabled(!v);
    }
  };

  const save = async () => {
    ss.saving();
    try {
      const payload: Record<string, unknown> = {
        provider:             local.provider,
        base_url:             local.base_url,
        model:                modelName,
        model_edit:           modelEditName,
        timeout:              local.timeout ?? null,
        num_inference_steps:  local.num_inference_steps,
        guidance_scale:       local.guidance_scale,
        true_cfg_scale:       local.true_cfg_scale,
        response_format:      local.response_format,
        enabled:              agentEnabled,
      };
      if (apiKey) payload.api_key = apiKey;
      await apiPutLlmRole(role, payload as Parameters<typeof apiPutLlmRole>[1]);
      setApiKey("");
      ss.saved();
      onSaved();
    } catch { ss.error(); }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">{roleLabel}</CardTitle>
            {info.port && <CardDescription className="text-xs">Port {info.port}</CardDescription>}
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">{agentEnabled ? t("common.enabled") : t("common.disabled")}</span>
              <Switch checked={agentEnabled} onCheckedChange={toggleEnabled} className="data-checked:bg-green-600 data-unchecked:bg-red-600" />
            </div>
            <Badge variant="outline" className="font-mono text-xs">{role}</Badge>
          </div>
        </div>
        <div className="flex items-center justify-between pt-1">
          <span className="text-xs text-muted-foreground font-mono truncate max-w-[60%]">
            {modelName ? modelName : <span className="italic opacity-50">{t("common.no_model_set")}</span>}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setCollapsed(c => !c)}
          >
            {collapsed ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
            <span className="ml-1">{collapsed ? t("common.show") : t("common.hide")}</span>
          </Button>
        </div>
      </CardHeader>
      {!collapsed && <CardContent>
        <div className="divide-y divide-border">
          <FieldRow label={t("settings.llm.gen_model_label")} hint={t("settings.llm.gen_model_hint")}>
            <Input
              value={modelName}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelName(e.target.value)}
              className="font-mono text-sm"
              placeholder="f.eks. black-forest-labs/FLUX.1-schnell"
            />
          </FieldRow>

          <FieldRow label={t("settings.llm.edit_model_label")} hint={t("settings.llm.edit_model_hint")}>
            <Input
              value={modelEditName}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelEditName(e.target.value)}
              className="font-mono text-sm"
              placeholder="f.eks. black-forest-labs/FLUX.1-schnell"
            />
          </FieldRow>

          <FieldRow label={t("settings.llm.provider_label")} hint={t("settings.llm.image_provider_hint")}>
            <Select value={local.provider} onValueChange={(v: string | null) => { if (v) setLocal(p => ({ ...p, provider: v as LlmRoleConfig["provider"] })); }}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                {PROVIDER_OPTIONS.map(o => (
                  <SelectItem key={o.value} value={o.value}>{t(`settings.llm.providers.${o.value}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FieldRow>

          <FieldRow label="Base URL" hint={t("settings.llm.base_url_hint_api")}>
            <div className="flex gap-2">
              <Input
                value={local.base_url}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLocal(p => ({ ...p, base_url: e.target.value }))}
                className="font-mono text-sm"
              />
              <TestButton url={local.base_url} />
            </div>
          </FieldRow>

          <FieldRow
            label={t("settings.llm.api_key_label")}
            hint={config.api_key_set ? t("settings.llm.api_key_set_hint", { masked: config.api_key_masked }) : t("settings.llm.api_key_unset_hint")}
          >
            <MaskedInput value={apiKey} onChange={setApiKey} placeholder={config.api_key_set ? "••• (oppdater)" : "Lim inn nøkkel"} />
          </FieldRow>

          <FieldRow label={t("settings.llm.timeout_label")} hint={t("settings.llm.image_timeout_hint")}>
            <Input
              type="number"
              value={local.timeout ?? ""}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLocal(p => ({ ...p, timeout: e.target.value ? Number(e.target.value) : null }))}
              className="w-24"
              placeholder="120"
            />
          </FieldRow>


          <Separator className="my-1" />
          <p className="text-xs text-muted-foreground py-2">{t("settings.llm.gen_params")}</p>

          {([
            ["num_inference_steps", "Diffusion steps",   "Antall genereringssteg (f.eks. 28). Høyere = bedre kvalitet, men tregere."],
            ["guidance_scale",      "Guidance scale",    "Tekstfesting (sett til 1.0 for Qwen-Image-2512, ellers 7.5)."],
            ["true_cfg_scale",      "True CFG scale",    "Klassifierfri veiledning for Qwen-Image-2512 (f.eks. 5.0)."],
          ] as [keyof LlmRoleConfig, string, string][]).map(([k, label, hint]) => (
            <FieldRow key={k} label={label} hint={hint}>
              <Input
                type="number"
                value={(local[k] as number | undefined) ?? ""}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLocal(p => ({ ...p, [k]: Number(e.target.value) }))}
                className="w-32 font-mono text-sm"
                step="0.1"
              />
            </FieldRow>
          ))}

          <FieldRow label="Response format" hint="Format bildet returneres i. b64_json = base64 (anbefalt).">
            <Select
              value={local.response_format ?? "b64_json"}
              onValueChange={(v: string | null) => { if (v) setLocal(p => ({ ...p, response_format: v })); }}
            >
              <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="b64_json">b64_json</SelectItem>
                <SelectItem value="url">url</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>
        </div>

        <div className="flex items-center gap-3 mt-4">
          <Button onClick={save} disabled={ss.state === "saving"}>
            {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t("common.save")}
          </Button>
          <SaveFeedback state={ss.state} />
        </div>
      </CardContent>}
    </Card>
  );
}


type VoiceServicesData = {
  stt_backend: string;
  faster_whisper_model: string;
  compute_type: string;
  language: string;
  stt_enabled: boolean;
  model_dir: string;
};

const WHISPER_FW_PRESETS: { model: string; size: string; note: string }[] = [
  { model: "large-v3",       size: "~3 GB",    note: "Beste kvalitet (OpenAI)" },
  { model: "large-v3-turbo", size: "~1.6 GB",  note: "Raskere, nesten like god" },
  { model: "medium",         size: "~1.5 GB",  note: "God balanse" },
  { model: "small",          size: "~0.5 GB",  note: "Rask, lavere nøyaktighet" },
];

const WHISPER_OV_PRESETS: { model_dir: string; label: string; size: string; note: string }[] = [
  {
    model_dir: "/mnt/ai_disk/models/voice/nb-whisper-large-ov",
    label: "NbAiLab/nb-whisper-large (OpenVINO IR, konvertert)",
    size: "~1.5 GB",
    note: "Norsk (Nasjonalbiblioteket) — brukes nå",
  },
];

const WHISPER_COMPUTE_OPTIONS = [
  { value: "int8",          label: "int8 – standard (anbefalt)" },
  { value: "float16",       label: "float16 – høy presisjon (GPU)" },
  { value: "int8_float16",  label: "int8_float16 – rask på GPU" },
  { value: "float32",       label: "float32 – CPU-vennlig" },
];

// nb-whisper-large (OV) exposes <|no|> and <|nn|> — NOT <|nb|>
const WHISPER_LANGUAGE_OPTIONS = [
  { value: "no",   label: "Norsk (no) — anbefalt for NbAiLab-modellen" },
  { value: "nn",   label: "Nynorsk (nn)" },
  { value: "auto", label: "Auto-detect" },
  { value: "en",   label: "English (en)" },
];

function WhisperCard({ data, onSaved }: { data: VoiceServicesData; onSaved: () => void }) {
  const [collapsed, setCollapsed]     = useState(true);
  const [sttEnabled, setSttEnabled]   = useState(data.stt_enabled);
  const [backend, setBackend]         = useState(data.stt_backend);
  const [model, setModel]             = useState(data.faster_whisper_model);
  const [modelDir, setModelDir]       = useState(data.model_dir);
  const [computeType, setComputeType] = useState(data.compute_type);
  const [language, setLanguage]       = useState(data.language);
  const [restarting, setRestarting]   = useState(false);
  const [restartOk, setRestartOk]     = useState<boolean | null>(null);
  const ss = useSaveState();

  const isOpenvino = backend === "openvino";

  const save = async () => {
    ss.saving();
    try {
      await apiPutVoiceBackend({
        stt_backend:          backend,
        faster_whisper_model: model,
        compute_type:         computeType,
        language,
        stt_enabled:          sttEnabled,
        model_dir:            modelDir,
      });
      ss.saved();
      onSaved();
    } catch { ss.error(); }
  };

  const restart = async () => {
    setRestarting(true);
    setRestartOk(null);
    try {
      const r = await apiRestartService("voice");
      setRestartOk(r.ok);
    } catch { setRestartOk(false); }
    finally { setRestarting(false); }
  };

  return (
    <Card className={sttEnabled ? "" : "opacity-60"}>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div
          className="flex items-center gap-3 cursor-pointer select-none flex-1"
          onClick={() => setCollapsed(v => !v)}
        >
          <CardTitle className="text-base">Whisper (Tale-til-tekst)</CardTitle>
          <Badge variant="outline" className="text-xs font-mono">port 8011</Badge>
          <span className="text-xs text-muted-foreground">{sttEnabled ? "aktiv" : "deaktivert"}</span>
        </div>
        <div className="flex items-center gap-3">
          <Switch
            checked={sttEnabled}
            onCheckedChange={v => setSttEnabled(v)}
            className="data-checked:bg-green-600 data-unchecked:bg-red-600"
            onClick={e => e.stopPropagation()}
          />
          {collapsed
            ? <ChevronDown className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(false)} />
            : <ChevronUp   className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(true)} />
          }
        </div>
      </CardHeader>
      {!collapsed && (
        <CardContent className="space-y-5 pt-0">
          <FieldRow label="Backend" hint="faster_whisper fungerer på alle plattformer (CPU/GPU). openvino krever Intel Arc GPU og OpenVINO GenAI — brukes med NbAiLab/nb-whisper-large.">
            <Select value={backend} onValueChange={v => { if (v) setBackend(v); }}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="openvino">OpenVINO GPU (Intel Arc) — nåværende</SelectItem>
                <SelectItem value="faster_whisper">CPU universell (faster-whisper)</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>

          {isOpenvino ? (
            <>
              <FieldRow label="Modellmappe (OpenVINO IR)" hint="Sti til ferdig konvertert OpenVINO IR-modell. Inneholder openvino_encoder_model.xml, openvino_decoder_model.xml osv.">
                <Input
                  value={modelDir}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelDir(e.target.value)}
                  placeholder="/mnt/ai_disk/models/voice/nb-whisper-large-ov"
                  className="w-80"
                />
              </FieldRow>
              <div className="rounded-md border border-white/10 divide-y divide-white/5 text-sm">
                {WHISPER_OV_PRESETS.map(p => (
                  <button
                    key={p.model_dir}
                    type="button"
                    onClick={() => setModelDir(p.model_dir)}
                    className={`w-full flex items-start justify-between px-3 py-2 text-left hover:bg-white/5 transition-colors ${modelDir === p.model_dir ? "bg-white/10" : ""}`}
                  >
                    <div>
                      <span className="font-mono text-xs text-foreground block">{p.label}</span>
                      <span className="text-xs text-muted-foreground font-mono">{p.model_dir}</span>
                    </div>
                    <span className="flex gap-4 text-xs text-muted-foreground shrink-0 ml-3 mt-0.5">
                      <span>{p.size}</span>
                      <span className="text-green-400">{p.note}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-amber-300/80">
                ℹ️ <strong>NbAiLab/nb-whisper-large</strong> er OpenVINO IR-konvertert og lever lokalt på disk. Modellen støtter <code>&lt;|no|&gt;</code> og <code>&lt;|nn|&gt;</code> — <strong>ikke</strong> <code>&lt;|nb|&gt;</code>. Sett språk til <em>Norsk (no)</em>.
              </p>
            </>
          ) : (
            <>
              <FieldRow label="Modell" hint="HuggingFace-modellnavn eller sti til CTranslate2-konvertert modell. Lastes ned automatisk ved første oppstart.">
                <Input
                  value={model}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModel(e.target.value)}
                  placeholder="large-v3"
                  className="w-64"
                />
              </FieldRow>
              <div className="rounded-md border border-white/10 divide-y divide-white/5 text-sm">
                {WHISPER_FW_PRESETS.map(p => (
                  <button
                    key={p.model}
                    type="button"
                    onClick={() => setModel(p.model)}
                    className={`w-full flex items-center justify-between px-3 py-2 text-left hover:bg-white/5 transition-colors ${model === p.model ? "bg-white/10" : ""}`}
                  >
                    <span className="font-mono text-xs text-foreground">{p.model}</span>
                    <span className="flex gap-4 text-xs text-muted-foreground">
                      <span>{p.size}</span>
                      <span>{p.note}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                <strong>Norsk-optimalisert (NbAiLab):</strong> Bruk <code>NbAiLab/nb-whisper-large</code> med OpenVINO-backend (konvertert modell finnes allerede lokalt). For faster-whisper må den konverteres: <code className="bg-white/5 px-1 rounded">ct2-transformers-converter --model NbAiLab/nb-whisper-large --output_dir /sti/til/modell</code>
              </p>
              <FieldRow label="Beregningstype" hint="int8 anbefales for de fleste. float16/int8_float16 krever GPU. float32 fungerer på CPU uten spesial-maskinvare.">
                <Select value={computeType} onValueChange={v => { if (v) setComputeType(v); }}>
                  <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {WHISPER_COMPUTE_OPTIONS.map(o => (
                      <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FieldRow>
            </>
          )}

          <FieldRow label="Språk" hint="Norsk (no) er standard og anbefalt. NbAiLab-modellen støtter <|no|> og <|nn|> — ikke <|nb|>.">
            <Select value={language} onValueChange={v => { if (v) setLanguage(v); }}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                {WHISPER_LANGUAGE_OPTIONS.map(o => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FieldRow>

          <div className="flex items-center gap-3 flex-wrap">
            <Button onClick={save} disabled={ss.state === "saving"} size="sm">
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Lagre
            </Button>
            <Button onClick={restart} disabled={restarting} size="sm" variant="outline">
              {restarting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
              Restart tjeneste
            </Button>
            <SaveFeedback state={ss.state} />
            {restartOk === true  && <span className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> Restartet</span>}
            {restartOk === false && <span className="text-xs text-red-400 flex items-center gap-1"><XCircle className="h-3 w-3" /> Feil ved restart</span>}
          </div>
          <p className="text-xs text-muted-foreground">
            Restart er nødvendig for at endringer skal tre i kraft.
          </p>
        </CardContent>
      )}
    </Card>
  );
}



type EmbServicesData = {
  device: string;
  hf_model: string;
  model_path: string;
  emb_enabled: boolean;
};

const EMBEDDING_PRESETS: { model: string; dim: number; size: string; note: string }[] = [
  { model: "BAAI/bge-m3",                                                 dim: 1024, size: "~570 MB", note: "Standard (tett + spare) — brukes nå" },
  { model: "intfloat/multilingual-e5-large",                              dim: 1024, size: "~560 MB", note: "Alternativ multilingual" },
  { model: "sentence-transformers/paraphrase-multilingual-mpnet-base-v2", dim: 768,  size: "~280 MB", note: "Lettvekt alternativ" },
];

const KNOWN_DIMS: Record<string, number> = {
  "BAAI/bge-m3": 1024,
  "intfloat/multilingual-e5-large": 1024,
  "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": 768,
};

function EmbeddingCard({ data, onSaved }: { data: EmbServicesData; onSaved: () => void }) {
  const [collapsed, setCollapsed]     = useState(true);
  const [embEnabled, setEmbEnabled]   = useState(data.emb_enabled);
  const [device, setDevice]           = useState(data.device);
  const [hfModel, setHfModel]         = useState(data.hf_model);
  const [modelPath, setModelPath]     = useState(data.model_path);
  const [restarting, setRestarting]   = useState(false);
  const [restartOk, setRestartOk]     = useState<boolean | null>(null);
  const ss = useSaveState();

  const isOpenvino = device === "NPU" || device === "CPU";
  const currentDim = KNOWN_DIMS[hfModel] ?? null;
  const defaultDim = KNOWN_DIMS[data.hf_model] ?? 1024;
  const dimChanged = currentDim !== null && currentDim !== defaultDim;

  const save = async () => {
    ss.saving();
    try {
      await apiPutEmbeddingBackend({
        device,
        hf_model:    hfModel,
        model_path:  isOpenvino ? modelPath : undefined,
        emb_enabled: embEnabled,
      });
      ss.saved();
      onSaved();
    } catch { ss.error(); }
  };

  const restart = async () => {
    setRestarting(true);
    setRestartOk(null);
    try {
      const r = await apiRestartService("embedding");
      setRestartOk(r.ok);
    } catch { setRestartOk(false); }
    finally { setRestarting(false); }
  };

  return (
    <Card className={embEnabled ? "" : "opacity-60"}>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div
          className="flex items-center gap-3 cursor-pointer select-none flex-1"
          onClick={() => setCollapsed(v => !v)}
        >
          <CardTitle className="text-base">BGE-M3 (Innvektinger)</CardTitle>
          <Badge variant="outline" className="text-xs font-mono">port 11446</Badge>
          <span className="text-xs text-muted-foreground">{embEnabled ? "aktiv" : "deaktivert"}</span>
        </div>
        <div className="flex items-center gap-3">
          <Switch
            checked={embEnabled}
            onCheckedChange={v => setEmbEnabled(v)}
            className="data-checked:bg-green-600 data-unchecked:bg-red-600"
            onClick={e => e.stopPropagation()}
          />
          {collapsed
            ? <ChevronDown className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(false)} />
            : <ChevronUp   className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(true)} />
          }
        </div>
      </CardHeader>
      {!collapsed && (
        <CardContent className="space-y-5 pt-0">
          <div className="rounded-md border border-blue-500/20 bg-blue-500/5 px-3 py-2 text-xs text-blue-300/90">
            ℹ️ <strong>BAAI/bge-m3</strong> er konvertert til OpenVINO IR-format og lever lokalt på <code>/mnt/wiki/bge-m3-ov/</code>. Modellen brukes av NPU- og CPU-backend uten nettilgang.
          </div>

          <FieldRow label="Backend" hint="torch laster modellen automatisk fra HuggingFace og fungerer på AMD/Intel/Apple Silicon. OpenVINO krever ferdig konvertert IR-modell og Intel NPU/iGPU.">
            <Select value={device} onValueChange={v => { if (v) setDevice(v); }}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="NPU">OpenVINO NPU (Intel) — nåværende</SelectItem>
                <SelectItem value="CPU">OpenVINO CPU (Intel)</SelectItem>
                <SelectItem value="torch">CPU universell (torch, auto-nedlasting)</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>

          <FieldRow
            label="HuggingFace-modell"
            hint={isOpenvino ? "Modellnavn (brukes som referanse for tokenizer-lastning). For OpenVINO brukes IR-modellen i stien nedenfor." : "Modellnavn på HuggingFace. Lastes automatisk ned ved første oppstart."}
          >
            <Input
              value={hfModel}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHfModel(e.target.value)}
              placeholder="BAAI/bge-m3"
              className="w-64"
            />
          </FieldRow>
          <div className="rounded-md border border-white/10 divide-y divide-white/5 text-sm">
            {EMBEDDING_PRESETS.map(p => (
              <button
                key={p.model}
                type="button"
                onClick={() => setHfModel(p.model)}
                className={`w-full flex items-center justify-between px-3 py-2 text-left hover:bg-white/5 transition-colors ${hfModel === p.model ? "bg-white/10" : ""}`}
              >
                <span className="font-mono text-xs text-foreground truncate max-w-[18rem]">{p.model}</span>
                <span className="flex gap-3 text-xs text-muted-foreground shrink-0 ml-2">
                  <span className="font-mono">{p.dim}-dim</span>
                  <span>{p.size}</span>
                  <span className={p.note.includes("nå") ? "text-green-400" : ""}>{p.note}</span>
                </span>
              </button>
            ))}
          </div>

          {currentDim && (
            <p className="text-xs text-muted-foreground">
              Vektordimensjon for valgt modell: <strong>{currentDim}</strong>
            </p>
          )}

          {dimChanged && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              ⚠️ Du bytter fra <strong>{defaultDim}-dim</strong> til <strong>{currentDim}-dim</strong>. Qdrant-kolleksjonene <code>kaare_memory</code> og <code>wiki_no</code> må slettes og bygges om — dette sletter all lagret hukommelse.
            </div>
          )}

          {isOpenvino && (
            <FieldRow label="Lokal modellsti (OpenVINO IR)" hint="Sti til ferdig konvertert OpenVINO IR-modell. Inneholder openvino_model.xml og openvino_model.bin.">
              <Input
                value={modelPath}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelPath(e.target.value)}
                placeholder="/mnt/wiki/bge-m3-ov"
                className="w-64"
              />
            </FieldRow>
          )}

          <div className="flex items-center gap-3 flex-wrap">
            <Button onClick={save} disabled={ss.state === "saving"} size="sm">
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Lagre
            </Button>
            <Button onClick={restart} disabled={restarting} size="sm" variant="outline">
              {restarting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
              Restart tjeneste
            </Button>
            <SaveFeedback state={ss.state} />
            {restartOk === true  && <span className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> Restartet</span>}
            {restartOk === false && <span className="text-xs text-red-400 flex items-center gap-1"><XCircle className="h-3 w-3" /> Feil ved restart</span>}
          </div>
          <p className="text-xs text-muted-foreground">
            Restart er nødvendig for at endringer skal tre i kraft.
          </p>
        </CardContent>
      )}
    </Card>
  );
}



type MemoryEmbedData = { enabled: boolean; model_dir: string };

function MemoryEmbedCard({ data, onSaved }: { data: MemoryEmbedData; onSaved: () => void }) {
  const [collapsed, setCollapsed] = useState(true);
  const [enabled, setEnabled]     = useState(data.enabled);
  const [modelDir, setModelDir]   = useState(data.model_dir);
  const ss = useSaveState();

  const save = async () => {
    ss.saving();
    try {
      await apiPutMemoryEmbedBackend({ enabled, model_dir: modelDir });
      ss.saved();
      onSaved();
    } catch { ss.error(); }
  };

  return (
    <Card className={enabled ? "" : "opacity-60"}>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div
          className="flex items-center gap-3 cursor-pointer select-none flex-1"
          onClick={() => setCollapsed(v => !v)}
        >
          <CardTitle className="text-base">Semantisk minne (MiniLM)</CardTitle>
          <Badge variant="outline" className="text-xs font-mono">port 11500</Badge>
          <span className="text-xs text-muted-foreground">{enabled ? "aktiv" : "deaktivert"}</span>
        </div>
        <div className="flex items-center gap-3">
          <Switch
            checked={enabled}
            onCheckedChange={v => setEnabled(v)}
            className="data-checked:bg-green-600 data-unchecked:bg-red-600"
            onClick={e => e.stopPropagation()}
          />
          {collapsed
            ? <ChevronDown className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(false)} />
            : <ChevronUp   className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(true)} />
          }
        </div>
      </CardHeader>
      {!collapsed && (
        <CardContent className="space-y-5 pt-0">
          <div className="rounded-md border border-blue-500/20 bg-blue-500/5 px-3 py-2 text-xs text-blue-300/90">
            384-dim embedding for semantisk minnesøk. Bruker <strong>paraphrase-multilingual-MiniLM-L12-v2</strong> (ONNX).
            Modellen må ligge lokalt — plasser <code>model.onnx</code> og <code>tokenizer.json</code> i mappen under.
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Modellmappe</Label>
            <Input
              value={modelDir}
              onChange={e => setModelDir(e.target.value)}
              placeholder="/kaare/state/models/semantic-embed"
              className="font-mono text-xs"
            />
          </div>
          <div className="flex items-center gap-3">
            <Button size="sm" onClick={save} disabled={ss.state === "saving"}>
              {ss.state === "saving" ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
              Lagre
            </Button>
            {ss.state === "saved"  && <span className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> Lagret</span>}
            {ss.state === "error"  && <span className="text-xs text-red-400 flex items-center gap-1"><XCircle className="h-3 w-3" /> Feil</span>}
          </div>
          <p className="text-xs text-muted-foreground">
            Tjenesten starter automatisk når den aktiveres og modellfilene finnes i mappen.
          </p>
        </CardContent>
      )}
    </Card>
  );
}



function TabLlm() {
  const { t } = useTranslation();
  const [configs, setConfigs] = useState<Record<string, LlmRoleConfig>>({});
  const [voiceData, setVoiceData] = useState<VoiceServicesData>({
    stt_backend: "openvino", faster_whisper_model: "large-v3", compute_type: "int8",
    language: "no", stt_enabled: true, model_dir: "",
  });
  const [embData, setEmbData] = useState<EmbServicesData>({
    device: "NPU", hf_model: "BAAI/bge-m3", model_path: "", emb_enabled: true,
  });
  const [memEmbData, setMemEmbData] = useState<MemoryEmbedData>({ enabled: false, model_dir: "" });
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    Promise.all([
      apiGetLlmSettings(),
      apiGetServices(),
    ]).then(([llm, svc]) => {
      setConfigs(llm);
      const s = svc as any;
      setVoiceData({
        stt_backend:          s.voice?.stt_backend          ?? "openvino",
        faster_whisper_model: s.voice?.faster_whisper_model ?? "large-v3",
        compute_type:         s.voice?.compute_type         ?? "int8",
        language:             s.voice?.language             ?? "no",
        stt_enabled:          s.voice?.stt_enabled          ?? true,
        model_dir:            s.voice?.model_dir            ?? "",
      });
      setEmbData({
        device:      s.embedding?.device      ?? "NPU",
        hf_model:    s.embedding?.hf_model    ?? "BAAI/bge-m3",
        model_path:  s.embedding?.model_path  ?? "",
        emb_enabled: s.embedding?.emb_enabled ?? true,
      });
      setMemEmbData({
        enabled:   s.memory_embed?.enabled   ?? false,
        model_dir: s.memory_embed?.model_dir ?? "",
      });
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div className="text-muted-foreground text-sm">{t("common.loading")}</div>;

  const roleOrder = ["default", "miss_kare", "pettersmart", "library", "fallback", "cloud", "image_edit"];

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        {t("settings.llm.description")}
        <br/>
        <strong className="text-amber-500">!</strong> {t("settings.llm.ctx_warning")}
      </p>
      {roleOrder.filter(r => r in configs).map(role => (
        role === "image_edit"
          ? <ImageRoleCard key={role} role={role} config={configs[role]} onSaved={load} />
          : <LlmRoleCard   key={role} role={role} config={configs[role]} onSaved={load} allConfigs={configs} />
      ))}
      <WhisperCard      data={voiceData}   onSaved={load} />
      <EmbeddingCard    data={embData}     onSaved={load} />
      <MemoryEmbedCard  data={memEmbData}  onSaved={load} />
    </div>
  );
}



const WEATHER_PROVIDERS: { value: WeatherProvider; label: string; needsKey: boolean; keyField?: "owm" | "wapi" }[] = [
  { value: "met.no",        label: "met.no (gratis, norsk, ingen nøkkel)",         needsKey: false },
  { value: "open-meteo",    label: "Open-Meteo (gratis, global, ingen nøkkel)",    needsKey: false },
  { value: "openweathermap",label: "OpenWeatherMap (global, API-nøkkel påkrevd)",  needsKey: true,  keyField: "owm" },
  { value: "weatherapi",    label: "WeatherAPI.com (global, API-nøkkel påkrevd)",  needsKey: true,  keyField: "wapi" },
];

function TabNettsokOgVaer() {
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



function TabRefleksjon() {
  const { t } = useTranslation();

  const [cfg, setCfg] = useState<ReflectionConfig>({ enabled: false, interval_seconds: 600 });
  const [loadingCfg, setLoadingCfg] = useState(true);
  const ss = useSaveState();

  const [refMtg, setRefMtg] = useState<ReflectionMeetingSettings | null>(null);
  const [loadingRef, setLoadingRef] = useState(true);
  const ssRef = useSaveState();

  const [devMtg, setDevMtg] = useState<DevMeetingSettings | null>(null);
  const [loadingDev, setLoadingDev] = useState(true);
  const ssDev = useSaveState();

  useEffect(() => {
    apiGetReflectionSettings()
      .then(d => { setCfg(d); setLoadingCfg(false); })
      .catch(() => setLoadingCfg(false));
    apiGetReflectionMeetingSettings()
      .then(d => { setRefMtg(d); setLoadingRef(false); })
      .catch(() => setLoadingRef(false));
    apiGetDevMeetingSettings()
      .then(d => { setDevMtg(d); setLoadingDev(false); })
      .catch(() => setLoadingDev(false));
  }, []);

  const saveSchedule = async () => {
    ss.saving();
    try { await apiPutReflectionSettings(cfg); ss.saved(); }
    catch { ss.error(); }
  };

  const saveRefMtg = async () => {
    if (!refMtg) return;
    ssRef.saving();
    try { await apiPutReflectionMeetingSettings(refMtg); ssRef.saved(); }
    catch { ssRef.error(); }
  };

  const saveDevMtg = async () => {
    if (!devMtg) return;
    ssDev.saving();
    try { await apiPutDevMeetingSettings(devMtg); ssDev.saved(); }
    catch { ssDev.error(); }
  };

  const intervalMinutes = Math.round(cfg.interval_seconds / 60);

  const REF_PRESETS = ["standard", "analytisk", "utfordrende", "egendefinert"];
  const DEV_PRESETS = ["standard", "streng", "utforskende", "egendefinert"];

  if (loadingCfg) return <div className="text-muted-foreground text-sm">{t("common.loading")}</div>;

  return (
    <div className="space-y-6">

      {/* ── Schedule ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.refleksjon.title")}</CardTitle>
          <CardDescription>{t("settings.refleksjon.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            <FieldRow label={t("settings.refleksjon.enabled_label")} hint={t("settings.refleksjon.enabled_hint")}>
              <div className="flex items-center gap-3 h-9">
                <button
                  type="button"
                  role="switch"
                  aria-checked={cfg.enabled}
                  onClick={() => setCfg(p => ({ ...p, enabled: !p.enabled }))}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${cfg.enabled ? "bg-green-600" : "bg-red-600"}`}
                >
                  <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${cfg.enabled ? "translate-x-4" : "translate-x-0.5"}`} />
                </button>
                <span className="text-sm text-muted-foreground">{cfg.enabled ? t("common.on") : t("common.off")}</span>
              </div>
            </FieldRow>

            <FieldRow label={t("settings.refleksjon.interval_label")} hint={t("settings.refleksjon.interval_hint")}>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  min={1}
                  value={intervalMinutes}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                    const mins = Math.max(1, Number(e.target.value));
                    setCfg(p => ({ ...p, interval_seconds: mins * 60 }));
                  }}
                  className="w-24"
                />
                <span className="text-xs text-muted-foreground">{t("settings.refleksjon.seconds_suffix", { seconds: cfg.interval_seconds })}</span>
              </div>
            </FieldRow>
          </div>

          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveSchedule} disabled={ss.state === "saving"}>
              {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ss.state} />
          </div>
        </CardContent>
      </Card>

      {/* ── Reflection meeting ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("reflections.tab_reflection")}</CardTitle>
          <CardDescription>{t("reflections.settings_title")}</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingRef || !refMtg
            ? <div className="text-muted-foreground text-sm">{t("common.loading")}</div>
            : (
            <div className="divide-y divide-border">
              <FieldRow label={t("reflections.settings_max_rounds")}>
                <Input type="number" min={2} max={12} className="w-24"
                  value={refMtg.max_rounds}
                  onChange={e => setRefMtg({ ...refMtg, max_rounds: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_kare_tokens")}>
                <Input type="number" min={200} max={8000} step={100} className="w-28"
                  value={refMtg.kare_max_tokens}
                  onChange={e => setRefMtg({ ...refMtg, kare_max_tokens: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_miss_kare_tokens")}>
                <Input type="number" min={100} max={4000} step={100} className="w-28"
                  value={refMtg.miss_kare_max_tokens}
                  onChange={e => setRefMtg({ ...refMtg, miss_kare_max_tokens: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_leder_preset")}>
                <div className="flex flex-wrap gap-2">
                  {REF_PRESETS.map(p => (
                    <button key={p} type="button"
                      onClick={() => setRefMtg(prev => prev ? { ...prev, leder_preset: p } : prev)}
                      className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                        refMtg.leder_preset === p
                          ? "bg-pink-500/20 text-pink-400 border-pink-500/40"
                          : "bg-muted text-muted-foreground border-border hover:border-pink-500/30"
                      }`}>
                      {t(`reflections.leder_preset_${p}`)}
                    </button>
                  ))}
                </div>
              </FieldRow>
              {refMtg.leder_preset !== "egendefinert" ? (
                <FieldRow label={t("reflections.settings_leder_default_label")}>
                  <pre className="bg-muted/30 border border-border rounded-md p-3 text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                    {refMtg.leder_preset_default || "—"}
                  </pre>
                </FieldRow>
              ) : (
                <FieldRow label={t("reflections.settings_leder_custom")}>
                  <textarea rows={8}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-mono leading-relaxed resize-y focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    placeholder={t("reflections.settings_leder_custom_ph")}
                    value={refMtg.leder_preset_custom}
                    onChange={e => setRefMtg({ ...refMtg, leder_preset_custom: e.target.value })} />
                </FieldRow>
              )}
            </div>
          )}
          {refMtg && (
            <div className="flex items-center gap-3 mt-4">
              <Button onClick={saveRefMtg} disabled={ssRef.state === "saving"}>
                {ssRef.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {t("common.save")}
              </Button>
              <SaveFeedback state={ssRef.state} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Dev meeting ── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("reflections.tab_dev")}</CardTitle>
          <CardDescription>{t("reflections.settings_title")}</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingDev || !devMtg
            ? <div className="text-muted-foreground text-sm">{t("common.loading")}</div>
            : (
            <div className="divide-y divide-border">
              <FieldRow label={t("reflections.settings_max_rounds")}>
                <Input type="number" min={2} max={12} className="w-24"
                  value={devMtg.max_rounds}
                  onChange={e => setDevMtg({ ...devMtg, max_rounds: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_max_invest_rounds")}>
                <Input type="number" min={1} max={10} className="w-24"
                  value={devMtg.max_invest_rounds}
                  onChange={e => setDevMtg({ ...devMtg, max_invest_rounds: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_kare_tokens")}>
                <Input type="number" min={500} max={8000} step={100} className="w-28"
                  value={devMtg.kare_max_tokens}
                  onChange={e => setDevMtg({ ...devMtg, kare_max_tokens: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_kare_invest_tokens")}>
                <Input type="number" min={200} max={4000} step={100} className="w-28"
                  value={devMtg.kare_invest_tokens}
                  onChange={e => setDevMtg({ ...devMtg, kare_invest_tokens: Number(e.target.value) })} />
              </FieldRow>
              <FieldRow label={t("reflections.settings_leder_preset")}>
                <div className="flex flex-wrap gap-2">
                  {DEV_PRESETS.map(p => (
                    <button key={p} type="button"
                      onClick={() => setDevMtg(prev => prev ? { ...prev, leder_preset: p } : prev)}
                      className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                        devMtg.leder_preset === p
                          ? "bg-blue-500/20 text-blue-400 border-blue-500/40"
                          : "bg-muted text-muted-foreground border-border hover:border-blue-500/30"
                      }`}>
                      {t(`reflections.leder_preset_${p}`)}
                    </button>
                  ))}
                </div>
              </FieldRow>
              {devMtg.leder_preset !== "egendefinert" ? (
                <FieldRow label={t("reflections.settings_leder_default_label")}>
                  <pre className="bg-muted/30 border border-border rounded-md p-3 text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                    {devMtg.leder_preset_default || "—"}
                  </pre>
                </FieldRow>
              ) : (
                <FieldRow label={t("reflections.settings_leder_custom")}>
                  <textarea rows={8}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-mono leading-relaxed resize-y focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    placeholder={t("reflections.settings_leder_custom_ph")}
                    value={devMtg.leder_preset_custom}
                    onChange={e => setDevMtg({ ...devMtg, leder_preset_custom: e.target.value })} />
                </FieldRow>
              )}
            </div>
          )}
          {devMtg && (
            <div className="flex items-center gap-3 mt-4">
              <Button onClick={saveDevMtg} disabled={ssDev.state === "saving"}>
                {ssDev.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {t("common.save")}
              </Button>
              <SaveFeedback state={ssDev.state} />
            </div>
          )}
        </CardContent>
      </Card>

    </div>
  );
}


function TabBilder() {
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



type UserEntry = { username: string; display_name: string; role: string };

function RadioOption({ value, current, onChange, label, description }: {
  value: string;
  current: string;
  onChange: (v: string) => void;
  label: string;
  description: string;
}) {
  const active = value === current;
  return (
    <button
      type="button"
      onClick={() => onChange(value)}
      className="flex items-start gap-3 w-full text-left py-2 group"
    >
      <span className={`mt-0.5 flex-shrink-0 w-4 h-4 rounded-full border-2 flex items-center justify-center transition-colors ${active ? "border-primary bg-primary" : "border-muted-foreground bg-transparent"}`}>
        {active && <span className="w-2 h-2 rounded-full bg-white" />}
      </span>
      <span>
        <span className="text-sm font-medium leading-tight block">{label}</span>
        <span className="text-xs text-muted-foreground">{description}</span>
      </span>
    </button>
  );
}

function TabKareInnstillinger() {
  const { t } = useTranslation();
  const [assistantName, setAssistantName] = useState("Kåre");
  const [hotword, setHotword] = useState("Kåre");
  const [personalityMode, setPersonalityMode] = useState<PersonalityMode>("standard");
  const [customText, setCustomText] = useState("");
  const [defaultPersonality, setDefaultPersonality] = useState("");
  const [mode, setMode] = useState<ContributorMode>("selected");
  const [allowedUsers, setAllowedUsers] = useState<string[]>([]);
  const [allUsers, setAllUsers] = useState<UserEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const ssPersonality = useSaveState();
  const ssSelfimage   = useSaveState();

  useEffect(() => {
    Promise.all([
      apiGetKareSettings(),
      axios.get(`${BASE}/api/users`, { headers: { Authorization: `Bearer ${token()}` } }),
    ]).then(([kare, usersRes]) => {
      setAssistantName(kare.assistant_name ?? "Kåre");
      setHotword(kare.hotword ?? "Kåre");
      setPersonalityMode(kare.personality_mode ?? "standard");
      setCustomText(kare.personality_core_custom ?? "");
      setDefaultPersonality(kare.personality_core_default ?? "");
      setMode(kare.personality_self.contributors);
      setAllowedUsers(kare.personality_self.allowed_users);
      setAllUsers((usersRes.data as UserEntry[]).filter(u => u.username !== "admin"));
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const toggleUser = (username: string) => {
    setAllowedUsers(prev =>
      prev.includes(username) ? prev.filter(u => u !== username) : [...prev, username]
    );
  };

  const savePersonality = async () => {
    ssPersonality.saving();
    try {
      await apiPutKareSettings({
        personality_mode: personalityMode,
        personality_core_custom: personalityMode === "egendefinert" ? customText : undefined,
      });
      ssPersonality.saved();
    } catch { ssPersonality.error(); }
  };

  const saveSelfimage = async () => {
    ssSelfimage.saving();
    try {
      await apiPutKareSettings({
        assistant_name: assistantName,
        hotword,
        personality_self: { contributors: mode, allowed_users: allowedUsers },
      });
      ssSelfimage.saved();
    } catch { ssSelfimage.error(); }
  };

  if (loading) return <div className="text-muted-foreground text-sm">{t("common.loading")}</div>;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.kare.personality_mode.title")}</CardTitle>
          <CardDescription>{t("settings.kare.personality_mode.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border mb-4">
            <RadioOption
              value="minimal"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.minimal_label")}
              description={t("settings.kare.personality_mode.minimal_desc")}
            />
            <RadioOption
              value="letvekt"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.letvekt_label")}
              description={t("settings.kare.personality_mode.letvekt_desc")}
            />
            <RadioOption
              value="standard"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.standard_label")}
              description={t("settings.kare.personality_mode.standard_desc")}
            />
            <RadioOption
              value="full"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.full_label")}
              description={t("settings.kare.personality_mode.full_desc")}
            />
            <RadioOption
              value="komplett"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.komplett_label")}
              description={t("settings.kare.personality_mode.komplett_desc")}
            />
            <RadioOption
              value="egendefinert"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.egendefinert_label")}
              description={t("settings.kare.personality_mode.egendefinert_desc")}
            />
          </div>
          {personalityMode === "egendefinert" && (
            <div className="mt-2 space-y-2">
              {customText === "" && defaultPersonality && (
                <div className="flex justify-end">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setCustomText(defaultPersonality)}
                  >
                    {t("settings.kare.personality_mode.load_default_btn")}
                  </Button>
                </div>
              )}
              <textarea
                value={customText}
                onChange={(e) => setCustomText(e.target.value)}
                placeholder={t("settings.kare.personality_mode.custom_placeholder")}
                rows={16}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <p className="text-xs text-muted-foreground">{t("settings.kare.personality_mode.custom_hint")}</p>
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-3">{t("settings.kare.personality_mode.hot_reload_hint")}</p>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={savePersonality} disabled={ssPersonality.state === "saving"}>
              {ssPersonality.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssPersonality.state} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("settings.kare.selfimage.title")}</CardTitle>
          <CardDescription>{t("settings.kare.selfimage.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border mb-6">
            <FieldRow label={t("settings.kare.selfimage.name_label")} hint={t("settings.kare.selfimage.name_hint")}>
              <Input value={assistantName} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAssistantName(e.target.value)} placeholder="Kåre" className="w-48" />
            </FieldRow>
            <FieldRow label={t("settings.kare.selfimage.hotword_label")} hint={t("settings.kare.selfimage.hotword_hint")}>
              <Input value={hotword} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHotword(e.target.value)} placeholder="Kåre" className="w-48" />
            </FieldRow>
          </div>
          <div className="mb-2">
            <Label className="text-sm font-medium">{t("settings.kare.selfimage.who_shapes")}</Label>
          </div>
          <div className="divide-y divide-border mb-5">
            <RadioOption
              value="all"
              current={mode}
              onChange={(v) => setMode(v as ContributorMode)}
              label={t("settings.kare.selfimage.modes.all_label")}
              description={t("settings.kare.selfimage.modes.all_desc")}
            />
            <RadioOption
              value="selected"
              current={mode}
              onChange={(v) => setMode(v as ContributorMode)}
              label={t("settings.kare.selfimage.modes.selected_label")}
              description={t("settings.kare.selfimage.modes.selected_desc")}
            />
            <RadioOption
              value="admin_only"
              current={mode}
              onChange={(v) => setMode(v as ContributorMode)}
              label={t("settings.kare.selfimage.modes.admin_only_label")}
              description={t("settings.kare.selfimage.modes.admin_only_desc")}
            />
          </div>

          {mode === "selected" && (
            <div className="mb-5">
              <Label className="text-sm font-medium mb-2 block">{t("settings.kare.selfimage.allowed_users_label")}</Label>
              {allUsers.length === 0 && (
                <p className="text-xs text-muted-foreground">{t("settings.kare.selfimage.no_users")}</p>
              )}
              <div className="flex flex-wrap gap-2 mt-1">
                {allUsers.map(u => {
                  const checked = allowedUsers.includes(u.username);
                  return (
                    <button
                      key={u.username}
                      type="button"
                      onClick={() => toggleUser(u.username)}
                      className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-sm transition-colors ${
                        checked
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:border-muted-foreground"
                      }`}
                    >
                      <span className={`w-3.5 h-3.5 rounded flex-shrink-0 border flex items-center justify-center transition-colors ${checked ? "bg-primary border-primary" : "border-muted-foreground"}`}>
                        {checked && (
                          <svg viewBox="0 0 12 12" className="w-2.5 h-2.5 fill-white">
                            <path d="M2 6l3 3 5-5" stroke="white" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        )}
                      </span>
                      {u.display_name || u.username}
                    </button>
                  );
                })}
              </div>
              <p className="text-xs text-muted-foreground mt-2">{t("settings.kare.selfimage.admin_note")}</p>
            </div>
          )}

          <div className="flex items-center gap-3">
            <Button onClick={saveSelfimage} disabled={ssSelfimage.state === "saving"}>
              {ssSelfimage.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssSelfimage.state} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}



function TabIntegrasjoner() {
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

function TabDistribusjon() {
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



const DEFAULT_AGENT_TOOLS: AgentToolsConfig = {
  pettersmart: { utforsk: true, inspiser: true, "nettsøk": true, "søk_vaktmester": true, shell: false, hukommelse: true },
  miss_kare:   { "spør_frøken_library": true },
  miss_library: { wiki: false },
};

const DEFAULT_MEETING_ROLES: MeetingRolesConfig = {
  pettersmart: "undersøker", pettersmart_custom: "", pettersmart_default: "",
  miss_kare: "empatisk", miss_kare_custom: "", miss_kare_default: "",
};

function TabAgenter() {
  const { t } = useTranslation();
  const ss         = useSaveState();
  const ssPsRole   = useSaveState();
  const ssMkRole   = useSaveState();
  const [cfg, setCfg]       = useState<AgentToolsConfig>(DEFAULT_AGENT_TOOLS);
  const [roles, setRoles]   = useState<MeetingRolesConfig>(DEFAULT_MEETING_ROLES);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    pettersmart: true, miss_kare: true, miss_library: true,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([apiGetAgentTools(), apiGetMeetingRoles()])
      .then(([tools, r]) => { setCfg({ ...DEFAULT_AGENT_TOOLS, ...tools }); setRoles(r); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const toggle = (agent: keyof AgentToolsConfig, tool: string, val: boolean) =>
    setCfg(prev => ({ ...prev, [agent]: { ...prev[agent], [tool]: val } }));
  const toggleExpand = (key: string) =>
    setExpanded(prev => ({ ...prev, [key]: !prev[key] }));

  const saveTools = async () => {
    ss.saving();
    try { await apiPutAgentTools(cfg); ss.saved(); }
    catch { ss.error(); }
  };
  const savePsRole = async () => {
    ssPsRole.saving();
    try {
      await apiPutMeetingRoles({
        pettersmart: roles.pettersmart,
        ...(roles.pettersmart === "egendefinert" ? { pettersmart_custom: roles.pettersmart_custom } : {}),
      });
      ssPsRole.saved();
    } catch { ssPsRole.error(); }
  };
  const saveMkRole = async () => {
    ssMkRole.saving();
    try {
      await apiPutMeetingRoles({
        miss_kare: roles.miss_kare,
        ...(roles.miss_kare === "egendefinert" ? { miss_kare_custom: roles.miss_kare_custom } : {}),
      });
      ssMkRole.saved();
    } catch { ssMkRole.error(); }
  };

  if (loading) return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground mt-6" />;

  const ColorSwitch = ({ agent, tool, defaultOff }: { agent: keyof AgentToolsConfig; tool: string; defaultOff?: boolean }) => (
    <Switch
      checked={cfg[agent]?.[tool] ?? !defaultOff}
      onCheckedChange={v => toggle(agent, tool, v)}
      className="data-checked:bg-green-600 data-unchecked:bg-red-600"
    />
  );

  type ToolDef = { key: string; label: string; description: string; defaultOff?: boolean };

  const psTools: ToolDef[] = [
    { key: "utforsk",        label: t("settings.agenter.tools.utforsk"),        description: t("settings.agenter.tools.utforsk_desc") },
    { key: "inspiser",       label: t("settings.agenter.tools.inspiser"),       description: t("settings.agenter.tools.inspiser_desc") },
    { key: "nettsøk",        label: t("settings.agenter.tools.nettsøk"),        description: t("settings.agenter.tools.nettsøk_desc") },
    { key: "søk_vaktmester", label: t("settings.agenter.tools.søk_vaktmester"), description: t("settings.agenter.tools.søk_vaktmester_desc") },
    { key: "shell",          label: t("settings.agenter.tools.shell"),          description: t("settings.agenter.tools.shell_desc"), defaultOff: true },
    { key: "hukommelse",     label: t("settings.agenter.tools.hukommelse"),     description: t("settings.agenter.tools.hukommelse_desc") },
  ];
  const mkTools: ToolDef[] = [
    { key: "spør_frøken_library", label: t("settings.agenter.tools.spør_frøken_library"), description: t("settings.agenter.tools.spør_frøken_library_desc") },
  ];
  const mlTools: ToolDef[] = [
    { key: "wiki", label: t("settings.agenter.tools.wiki"), description: t("settings.agenter.tools.wiki_desc"), defaultOff: true },
  ];

  const SectionLabel = ({ text }: { text: string }) => (
    <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground mb-3">{text}</p>
  );

  const ToolList = ({ agent, tools }: { agent: keyof AgentToolsConfig; tools: ToolDef[] }) => (
    <div className="space-y-3">
      {tools.map(tool => (
        <div key={tool.key} className="flex items-center justify-between py-1">
          <div>
            <p className="text-sm font-mono text-white/90">{tool.label}</p>
            <p className="text-xs text-muted-foreground">{tool.description}</p>
          </div>
          <ColorSwitch agent={agent} tool={tool.key} defaultOff={tool.defaultOff} />
        </div>
      ))}
    </div>
  );

  return (
    <div className="space-y-4">

      {/* ── Pettersmart ── */}
      <Card className="bg-[#1a1a1a] border-[#333]">
        <CardHeader className="cursor-pointer select-none pb-3" onClick={() => toggleExpand("pettersmart")}>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-sm font-semibold text-white">{t("settings.agenter.pettersmart.label")}</CardTitle>
              <CardDescription className="text-xs text-muted-foreground">{t("settings.agenter.pettersmart.description")}</CardDescription>
            </div>
            {expanded.pettersmart ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
          </div>
        </CardHeader>
        {expanded.pettersmart && (
          <CardContent className="space-y-6 pt-0">
            <div>
              <SectionLabel text={t("settings.agenter.section_tools")} />
              <ToolList agent="pettersmart" tools={psTools} />
            </div>
            <Separator className="bg-[#333]" />
            <div>
              <SectionLabel text={t("settings.agenter.section_role_dev")} />
              <div className="divide-y divide-[#2a2a2a] mb-4">
                {([ ["undersøker", "undersøker"], ["kritiker", "kritiker"], ["analytiker", "analytiker"], ["egendefinert", "egendefinert"] ] as [string, string][]).map(([value, key]) => (
                  <RadioOption
                    key={value}
                    value={value}
                    current={roles.pettersmart}
                    onChange={v => setRoles(r => ({ ...r, pettersmart: v }))}
                    label={t(`settings.agenter.pettersmart.roles.${key}_label`)}
                    description={t(`settings.agenter.pettersmart.roles.${key}_desc`)}
                  />
                ))}
              </div>
              {roles.pettersmart === "egendefinert" && (
                <div className="space-y-2 mt-2">
                  {roles.pettersmart_custom === "" && roles.pettersmart_default && (
                    <div className="flex justify-end">
                      <Button variant="outline" size="sm"
                        onClick={() => setRoles(r => ({ ...r, pettersmart_custom: r.pettersmart_default }))}>
                        {t("settings.agenter.role_load_default")}
                      </Button>
                    </div>
                  )}
                  <textarea
                    value={roles.pettersmart_custom}
                    onChange={e => setRoles(r => ({ ...r, pettersmart_custom: e.target.value }))}
                    placeholder={t("settings.agenter.role_custom_placeholder")}
                    rows={14}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <p className="text-xs text-muted-foreground">{t("settings.agenter.role_custom_hint")}</p>
                </div>
              )}
              <p className="text-xs text-muted-foreground mt-3">{t("settings.agenter.role_hot_reload_hint")}</p>
              <div className="flex items-center gap-3 mt-4">
                <Button size="sm" onClick={savePsRole} disabled={ssPsRole.state === "saving"}>
                  {ssPsRole.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {t("settings.agenter.role_save")}
                </Button>
                <SaveFeedback state={ssPsRole.state} />
              </div>
            </div>
          </CardContent>
        )}
      </Card>

      {/* ── Miss Kåre ── */}
      <Card className="bg-[#1a1a1a] border-[#333]">
        <CardHeader className="cursor-pointer select-none pb-3" onClick={() => toggleExpand("miss_kare")}>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-sm font-semibold text-white">{t("settings.agenter.miss_kare.label")}</CardTitle>
              <CardDescription className="text-xs text-muted-foreground">{t("settings.agenter.miss_kare.description")}</CardDescription>
            </div>
            {expanded.miss_kare ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
          </div>
        </CardHeader>
        {expanded.miss_kare && (
          <CardContent className="space-y-6 pt-0">
            <div>
              <SectionLabel text={t("settings.agenter.section_tools")} />
              <ToolList agent="miss_kare" tools={mkTools} />
            </div>
            <Separator className="bg-[#333]" />
            <div>
              <SectionLabel text={t("settings.agenter.section_role_reflection")} />
              <div className="divide-y divide-[#2a2a2a] mb-4">
                {([ ["empatisk", "empatisk"], ["analytiker", "analytiker"], ["utfordrende", "utfordrende"], ["egendefinert", "egendefinert"] ] as [string, string][]).map(([value, key]) => (
                  <RadioOption
                    key={value}
                    value={value}
                    current={roles.miss_kare}
                    onChange={v => setRoles(r => ({ ...r, miss_kare: v }))}
                    label={t(`settings.agenter.miss_kare.roles.${key}_label`)}
                    description={t(`settings.agenter.miss_kare.roles.${key}_desc`)}
                  />
                ))}
              </div>
              {roles.miss_kare === "egendefinert" && (
                <div className="space-y-2 mt-2">
                  {roles.miss_kare_custom === "" && roles.miss_kare_default && (
                    <div className="flex justify-end">
                      <Button variant="outline" size="sm"
                        onClick={() => setRoles(r => ({ ...r, miss_kare_custom: r.miss_kare_default }))}>
                        {t("settings.agenter.role_load_default")}
                      </Button>
                    </div>
                  )}
                  <textarea
                    value={roles.miss_kare_custom}
                    onChange={e => setRoles(r => ({ ...r, miss_kare_custom: e.target.value }))}
                    placeholder={t("settings.agenter.role_custom_placeholder")}
                    rows={14}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <p className="text-xs text-muted-foreground">{t("settings.agenter.role_custom_hint")}</p>
                </div>
              )}
              <p className="text-xs text-muted-foreground mt-3">{t("settings.agenter.role_hot_reload_hint")}</p>
              <div className="flex items-center gap-3 mt-4">
                <Button size="sm" onClick={saveMkRole} disabled={ssMkRole.state === "saving"}>
                  {ssMkRole.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {t("settings.agenter.role_save")}
                </Button>
                <SaveFeedback state={ssMkRole.state} />
              </div>
            </div>
          </CardContent>
        )}
      </Card>

      {/* ── Frøken Library ── */}
      <Card className="bg-[#1a1a1a] border-[#333]">
        <CardHeader className="cursor-pointer select-none pb-3" onClick={() => toggleExpand("miss_library")}>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-sm font-semibold text-white">{t("settings.agenter.miss_library.label")}</CardTitle>
              <CardDescription className="text-xs text-muted-foreground">{t("settings.agenter.miss_library.description")}</CardDescription>
            </div>
            {expanded.miss_library ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
          </div>
        </CardHeader>
        {expanded.miss_library && (
          <CardContent className="pt-0">
            <ToolList agent="miss_library" tools={mlTools} />
          </CardContent>
        )}
      </Card>

      <div className="flex items-center gap-3">
        <Button size="sm" onClick={saveTools} disabled={ss.state === "saving"}>
          {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {t("settings.agenter.save_tools")}
        </Button>
        <SaveFeedback state={ss.state} />
      </div>
    </div>
  );
}



export default function Settings() {
  const { t } = useTranslation();
  const dot = (color: string) => (
    <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, display: "inline-block", marginRight: 5, flexShrink: 0 }} />
  );

  return (
    <div>
      <h1 style={{ color: "#fff", fontSize: 22, fontWeight: 700, margin: "0 0 28px" }}>{t("settings.title")}</h1>
      <Tabs defaultValue="generelt" className="w-full">
        {/* Scrollable on mobile so tabs stay on one row instead of wrapping over content */}
        <div className="w-full overflow-x-auto mb-6 pb-1">
          <TabsList className="flex-nowrap h-auto gap-1">
            <TabsTrigger value="generelt"    className="gap-1 whitespace-nowrap">{dot("#9c8d5e")}{t("settings.tabs.generelt")}</TabsTrigger>
            <TabsTrigger value="ha"          className="gap-1 whitespace-nowrap">{dot("#4db67a")}{t("settings.tabs.ha")}</TabsTrigger>
            <TabsTrigger value="mqtt"        className="gap-1 whitespace-nowrap">{dot("#ff9f43")}{t("settings.tabs.mqtt")}</TabsTrigger>
            <TabsTrigger value="llm"         className="gap-1 whitespace-nowrap">{dot("#a29bfe")}{t("settings.tabs.llm")}</TabsTrigger>
            <TabsTrigger value="refleksjon"  className="gap-1 whitespace-nowrap">{dot("#fd79a8")}{t("settings.tabs.refleksjon")}</TabsTrigger>
            <TabsTrigger value="nettsok"     className="gap-1 whitespace-nowrap">{dot("#00cec9")}{t("settings.tabs.nettsok")}</TabsTrigger>
            <TabsTrigger value="bilder"      className="gap-1 whitespace-nowrap">{dot("#e17055")}{t("settings.tabs.bilder")}</TabsTrigger>
            <TabsTrigger value="kare"           className="gap-1 whitespace-nowrap">{dot("#b8c6db")}{t("settings.tabs.kare")}</TabsTrigger>
            <TabsTrigger value="integrasjoner" className="gap-1 whitespace-nowrap">{dot("#55efc4")}{t("settings.tabs.integrasjoner")}</TabsTrigger>
            <TabsTrigger value="distribusjon"  className="gap-1 whitespace-nowrap">{dot("#fdcb6e")}{t("settings.tabs.distribusjon")}</TabsTrigger>
            <TabsTrigger value="agenter"       className="gap-1 whitespace-nowrap">{dot("#74b9ff")}{t("settings.tabs.agenter")}</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="generelt">      <div data-tab="generelt"><TabGenerelt /></div></TabsContent>
        <TabsContent value="ha">            <div data-tab="ha"><TabHomeAssistant /></div></TabsContent>
        <TabsContent value="mqtt">          <div data-tab="mqtt"><TabMqtt /></div></TabsContent>
        <TabsContent value="llm">           <div data-tab="llm"><TabLlm /></div></TabsContent>
        <TabsContent value="refleksjon">    <div data-tab="refleksjon"><TabRefleksjon /></div></TabsContent>
        <TabsContent value="nettsok">       <div data-tab="nettsok"><TabNettsokOgVaer /></div></TabsContent>
        <TabsContent value="bilder">        <div data-tab="bilder"><TabBilder /></div></TabsContent>
        <TabsContent value="kare">          <div data-tab="kare"><TabKareInnstillinger /></div></TabsContent>
        <TabsContent value="integrasjoner"> <div data-tab="integrasjoner"><TabIntegrasjoner /></div></TabsContent>
        <TabsContent value="distribusjon">  <div data-tab="distribusjon"><TabDistribusjon /></div></TabsContent>
        <TabsContent value="agenter">       <div data-tab="agenter"><TabAgenter /></div></TabsContent>
      </Tabs>
    </div>
  );
}
