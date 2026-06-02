import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ChevronDown, ChevronUp, Copy, RefreshCw } from "lucide-react";
import { apiPutInnerVoices, type InnerVoicesData } from "@/services/api";
import { SaveFeedback, useSaveState } from "../shared";

const PROVIDERS = ["openvino", "mlx", "cpu", "remote"] as const;

function VoiceRow({
  name,
  provider,
  modelPath,
  intervalSeconds,
  onProviderChange,
  onModelPathChange,
  t,
}: {
  name: string;
  provider: string;
  modelPath: string;
  intervalSeconds: number;
  onProviderChange: (v: string) => void;
  onModelPathChange: (v: string) => void;
  t: (k: string) => string;
}) {
  return (
    <div className="space-y-2 rounded-md border border-border/50 p-3">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-sm font-semibold w-20">{name}</span>
        <div className="flex items-center gap-2">
          <Label className="text-xs text-muted-foreground shrink-0">{t("settings.llm.inner_voices.provider")}</Label>
          <Select value={provider} onValueChange={v => { if (v) onProviderChange(v); }}>
            <SelectTrigger className="w-36 h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PROVIDERS.map(p => (
                <SelectItem key={p} value={p} className="text-xs">
                  {t(`settings.llm.inner_voices.providers.${p}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="text-xs text-muted-foreground ml-auto">
          {t("settings.llm.inner_voices.interval")}: {intervalSeconds}s
        </span>
      </div>
      {provider !== "remote" && (
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">{t("settings.llm.inner_voices.model_path")}</Label>
          <Input
            value={modelPath}
            onChange={e => onModelPathChange(e.target.value)}
            placeholder={t("settings.llm.inner_voices.model_path_placeholder")}
            className="h-8 text-xs font-mono"
          />
          <p className="text-xs text-muted-foreground">
            {t("settings.llm.inner_voices.model_path_hint")}
          </p>
        </div>
      )}
    </div>
  );
}

export function InnerVoicesCard({ data, onSaved }: { data: InnerVoicesData; onSaved: () => void }) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(true);

  const [jingProvider,   setJingProvider]   = useState(data.jing.provider);
  const [jingModelPath,  setJingModelPath]  = useState(data.jing.model_path);
  const [jangProvider,   setJangProvider]   = useState(data.jang.provider);
  const [jangModelPath,  setJangModelPath]  = useState(data.jang.model_path);
  const [nodeLabel,      setNodeLabel]      = useState(data.node_label);
  const [pushToken,      setPushToken]      = useState(data.push_token);
  const [copied,         setCopied]         = useState(false);

  const ss = useSaveState();

  const hasRemote = jingProvider === "remote" || jangProvider === "remote";

  const save = async () => {
    ss.saving();
    try {
      await apiPutInnerVoices({
        jing_provider:   jingProvider,
        jing_model_path: jingModelPath,
        jang_provider:   jangProvider,
        jang_model_path: jangModelPath,
        node_label:      nodeLabel,
        push_token:      pushToken,
      });
      ss.saved();
      onSaved();
    } catch { ss.error(); }
  };

  const generateToken = async () => {
    ss.saving();
    try {
      await apiPutInnerVoices({ generate_token: true });
      const res = await fetch("/api/settings/inner-voices");
      const fresh = await res.json();
      setPushToken(fresh.push_token ?? "");
      ss.saved();
      onSaved();
    } catch { ss.error(); }
  };

  const copyToken = () => {
    navigator.clipboard.writeText(pushToken).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div
          className="flex items-center gap-3 cursor-pointer select-none flex-1"
          onClick={() => setCollapsed(v => !v)}
        >
          <CardTitle className="text-base">{t("settings.llm.inner_voices.title")}</CardTitle>
          <span className="text-xs text-muted-foreground">
            {jingProvider} / {jangProvider}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {collapsed
            ? <ChevronDown className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(false)} />
            : <ChevronUp   className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(true)}  />
          }
        </div>
      </CardHeader>

      {!collapsed && (
        <CardContent className="space-y-4 pt-0">
          <p className="text-xs text-muted-foreground">
            {t("settings.llm.inner_voices.description")}
          </p>

          <VoiceRow
            name="Jing (0.6B)"
            provider={jingProvider}
            modelPath={jingModelPath}
            intervalSeconds={data.jing.interval_seconds}
            onProviderChange={setJingProvider}
            onModelPathChange={setJingModelPath}
            t={t}
          />

          <VoiceRow
            name="Jang (4B)"
            provider={jangProvider}
            modelPath={jangModelPath}
            intervalSeconds={data.jang.interval_seconds}
            onProviderChange={setJangProvider}
            onModelPathChange={setJangModelPath}
            t={t}
          />

          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">{t("settings.llm.inner_voices.node_label")}</Label>
            <Input
              value={nodeLabel}
              onChange={e => setNodeLabel(e.target.value)}
              placeholder="Local"
              className="h-8 text-sm w-48"
            />
            <p className="text-xs text-muted-foreground">
              {t("settings.llm.inner_voices.node_label_hint")}
            </p>
          </div>

          {hasRemote && (
            <div className="space-y-2 rounded-md border border-violet-500/30 bg-violet-500/5 p-3">
              <p className="text-xs font-medium text-violet-300">
                {t("settings.llm.inner_voices.remote_section_title")}
              </p>
              <p className="text-xs text-muted-foreground">
                {t("settings.llm.inner_voices.remote_section_hint")}
              </p>
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">{t("settings.llm.inner_voices.push_token")}</Label>
                <div className="flex items-center gap-2">
                  <Input
                    type="password"
                    value={pushToken}
                    onChange={e => setPushToken(e.target.value)}
                    placeholder="••••••••"
                    className="h-8 text-xs font-mono flex-1"
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 px-2 text-xs gap-1"
                    onClick={copyToken}
                    disabled={!pushToken}
                  >
                    <Copy className="h-3 w-3" />
                    {copied ? t("common.copied") : t("common.copy")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 px-2 text-xs gap-1"
                    onClick={generateToken}
                  >
                    <RefreshCw className="h-3 w-3" />
                    {t("settings.llm.inner_voices.generate_token")}
                  </Button>
                </div>
              </div>
            </div>
          )}

          <div className="flex items-center gap-3 pt-1">
            <Button size="sm" onClick={save} disabled={ss.state === "saving"}>
              {t("common.save")}
            </Button>
            <SaveFeedback state={ss.state} />
          </div>
        </CardContent>
      )}
    </Card>
  );
}
