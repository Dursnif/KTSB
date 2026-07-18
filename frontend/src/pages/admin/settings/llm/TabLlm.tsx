import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  apiGetLlmSettings, apiGetServices, apiGetDockerControl, apiGetInnerVoices,
  type LlmRoleConfig, type DockerControlSettings, type InnerVoicesData,
} from "@/services/api";
import { LlmInfraCard } from "./LlmInfraCard";
import { LlmRoleCard } from "./LlmRoleCard";
import { ImageRoleCard } from "./ImageRoleCard";
import { WhisperCard, type VoiceServicesData } from "./WhisperCard";
import { PiperCard } from "./PiperCard";
import { EmbeddingCard, type EmbServicesData } from "./EmbeddingCard";
import { MemoryEmbedCard, type MemoryEmbedData } from "./MemoryEmbedCard";
import { InnerVoicesCard } from "./InnerVoicesCard";

export function TabLlm() {
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
  const [innerVoicesData, setInnerVoicesData] = useState<InnerVoicesData>({
    jing: { provider: "openvino", model_path: "", interval_seconds: 180, max_tokens: 300 },
    jang: { provider: "openvino", model_path: "", interval_seconds: 600, max_tokens: 400, turns_back: 3, inner_thoughts_retention_hours: 24 },
    node_label: "Local",
    push_token: "",
  });
  const [loading, setLoading] = useState(true);
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
    ]).then(([llm, svc, dc, iv]) => {
      setConfigs(llm);
      setDockerControl(dc);
      const s = svc as any; // eslint-disable-line @typescript-eslint/no-explicit-any
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
      setInnerVoicesData(iv);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div className="text-muted-foreground text-sm">{t("common.loading")}</div>;

  const roleOrder = ["default", "miss_kare", "mechanic", "library", "fallback", "cloud", "image_edit"];

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
          : <LlmRoleCard   key={role} role={role} config={configs[role]} onSaved={load} allConfigs={configs} allowDockerControl={dockerControl.allow_docker_control} />
      ))}
      <WhisperCard        data={voiceData}       onSaved={load} />
      <PiperCard                               onSaved={load} />
      <EmbeddingCard      data={embData}        onSaved={load} />
      <MemoryEmbedCard    data={memEmbData}     onSaved={load} />
      <InnerVoicesCard    data={innerVoicesData} onSaved={load} />
    </div>
  );
}
