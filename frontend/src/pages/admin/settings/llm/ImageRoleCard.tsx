import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Loader2, ChevronDown, ChevronUp } from "lucide-react";
import {
  apiPutLlmRole,
  type LlmRoleConfig,
} from "@/services/api";
import { PROVIDER_OPTIONS, LLM_ROLE_LABELS } from "./llm-constants";
import { useSaveState, FieldRow, SaveFeedback, TestButton, MaskedInput } from "../shared";

export function ImageRoleCard({ role, config, onSaved }: { role: string; config: LlmRoleConfig; onSaved: () => void }) {
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
            <input
              value={modelName}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelName(e.target.value)}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
              placeholder="f.eks. black-forest-labs/FLUX.1-schnell"
            />
          </FieldRow>

          <FieldRow label={t("settings.llm.edit_model_label")} hint={t("settings.llm.edit_model_hint")}>
            <input
              value={modelEditName}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelEditName(e.target.value)}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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
              <input
                value={local.base_url}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLocal(p => ({ ...p, base_url: e.target.value }))}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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
            <input
              type="number"
              value={local.timeout ?? ""}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLocal(p => ({ ...p, timeout: e.target.value ? Number(e.target.value) : null }))}
              className="flex h-9 w-24 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
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
              <input
                type="number"
                value={(local[k] as number | undefined) ?? ""}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLocal(p => ({ ...p, [k]: Number(e.target.value) }))}
                className="flex h-9 w-32 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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
