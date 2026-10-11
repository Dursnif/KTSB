import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  apiGetLlmSettings, apiGetLlmModelCheck, type LlmModelCheck, apiGetServices, apiGetDockerControl, apiGetInnerVoices, apiGetLanguage,
  type LlmRoleConfig, type DockerControlSettings, type InnerVoicesData,
} from "@/services/api";
import { LlmInfraCard } from "./LlmInfraCard";
import { LlmRoleCard } from "./LlmRoleCard";
import { ImageRoleCard } from "./ImageRoleCard";
import { ToolOptionsCard } from "./ToolOptionsCard";
import { ExpertOnly } from "../shared";
import { WhisperCard, type VoiceServicesData } from "./WhisperCard";
import { PiperCard } from "./PiperCard";
import { EmbeddingCard, type EmbServicesData } from "./EmbeddingCard";
import { MemoryEmbedCard, type MemoryEmbedData } from "./MemoryEmbedCard";
import { ModelStorageCard } from "./ModelStorageCard";
import { InnerVoicesCard } from "./InnerVoicesCard";

export function TabLlm() {
  const { t } = useTranslation();
  const [configs, setConfigs] = useState<Record<string, LlmRoleConfig>>({});
  const [voiceData, setVoiceData] = useState<VoiceServicesData>({
    stt_backend: "openvino", faster_whisper_model: "", compute_type: "int8",
    stt_enabled: true, model_dir: "", cpu_threads: 0,
  });
  const [kareLanguage, setKareLanguage] = useState("en");
  const [embData, setEmbData] = useState<EmbServicesData>({
    device: "NPU", hf_model: "BAAI/bge-m3", model_path: "", emb_enabled: true,
  });
  const [memEmbData, setMemEmbData] = useState<MemoryEmbedData>({ enabled: false, model_dir: "" });
  const [innerVoicesData, setInnerVoicesData] = useState<InnerVoicesData>({
    jing: { provider: "openvino", model_path: "", interval_seconds: 180, max_tokens: 300 },
    jang: { provider: "openvino", model_path: "", interval_seconds: 600, max_tokens: 400, turns_back: 3, inner_thoughts_retention_hours: 24 },
    node_label: "Local",
    push_token: "",
  });
  const [loading, setLoading] = useState(true);
  const [modelCheck, setModelCheck] = useState<Record<string, LlmModelCheck>>({});
  const [dockerControl, setDockerControl] = useState<DockerControlSettings>({
    allow_docker_control: false,
    socket_available: false,
  });

  const load = useCallback(() => {
    Promise.all([
      apiGetLlmSettings(),
      apiGetServices(),
      apiGetDockerControl(),
      apiGetInnerVoices(),
      apiGetLanguage(),
    ]).then(([llm, svc, dc, iv, langSettings]) => {
      setConfigs(llm);
      setDockerControl(dc);
      const s = svc as any; // eslint-disable-line @typescript-eslint/no-explicit-any
      setVoiceData({
        stt_backend:          s.voice?.stt_backend          ?? "openvino",
        faster_whisper_model: s.voice?.faster_whisper_model ?? "",
        compute_type:         s.voice?.compute_type         ?? "int8",
        stt_enabled:          s.voice?.stt_enabled          ?? true,
        model_dir:            s.voice?.model_dir            ?? "",
        cpu_threads:          s.voice?.cpu_threads          ?? 0,
      });
      setKareLanguage(langSettings.kare_language || "en");
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
      setInnerVoicesData(iv);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);
  // Separate from load(): asks every Ollama instance, so a slow one must not delay the tab.
  useEffect(() => { apiGetLlmModelCheck().then(setModelCheck).catch(() => {}); }, []);

  if (loading) return <div className="text-muted-foreground text-sm">{t("common.loading")}</div>;

  const roleOrder = ["default", "miss_kare", "mechanic", "library", "fallback", "cloud", "image_edit"];
  const REASON_FREELY_FIELDS = ["max_tokens", "temperature", "top_p", "presence_penalty"] as const;

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        {t("settings.llm.description")}
        <br/>
        <strong className="text-amber-500">!</strong> {t("settings.llm.ctx_warning")}
      </p>
      <LlmInfraCard
        dockerControl={dockerControl}
        onDockerControlChange={v => setDockerControl(prev => ({ ...prev, allow_docker_control: v }))}
      />
      {roleOrder.filter(r => r in configs).map(role => (
        role === "image_edit"
          ? <ImageRoleCard key={role} role={role} config={configs[role]} onSaved={load} />
          : <LlmRoleCard   key={role} role={role} config={configs[role]} onSaved={load} allConfigs={configs} allowDockerControl={dockerControl.allow_docker_control} modelCheck={modelCheck[role]} />
      ))}
      {"reason_freely" in configs && (
        <ExpertOnly>
          <ToolOptionsCard
            role="reason_freely"
            titleKey="settings.llm.reason_freely_title"
            descKey="settings.llm.reason_freely_desc"
            fields={REASON_FREELY_FIELDS}
            config={configs["reason_freely"]}
          />
        </ExpertOnly>
      )}
      <ExpertOnly><ModelStorageCard /></ExpertOnly>
      <WhisperCard        data={voiceData}       kareLanguage={kareLanguage} onSaved={load} />
      <PiperCard                               onSaved={load} />
      <EmbeddingCard      data={embData}        onSaved={load} />
      <MemoryEmbedCard    data={memEmbData}     onSaved={load} />
      <InnerVoicesCard    data={innerVoicesData} onSaved={load} />
    </div>
  );
}
