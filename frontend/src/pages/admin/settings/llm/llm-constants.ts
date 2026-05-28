export const PROVIDER_OPTIONS = [
  { value: "ollama" },
  { value: "vllm" },
  { value: "openai" },
  { value: "nvidia" },
  { value: "huggingface" },
  { value: "openvino" },
  { value: "other" },
];

export const LLM_ROLE_LABELS: Record<string, { port?: string }> = {
  default:     { port: "11440 (vLLM)" },
  miss_kare:   { port: "11445" },
  mechanic: { port: "11445" },
  library:     { port: "11447" },
  fallback:    { port: "11445" },
  cloud:       { port: "Ekstern provider" },
  image_edit:  { port: "Ekstern API" },
};
