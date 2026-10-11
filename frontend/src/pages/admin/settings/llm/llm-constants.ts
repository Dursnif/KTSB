export const PROVIDER_OPTIONS = [
  { value: "ollama" },
  { value: "vllm" },
  { value: "openai" },
  { value: "nvidia" },
  { value: "huggingface" },
  { value: "openvino" },
  { value: "other" },
];

// Port only — never a container name here. Container names are per-install topology
// (Docker vs. bare-metal differ structurally), already live-fetched and editable in the
// "container" field below each card (LlmRoleCard.tsx's `local.container`, sourced from
// llm.yaml via GET /api/settings/llm/{role}). A hardcoded name here already drifted out of
// sync once after the 2026-09-02 container rename — see model_lock.py's restartable_containers().
// Local roles show the host:port of their own base_url (endpointLabel) — never this house's ports
// (DT03 R2 2.9: the GUI showed 11440/11447/11445 on every install). Only external roles keep a label.
export const LLM_ROLE_LABELS: Record<string, { port?: string }> = {
  cloud:       { port: "Ekstern provider" },
  image_edit:  { port: "Ekstern API" },
};

/** «host:port» of a base_url («http://ollama:11434/» → «ollama:11434»); null when there is none. */
export function endpointLabel(baseUrl?: string | null): string | null {
  if (!baseUrl) return null;
  try { return new URL(baseUrl).host || null; } catch { return null; }
}

/** Parameter count in billions read from a model name («…qwen3.5-abliterated:9b» → 9,
 *  «Qwen3.8-27B-…» → 27); null when the name does not say. Used for the basic-mode note (DT03 D8). */
export function modelSizeB(name: string): number | null {
  const m = name.match(/(?:^|[^a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z])/i);
  return m ? Number(m[1]) : null;
}

/** Below this the main agent runs in «basic mode» (README «What to expect»). */
export const FULL_MAIN_AGENT_MIN_B = 20;
