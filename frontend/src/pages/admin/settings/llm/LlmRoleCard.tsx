import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { CheckCircle2, XCircle, Loader2, RotateCcw, ChevronDown, ChevronUp, Search } from "lucide-react";
import {
  apiPutLlmRole, apiRestartVllmDocker, apiGetGpus, apiGetOllamaModels,
  apiDeleteOllamaModel, apiPullModel, apiGetPullStatus, apiDiscoverOllama,
  apiGetWarmupStatus, apiTriggerWarmup, apiDiscoverContainers, apiRestartOllama,
  type LlmRoleConfig, type VllmDockerConfig, type OllamaPullStatus, type OllamaEnvConfig,
  type WarmupStatus, type ContainerInfo, type GpuInfo,
} from "@/services/api";
import { PROVIDER_OPTIONS, LLM_ROLE_LABELS } from "./llm-constants";
import { useSaveState, FieldRow, SaveFeedback, TestButton, MaskedInput } from "../shared";

const THINK_OPTIONS = [
  { value: "__unset__", labelKey: "settings.llm.think_unset" },
  { value: "true",      labelKey: "settings.llm.think_enabled" },
  { value: "false",     labelKey: "settings.llm.think_disabled" },
];

const AGENT_TOGGLEABLE = ["miss_kare", "mechanic", "library", "fallback", "cloud", "image_edit"];

