import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { apiGetServices, apiPutMqtt, apiGetVpnSettings, apiPutVpnSettings } from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback, TestButton } from "./shared";

export default function TabMqtt() {
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
