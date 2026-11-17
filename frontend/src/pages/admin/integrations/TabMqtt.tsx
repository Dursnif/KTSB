import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { apiGetServices, apiPutMqtt } from "@/services/api";
import { FieldRow, SaveFeedback, TestButton, ExpertOnly } from "../settings/shared";
import { useSaveState } from "../settings/useSaveState";

export default function TabMqtt() {
  const { t } = useTranslation();
  const [host, setHost] = useState("");
  const [port, setPort] = useState("1883");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [tlsEnabled, setTlsEnabled] = useState(false);
  const [clientId, setClientId] = useState("");
  const [reconnectInterval, setReconnectInterval] = useState("30");
  const ssMqtt = useSaveState();

  useEffect(() => {
    apiGetServices().then(d => {
      setHost(d.mqtt.host);
      setPort(String(d.mqtt.port));
      setUsername(d.mqtt.username ?? "");
      setTlsEnabled(d.mqtt.tls_enabled ?? false);
      setClientId(d.mqtt.client_id ?? "");
      setReconnectInterval(String(d.mqtt.reconnect_interval ?? 30));
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
        client_id: clientId,
        reconnect_interval: Number(reconnectInterval),
      };
      if (password) payload.password = password;
      const saveRes = await apiPutMqtt(payload);
      setPassword("");
      ssMqtt.saved(saveRes?.applies);
    } catch { ssMqtt.error(); }
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
                <Input value={host} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHost(e.target.value)} placeholder="192.168.0.x" />
                <TestButton url={`http://${host}:${port}`} />
              </div>
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.port_label")} hint={t("settings.mqtt.card.port_hint")}>
              <Input type="number" value={port} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPort(e.target.value)} className="w-28" />
            </FieldRow>
            <ExpertOnly><>
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
                <Switch checked={tlsEnabled} onCheckedChange={setTlsEnabled} id="mqtt-tls" />
                <label htmlFor="mqtt-tls" className="text-sm cursor-pointer select-none">{t("settings.mqtt.card.tls_enable")}</label>
              </div>
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.client_label")} hint={t("settings.mqtt.card.client_hint")}>
              <Input value={clientId} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setClientId(e.target.value)} placeholder="(auto)" className="w-48" />
            </FieldRow>
            <FieldRow label={t("settings.mqtt.card.reconnect_label")} hint={t("settings.mqtt.card.reconnect_hint")}>
              <Input type="number" value={reconnectInterval} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setReconnectInterval(e.target.value)} className="w-28" />
            </FieldRow>
            </></ExpertOnly>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveMqtt} disabled={ssMqtt.state === "saving"}>
              {ssMqtt.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssMqtt.state} applies={ssMqtt.applies} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