export function LlmRoleCard({ role, config, onSaved, allConfigs, allowDockerControl }: {
  role: string;
  config: LlmRoleConfig;
  onSaved: () => void;
  allConfigs: Record<string, LlmRoleConfig>;
  allowDockerControl?: boolean;
}) {
  const { t } = useTranslation();
  const [local, setLocal] = useState<LlmRoleConfig>({ ...config });
  const [modelName, setModelName] = useState(config.model ?? "");
  const [agentEnabled, setAgentEnabled] = useState(config.enabled ?? true);
  const [collapsed, setCollapsed] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [dockerRestarting, setDockerRestarting] = useState(false);
  const [dockerStatus, setDockerStatus] = useState<"idle" | "ok" | "error">("idle");
  const [containerName, setContainerName] = useState(config.container ?? "");
  const [containerDiscovering, setContainerDiscovering] = useState(false);
  const [containerOptions, setContainerOptions] = useState<ContainerInfo[]>([]);
  const [ollamaRestarting, setOllamaRestarting] = useState(false);
  const [ollamaRestartStatus, setOllamaRestartStatus] = useState<"idle" | "ok" | "error">("idle");
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
  const [keepWarm, setKeepWarm]                    = useState<boolean>(config.keep_warm ?? false);
  const isMechanic = role === "mechanic";
  const [shareWith, setShareWith]                  = useState<string | null>(config.share_with ?? null);
  const isShared = isMechanic && !!shareWith;
  const [warmup, setWarmup]                        = useState<WarmupStatus>({ status: "idle" });
  const warmupPollRef                              = useRef<ReturnType<typeof setInterval> | null>(null);

  const startWarmupPolling = () => {
    if (warmupPollRef.current) clearInterval(warmupPollRef.current);
    warmupPollRef.current = setInterval(async () => {
      try {
        const st = await apiGetWarmupStatus(role);
        setWarmup(st);
        if (st.status === "done" || st.status === "error" || st.status === "idle" || st.status === "warning_cpu") {
          clearInterval(warmupPollRef.current!);
          warmupPollRef.current = null;
        }
      } catch { /* ignore transient errors */ }
    }, 2000);
  };

  useEffect(() => {
    return () => {
      if (warmupPollRef.current) clearInterval(warmupPollRef.current);
    };
  }, []);

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
            const pulledModel = pullModelInput.trim();
            setPullModelInput("");
            apiTriggerWarmup(role, pulledModel).catch(() => {});
            setWarmup({ status: "waiting", model: pulledModel });
            startWarmupPolling();
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
        payload.keep_warm = keepWarm;
        payload.container = containerName || null;
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
      if (isMechanic) payload.share_with = shareWith ?? null;
      await apiPutLlmRole(role, payload as Parameters<typeof apiPutLlmRole>[1]);
      setApiKey("");
      ss.saved();
      onSaved();
      if (isOllama && modelName) {
        setWarmup({ status: "waiting", model: modelName });
        startWarmupPolling();
      }
    } catch { ss.error(); }
  };

  const discoverContainer = async () => {
    setContainerDiscovering(true);
    try {
      const res = await apiDiscoverContainers();
      const urlPort = parseInt(local.base_url?.match(/:(\d+)/)?.[1] ?? "0");
      const matches = (res.containers ?? []).filter(c => urlPort > 0 && c.ports.includes(urlPort));
      setContainerOptions(matches.length > 0 ? matches : (res.containers ?? []));
      if (matches.length === 1) setContainerName(matches[0].name);
    } catch { /* ignore */ } finally {
      setContainerDiscovering(false);
    }
  };

  const restartOllamaContainer = async () => {
    setOllamaRestarting(true);
    setOllamaRestartStatus("idle");
    try {
      const res = await apiRestartOllama(role);
      if (res.ok) {
        setOllamaRestartStatus("ok");
        if (res.warmup_started) {
          setWarmup({ status: "waiting", model: "" });
          startWarmupPolling();
        }
      } else {
        setOllamaRestartStatus("error");
      }
    } catch {
      setOllamaRestartStatus("error");
    } finally {
      setOllamaRestarting(false);
    }
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
            {isMechanic && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">{t("settings.llm.share_with_label")}</span>
                <Switch checked={!!shareWith} onCheckedChange={v => setShareWith(v ? "miss_kare" : null)} />
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
      {!collapsed && isShared && (
        <CardContent>
          <p className="text-sm text-muted-foreground italic py-2">{t("settings.llm.share_with_active_note")}</p>
          <Button onClick={save} disabled={ss.state === "saving"} size="sm" className="mt-2">
            {ss.state === "saving" ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
            {t("common.save")}
          </Button>
          <SaveFeedback state={ss.state} />
        </CardContent>
      )}
      {!collapsed && !isShared && <CardContent>
        <div className="divide-y divide-border">
          <FieldRow label={t("settings.llm.model_label")} hint={t("settings.llm.model_hint")}>
            <input
              value={modelName}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setModelName(e.target.value)}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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
              <input
                value={local.base_url}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLocal(p => ({ ...p, base_url: e.target.value }))}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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

          {isOllama && (
            <FieldRow label={t("settings.llm.keep_warm_label")} hint={t("settings.llm.keep_warm_hint")}>
              <Switch checked={keepWarm} onCheckedChange={setKeepWarm} className="data-checked:bg-green-500" />
            </FieldRow>
          )}

          <FieldRow label={t("settings.llm.timeout_label")} hint={t("settings.llm.timeout_hint")}>
            <input
              type="number"
              value={local.timeout ?? ""}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLocal(p => ({ ...p, timeout: e.target.value ? Number(e.target.value) : null }))}
              className="flex h-9 w-24 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
              placeholder="120"
            />
          </FieldRow>

          {isOllama && (
            <FieldRow label={t("settings.llm.container_name")} hint={t("settings.llm.container_name_hint")}>
              <div className="flex gap-2">
                <input
                  value={containerName}
                  onChange={e => setContainerName(e.target.value)}
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
                  placeholder="ollama-agents"
                  list="container-options"
                />
                <datalist id="container-options">
                  {containerOptions.map(c => <option key={c.name} value={c.name} />)}
                </datalist>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={discoverContainer}
                  disabled={containerDiscovering}
                  title={t("settings.llm.container_discover")}
                >
                  {containerDiscovering ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
                </Button>
              </div>
            </FieldRow>
          )}

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
                    <input
                      type="number"
                      value={local.options?.[k] ?? ""}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setOpt(k, Number(e.target.value))}
                      className="flex h-9 w-32 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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
                    <input
                      type="number"
                      value={local.options?.[k] ?? ""}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setOpt(k, Number(e.target.value))}
                      className="flex h-9 w-32 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
                      step={step}
                    />
                  </FieldRow>
                );
              })}

              <Separator className="my-1" />
              <p className="text-xs text-muted-foreground py-2">{t("settings.llm.vllm_docker_params")}</p>
              <FieldRow label={t("settings.llm.docker_fields.max_model_len.label")} hint={t("settings.llm.docker_fields.max_model_len.hint")}>
                <input
                  type="number"
                  value={local.vllm_docker?.max_model_len ?? ""}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDockerOpt("max_model_len", Number(e.target.value))}
                  className="flex h-9 w-32 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
                  step="1024"
                />
              </FieldRow>
              <FieldRow label={t("settings.llm.docker_fields.gpu_memory_utilization.label")} hint={t("settings.llm.docker_fields.gpu_memory_utilization.hint")}>
                <input
                  type="number"
                  value={local.vllm_docker?.gpu_memory_utilization ?? ""}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDockerOpt("gpu_memory_utilization", Number(e.target.value))}
                  className="flex h-9 w-32 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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
                <input
                  type="number"
                  value={local.vllm_docker?.max_num_seqs ?? ""}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDockerOpt("max_num_seqs", Number(e.target.value))}
                  className="flex h-9 w-24 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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
                  <input
                    type="number"
                    value={(local as Record<string, unknown>)[k] as number ?? ""}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLocal(p => ({ ...p, [k]: Number(e.target.value) }))}
                    className="flex h-9 w-32 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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
                      <input
                        type="number" min={1} step={1}
                        className="flex h-9 w-24 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
                        placeholder={t("settings.llm.manage_auto")}
                        value={ollamaEnv.num_threads ?? ""}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setEnv("num_threads", e.target.value ? Number(e.target.value) : null)
                        }
                      />
                    </FieldRow>

                    <FieldRow label={t("settings.llm.manage_num_parallel_label")} hint={t("settings.llm.manage_num_parallel_hint")}>
                      <input
                        type="number" min={1} step={1}
                        className="flex h-9 w-24 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
                        placeholder="1"
                        value={ollamaEnv.num_parallel ?? ""}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setEnv("num_parallel", e.target.value ? Number(e.target.value) : null)
                        }
                      />
                    </FieldRow>

                    <FieldRow label={t("settings.llm.manage_max_loaded_label")} hint={t("settings.llm.manage_max_loaded_hint")}>
                      <input
                        type="number" min={1} step={1}
                        className="flex h-9 w-24 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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
                        <input
                          className="flex h-9 w-48 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm font-mono"
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

        <div className="flex flex-col gap-1 mt-4">
          <div className="flex items-center gap-3 flex-wrap">
            <Button onClick={save} disabled={ss.state === "saving" || warmup.status === "waiting" || warmup.status === "loading"}>
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
          {warmup.status === "waiting" && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t("settings.llm.warmup_waiting")}
            </span>
          )}
          {warmup.status === "loading" && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t("settings.llm.warmup_loading")}
            </span>
          )}
          {warmup.status === "done" && (
            <span className="flex items-center gap-1 text-xs text-green-500">
              <CheckCircle2 className="h-3 w-3" />
              {t("settings.llm.warmup_done")}
            </span>
          )}
          {warmup.status === "warning_cpu" && (
            <span className="flex items-center gap-1 text-xs text-amber-500">
              <XCircle className="h-3 w-3" />
              {t("settings.llm.warmup_cpu_warning")}
            </span>
          )}
          {warmup.status === "error" && (
            <span className="flex items-center gap-1 text-xs text-destructive">
              <XCircle className="h-3 w-3" />
              {t("settings.llm.warmup_error")}
            </span>
          )}
          {isOllama && allowDockerControl && (
            <div className="flex items-center gap-2 mt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={restartOllamaContainer}
                disabled={!containerName || ollamaRestarting || warmup.status === "waiting" || warmup.status === "loading"}
                title={!containerName ? t("settings.llm.container_name_required") : undefined}
                className="text-xs h-7"
              >
                {ollamaRestarting
                  ? <><Loader2 className="mr-1.5 h-3 w-3 animate-spin" />{t("common.restarting")}</>
                  : <><RotateCcw className="mr-1.5 h-3 w-3" />{t("settings.llm.restart_ollama")}</>
                }
              </Button>
              {!containerName && <span className="text-xs text-muted-foreground">{t("settings.llm.container_name_required")}</span>}
              {ollamaRestartStatus === "ok" && <span className="flex items-center gap-1 text-xs text-green-500"><CheckCircle2 className="h-3 w-3" />{t("common.container_restarted")}</span>}
              {ollamaRestartStatus === "error" && <span className="flex items-center gap-1 text-xs text-destructive"><XCircle className="h-3 w-3" />{t("common.restart_failed")}</span>}
            </div>
          )}
        </div>
      </CardContent>}
    </Card>
  );
}

