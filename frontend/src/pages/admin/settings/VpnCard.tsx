// VPN address card (DuckDNS host + WireGuard port). Moved out of the MQTT tab when MQTT went to the Integrations
// page (P144-s01) — self-contained so it can move again as one block (I21).
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import { apiGetVpnSettings, apiPutVpnSettings } from "@/services/api";
import { FieldRow, SaveFeedback } from "./shared";
import { useSaveState } from "./useSaveState";

export default function VpnCard() {
  const { t } = useTranslation();
  const [vpnHost, setVpnHost] = useState("");
  const [vpnPort, setVpnPort] = useState("51820");
  const ssVpn = useSaveState();

  useEffect(() => {
    apiGetVpnSettings().then(d => {
      setVpnHost(d.duckdns_host);
      setVpnPort(String(d.wg_port));
    }).catch(() => {});
  }, []);

  const saveVpn = async () => {
    ssVpn.saving();
    try {
      await apiPutVpnSettings({ duckdns_host: vpnHost, wg_port: Number(vpnPort) });
      ssVpn.saved();
    } catch { ssVpn.error(); }
  };

  return (
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
  );
}
