import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Loader2 } from "lucide-react";
import { apiGetServices, apiPutHa, apiGetHaToken, apiPutHaToken, apiGetHaBridge, apiPutHaBridge } from "@/services/api";
import { useSaveState, FieldRow, SaveFeedback, TestButton, MaskedInput } from "./shared";

export default function TabHomeAssistant() {
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
