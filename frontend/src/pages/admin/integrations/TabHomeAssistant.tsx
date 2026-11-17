import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Loader2 } from "lucide-react";
import { apiGetServices, apiPutHa, apiGetHaToken, apiPutHaToken, apiGetHaBridge, apiPutHaBridge } from "@/services/api";
import { FieldRow, SaveFeedback, TestButton, MaskedInput, ExpertOnly } from "../settings/shared";
import { useSaveState } from "../settings/useSaveState";

// Fallback when the API does not send its list (older backend); the API's `actions` is the source (P139).
const HA_ALLOWED_ACTIONS_FALLBACK = [
  "turn_on", "turn_off", "toggle", "set_level", "set_color_temp", "set_color",
  "open", "close", "set_temperature", "get_temperature", "get_state",
];

export default function TabHomeAssistant() {
  const { t } = useTranslation();
  const [haUrl, setHaUrl]       = useState("");
  const [haTimeout, setHaTimeout] = useState("5");
  const [haVerify, setHaVerify] = useState("2");
  const [haEnabled, setHaEnabled] = useState(false);
  const [haToken, setHaToken]   = useState("");
  const [tokenInfo, setTokenInfo] = useState<{ is_set: boolean; masked: string } | null>(null);
  const [bridgeLogUrl, setBridgeLogUrl] = useState("");
  const [bridgeTimeout, setBridgeTimeout] = useState("5");
  const [bridgeActions, setBridgeActions] = useState("");
  const [knownActions, setKnownActions] = useState<string[]>(HA_ALLOWED_ACTIONS_FALLBACK);

  const ssGateway = useSaveState();
  const ssToken   = useSaveState();
  const ssBridge  = useSaveState();

  useEffect(() => {
    apiGetServices().then(d => {
      setHaUrl(d.home_assistant.url);
      setHaTimeout(String(d.home_assistant.timeout));
      setHaVerify(String(d.home_assistant.verify_seconds ?? 2));
      setHaEnabled(d.home_assistant.enabled ?? false);
    }).catch(() => {});
    apiGetHaToken().then(setTokenInfo).catch(() => {});
    apiGetHaBridge().then(d => {
      setBridgeLogUrl(d.log_url);
      setBridgeTimeout(d.timeout);
      const known = d.actions?.length ? d.actions : HA_ALLOWED_ACTIONS_FALLBACK;
      setKnownActions(known);
      // An empty list means «everything allowed» in the gateway — show it as all ticked, not none.
      setBridgeActions(d.allowed_actions?.trim() ? d.allowed_actions : known.join(","));
    }).catch(() => {});
  }, []);

  const saveGateway = async () => {
    ssGateway.saving();
    try {
      const saveRes = await apiPutHa({ url: haUrl, timeout: Number(haTimeout), verify_seconds: Number(haVerify), enabled: haEnabled });
      ssGateway.saved(saveRes?.applies);
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
      const saveRes = await apiPutHaBridge({ log_url: bridgeLogUrl, timeout: bridgeTimeout, allowed_actions: bridgeActions });
      ssBridge.saved(saveRes?.applies);
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
            <FieldRow label={t("settings.ha.gateway.enabled_label")} hint={t("settings.ha.gateway.enabled_hint")}>
              <div className="flex items-center gap-2">
                <Switch checked={haEnabled} onCheckedChange={setHaEnabled} />
                <span className="text-sm text-muted-foreground">{haEnabled ? t("common.enabled") : t("common.disabled")}</span>
              </div>
            </FieldRow>
            <FieldRow label={t("settings.ha.gateway.url_label")} hint={t("settings.ha.gateway.url_hint")}>
              <div className="flex gap-2">
                <Input value={haUrl} onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setHaUrl(e.target.value)} placeholder="http://192.168.0.x:8123" />
                <TestButton url={haUrl} />
              </div>
            </FieldRow>
            <ExpertOnly>
            <FieldRow label={t("settings.ha.gateway.timeout_label")} hint={t("settings.ha.gateway.timeout_hint")}>
              <Input type="number" value={haTimeout} onChange={(e: React.ChangeEvent<HTMLInputElement>) =>setHaTimeout(e.target.value)} className="w-24" />
            </FieldRow>
            <FieldRow label={t("settings.ha.gateway.verify_label")} hint={t("settings.ha.gateway.verify_hint")}>
              <Input type="number" step="0.5" min={0} max={10} value={haVerify} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHaVerify(e.target.value)} className="w-24" />
            </FieldRow>
            </ExpertOnly>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveGateway} disabled={ssGateway.state === "saving"}>
              {ssGateway.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssGateway.state} applies={ssGateway.applies} />
          </div>
        </CardContent>
      </Card>

      {/* HA Gateway — avansert */}
      <ExpertOnly>
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
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-2">
                {knownActions.map(action => {
                  const selected = bridgeActions.split(",").map(a => a.trim()).filter(Boolean);
                  const checked = selected.includes(action);
                  const toggle = () => {
                    const next = checked ? selected.filter(a => a !== action) : [...selected, action];
                    setBridgeActions(next.join(","));
                  };
                  return (
                    <label key={action} className="flex items-center gap-2 text-sm cursor-pointer">
                      <input type="checkbox" checked={checked} onChange={toggle} className="h-4 w-4" />
                      <span>{t(`settings.ha.advanced.actions.${action}`)}</span>
                    </label>
                  );
                })}
              </div>
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveBridge} disabled={ssBridge.state === "saving"}>
              {ssBridge.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssBridge.state} applies={ssBridge.applies} />
          </div>
        </CardContent>
      </Card>
      </ExpertOnly>

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
