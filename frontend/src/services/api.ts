import axios from "axios";

// HTTPS (Caddy): use same origin — Caddy routes /api/* to kaare-api
// HTTP (direct): use port 8000 directly
export const API_BASE = window.location.protocol === "https:"
    ? window.location.origin
    : `http://${window.location.hostname}:8000`;

export const api = axios.create({ baseURL: API_BASE });

api.interceptors.request.use((config) => {
  const token = sessionStorage.getItem("kaare_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ── Generate ──────────────────────────────────────────────────────────────────

export type TraceStep = { round: number; tool: string; args: string; result: string };

export async function apiGenerate(prompt: string, images?: string[]) {
  const raw = sessionStorage.getItem("kaare_user");
  const user_id: string = raw ? (JSON.parse(raw)?.username ?? "global") : "global";
  const { data } = await api.post("/api/generate", { prompt, images: images ?? [], user_id });
  return data as { text: string; trace?: TraceStep[] };
}

export async function apiPing() {
  await api.get("/api/ping");
}

export async function apiLogout() {
  await api.post("/api/auth/logout");
}

export type PendingNotification = {
  id: string;
  timer_id: string;
  rid: string;
  message: string;
  created_at: string;
  acked: boolean;
};

export async function apiGetPendingNotifications(user_id: string) {
  const { data } = await api.get(`/api/pending_notifications?user_id=${encodeURIComponent(user_id)}`);
  return data as { notifications: PendingNotification[] };
}

export async function apiAckNotification(notif_id: string, user_id: string) {
  await api.post(`/api/pending_notifications/${encodeURIComponent(notif_id)}/ack?user_id=${encodeURIComponent(user_id)}`);
}

export async function apiMissKareComment(user_id: string) {
  const { data } = await api.get(`/api/miss_kare/comment?user_id=${encodeURIComponent(user_id)}`);
  return data as { comment: string; user_id: string };
}

export type ChatTurn = { role: "user" | "assistant"; text: string; ts: string };

export async function apiChatHistory(user_id: string, limit = 60) {
  const { data } = await api.get(`/api/chat_history?user_id=${encodeURIComponent(user_id)}&limit=${limit}`);
  return data as { user_id: string; turns: ChatTurn[]; needs_unlock: boolean };
}

export async function apiSystemStatus() {
  const { data } = await api.get("/api/system_status");
  return data as { modules: { name: string; enabled: boolean }[] };
}

export type ServiceStatus = {
  key: string;
  name: string;
  description: string;
  color: string;
  online: boolean;
};

export type ModelStatus = {
  key: string;
  name: string;
  model: string;
  platform: string;
  color: string;
  online: boolean;
};

export async function apiSystemOverview() {
  const { data } = await api.get("/api/system/overview");
  return data as { services: ServiceStatus[]; models: ModelStatus[] };
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function apiLogin(username: string, pin: string) {
  const { data } = await api.post("/api/auth/login", { username, pin });
  return data as { token: string; user: KaareUser; must_change_pin: boolean };
}

export async function apiMe() {
  const { data } = await api.get("/api/auth/me");
  return data as KaareUser;
}

// ── Users ─────────────────────────────────────────────────────────────────────

export async function apiListUsers() {
  const { data } = await api.get("/api/users");
  return data as KaareUser[];
}

export async function apiCreateUser(payload: CreateUserPayload) {
  const { data } = await api.post("/api/users", payload);
  return data as KaareUser;
}

export async function apiUpdateUser(username: string, payload: UpdateUserPayload) {
  const { data } = await api.put(`/api/users/${username}`, payload);
  return data as KaareUser;
}

export async function apiUpdatePin(username: string, new_pin: string) {
  const { data } = await api.put(`/api/users/${username}/pin`, { new_pin });
  return data as { ok: boolean };
}

export async function apiGetUserFlags(username: string): Promise<Record<string, boolean>> {
  const { data } = await api.get(`/api/users/${username}/flags`);
  return data;
}

export type UserSummary = {
  username: string;
  display_name: string;
  avatar: string;
  role: string;
  pin_required: boolean;
};

export async function apiGetUserSummary(username: string): Promise<UserSummary> {
  const { data } = await api.get(`/api/users/${username}/summary`);
  return data;
}

export async function apiPutUserFlags(username: string, flags: Record<string, boolean>): Promise<Record<string, boolean>> {
  const { data } = await api.put(`/api/users/${username}/flags`, flags);
  return data;
}

export async function apiGetUnlockConfig(username: string) {
  const { data } = await api.get(`/api/users/${username}/unlock`);
  return data as { method: string; phrase: string; global_lists: boolean };
}

export async function apiPutUnlockConfig(username: string, payload: {
  method: string; phrase: string; pin: string; global_lists: boolean;
}) {
  const { data } = await api.put(`/api/users/${username}/unlock`, payload);
  return data as { ok: boolean };
}

export async function apiDeleteUser(username: string) {
  const { data } = await api.delete(`/api/users/${username}`);
  return data as { ok: boolean };
}

export async function apiRecover(username: string, seed_phrase: string, new_pin: string) {
  const { data } = await api.post("/api/users/recover", { username, seed_phrase, new_pin });
  return data as { token: string; user: KaareUser };
}

export async function apiReissueSeedPhrase(username: string) {
  const { data } = await api.post(`/api/users/${username}/reissue_seed_phrase`, {});
  return data as { seed_phrase: string };
}

export async function apiListPersonalities() {
  const { data } = await api.get("/api/personalities");
  return data as { key: string; label: string }[];
}

// ── VPN client management ─────────────────────────────────────────────────────

export type VpnClient = {
  name: string;
  username: string;
  device_name: string;
  ip: string;
  created_at: string;
};

export type VpnClientWithConfig = VpnClient & { config: string };

export async function apiVpnListClients() {
  const { data } = await api.get("/api/vpn/clients");
  return data as VpnClient[];
}

export async function apiVpnCreateClient(username: string, device_name: string) {
  const { data } = await api.post("/api/vpn/clients", { username, device_name });
  return data as VpnClientWithConfig;
}

export async function apiVpnDeleteClient(client_name: string) {
  const { data } = await api.delete(`/api/vpn/clients/${client_name}`);
  return data as { ok: boolean };
}

// ── Types ─────────────────────────────────────────────────────────────────────

export type VpnAccess = "local_only" | "ai_only" | "full_access";

export type KaareUser = {
  id: number;
  username: string;
  display_name: string;
  role: Role;
  avatar: string;
  is_active: boolean;
  is_online: boolean;
  last_seen: string | null;
  personality: string;
  vpn_access: VpnAccess;
  created_at: string;
  can_manage_child_timers?: boolean;
  notify_channel?: string;
  is_parent?: boolean;
  pin_required?: boolean;
  managed_children?: string | null;
  plex_account_id?: number | null; // P140: linked Plex server account
  frigate_tracking?: boolean; // P144-s02: «Frigate may track me» (default true)
  frigate_faces?: string[]; // P144-s02: Frigate face names that are this person
  seed_phrase?: string; // present only once, when a keypair was just generated (create or first login)
};

export type Role = "child" | "teen" | "young_adult" | "adult" | "admin" | "household" | "stranger";

export type CreateUserPayload = {
  username: string;
  display_name: string;
  role: Role;
  pin: string;
  avatar: string;
};

export type UpdateUserPayload = {
  display_name?: string;
  role?: Role;
  avatar?: string;
  is_active?: boolean;
  personality?: string;
  vpn_access?: VpnAccess;
  can_manage_child_timers?: boolean;
  notify_channel?: string;
  is_parent?: boolean;
  pin_required?: boolean;
  managed_children?: string | null;
  plex_account_id?: number; // P140: -1 = unlink
  frigate_faces?: string[]; // P144-s02: [] = unlink
};

export async function apiGenerateTempPin(username: string): Promise<{ temp_pin: string; expires_minutes: number }> {
  const { data } = await api.post(`/api/users/${username}/generate_temp_pin`);
  return data;
}

export async function apiGetTimerSettings() {
  const { data } = await api.get("/api/settings/timers");
  return data as { max_per_user: number };
}

export async function apiPutTimerSettings(max_per_user: number) {
  const { data } = await api.put("/api/settings/timers", { max_per_user });
  return data as { ok: boolean; max_per_user: number };
}

// ── Voice enrollment ──────────────────────────────────────────────────────────

export async function apiVoiceStatus(username: string) {
  const { data } = await api.get(`/api/users/${username}/voice/status`);
  return data as { username: string; has_voiceprint: boolean };
}

export async function apiVoiceEnroll(username: string, wavBlob: Blob) {
  const form = new FormData();
  form.append("file", wavBlob, "enrollment.wav");
  const { data } = await api.post(`/api/users/${username}/voice/enroll`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data as { ok: boolean; username: string; samples: number };
}

export async function apiVoiceDelete(username: string) {
  const { data } = await api.delete(`/api/users/${username}/voice`);
  return data as { ok: boolean; removed: boolean };
}

// ── Refleksjonsmøter ──────────────────────────────────────────────────────────

export async function apiReflectionDates(username: string) {
  const { data } = await api.get(`/api/reflections/${username}`);
  return data as string[];
}

export async function apiReflectionContent(username: string, date: string, pin: string) {
  const { data } = await api.post(`/api/reflections/${username}/${date}`, { pin });
  return data as { date: string; content: string };
}

export async function apiGetReflectionComment(username: string, date: string, pin: string) {
  const { data } = await api.post(`/api/reflections/${username}/comment/${date}/get`, { pin });
  return data as { comment: string };
}

export async function apiSetReflectionComment(username: string, date: string, pin: string, comment: string) {
  const { data } = await api.post(`/api/reflections/${username}/comment/${date}`, { pin, comment });
  return data as { ok: boolean };
}

export async function apiGetReflectionTopic(username: string, pin: string) {
  const { data } = await api.post(`/api/reflections/${username}/topic/get`, { pin });
  return data as { topic: string };
}

export async function apiSetReflectionTopic(username: string, pin: string, topic: string) {
  const { data } = await api.post(`/api/reflections/${username}/topic`, { pin, topic });
  return data as { ok: boolean };
}

export async function apiGetMeetingTopic(type: "reflection" | "dev") {
  const { data } = await api.get(`/api/meetings/topic/${type}`);
  return data as { topic: string };
}

// P118 B8: the dev slot's agenda themes (keys match kaare_core/memory/agenda_themes.py THEMES).
export const AGENDA_THEMES = ["answers", "tools", "memory", "agents", "house", "system", "other"] as const;

// P133 C6: topics marked resolved in a conversation (dev slot / Kåre's memory).
export type ResolvedTopic = {
  id: number;
  label: string;
  kind: string;
  theme: string;
  conclusion: string;
  resolved_at: string;
  resolved_by: string;
  echoes: number;
};

export async function apiGetResolvedTopics(): Promise<ResolvedTopic[]> {
  const { data } = await api.get("/api/meetings/resolved");
  return (data?.topics ?? []) as ResolvedTopic[];
}

export async function apiSetMeetingTopic(type: "reflection" | "dev", topic: string, theme = "") {
  await api.post(`/api/meetings/topic/${type}`, theme ? { topic, theme } : { topic });
}

export async function apiGetMeetingComment(type: "reflection" | "dev", date: string) {
  const { data } = await api.get(`/api/meetings/comment/${type}/${date}`);
  return data as { comment: string };
}

export async function apiSetMeetingComment(type: "reflection" | "dev", date: string, comment: string) {
  await api.post(`/api/meetings/comment/${type}/${date}`, { comment });
}

// ── Settings: Meeting ──────────────────────────────────────────────────────────

export interface ReflectionMeetingSettings {
  enabled: boolean;
  interval_seconds: number;
  max_rounds: number;
  kare_max_tokens: number;
  miss_kare_max_tokens: number;
  leder_preset: string;
  leder_preset_default: string;
  leder_preset_custom: string;
}

export interface DevMeetingSettings {
  // Dev slot (P118) — shown in the GUI
  enabled: boolean;
  time_budget_minutes: number;
  topics_per_meeting: number;
  max_user_traces: number;
  round_cap_per_topic: number;
  // Old persona-meeting settings — still returned by the API, no longer shown
  max_rounds: number;
  max_invest_rounds: number;
  kare_max_tokens: number;
  kare_invest_tokens: number;
  leder_preset: string;
  leder_preset_default: string;
  leder_preset_custom: string;
}

export async function apiGetReflectionMeetingSettings(): Promise<ReflectionMeetingSettings> {
  const { data } = await api.get("/api/settings/reflection");
  return data;
}

export async function apiPutReflectionMeetingSettings(payload: Partial<ReflectionMeetingSettings>) {
  await api.put("/api/settings/reflection", payload);
}

export async function apiGetDevMeetingSettings(): Promise<DevMeetingSettings> {
  const { data } = await api.get("/api/settings/dev-meeting");
  return data;
}

export async function apiPutDevMeetingSettings(payload: Partial<DevMeetingSettings>) {
  await api.put("/api/settings/dev-meeting", payload);
}

export const ROLE_LABELS: Record<Role, string> = {
  child: "Barn",
  teen: "Ungdom",
  young_adult: "Ung voksen",
  adult: "Voksen",
  admin: "Administrator",
  household: "Husstand",
  stranger: "Fremmed",
};

// ── Settings: LLM ─────────────────────────────────────────────────────────────

export type LlmProvider = "ollama" | "vllm" | "openai" | "nvidia" | "openvino" | "huggingface" | "other";

export type OllamaEnvConfig = {
  num_threads?: number | null;
  num_parallel?: number | null;
  max_loaded_models?: number | null;
  flash_attention?: boolean | null;
  kv_cache_type?: string | null;
};

export type VllmDockerConfig = {
  max_model_len?: number;
  kv_cache_dtype?: string;
  gpu_memory_utilization?: number;
  max_num_seqs?: number;
  gpu_id?: number;
};

export type LlmRoleConfig = {
  provider: LlmProvider;
  base_url: string;
  model_role: string;
  model?: string;
  timeout?: number | null;
  think?: boolean | string | null;
  options?: Record<string, number>;
  // vLLM-specific fields
  vllm_docker?: VllmDockerConfig;
  // non-Ollama text LLM fields
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  api_key_env?: string;
  api_key_set?: boolean;
  api_key_masked?: string;
  // image_edit role specific fields
  model_role_edit?: string;
  model_edit?: string;
  num_inference_steps?: number;
  guidance_scale?: number;
  true_cfg_scale?: number;
  response_format?: string;
  enabled?: boolean;
  gpu_id?: number;
  keep_warm?: boolean;
  ollama_env?: OllamaEnvConfig;
  container?: string | null;
  share_with?: string | null;
  // Set when this role belongs to the shared agent pool (llm.yaml agent_pool:)
  agent_pool_roles?: string[];
};

export async function apiGetLlmSettings(): Promise<Record<string, LlmRoleConfig>> {
  const { data } = await api.get("/api/settings/llm");
  return data;
}

export type LlmModelCheck = { model: string; base_url: string; status: "ok" | "missing" | "unreachable" };

export async function apiGetLlmModelCheck(): Promise<Record<string, LlmModelCheck>> {
  const { data } = await api.get("/api/settings/llm/model_check");
  return data;
}

export async function apiPutLlmRole(role: string, payload: Partial<LlmRoleConfig> & { api_key?: string }) {
  const { data } = await api.put(`/api/settings/llm/${role}`, payload);
  return data as { ok: boolean };
}

export async function apiRestartVllmDocker(role: string) {
  const { data } = await api.post(`/api/settings/llm/${role}/restart_docker`);
  return data as { ok: boolean; container?: string; error?: string };
}

export type VllmRestartStatus = {
  status: "idle" | "inspecting" | "stopping" | "starting" | "loading" | "ready" | "rolling_back" | "error";
  detail?: string;
  rolled_back?: boolean;
  restored?: boolean;
};

export async function apiGetVllmRestartStatus(role: string) {
  const { data } = await api.get(`/api/settings/llm/${role}/restart_docker_status`);
  return data as VllmRestartStatus;
}

// ── Ollama model management ───────────────────────────────────────────────────

export type GpuInfo = { id: number; name: string; vram_gb: number };

export type OllamaPullStatus = {
  pulling: boolean;
  status: string;
  completed: number;
  total: number;
  error: string | null;
};

export async function apiGetGpus(): Promise<GpuInfo[]> {
  const { data } = await api.get("/api/system/gpus");
  return (data.gpus ?? []) as GpuInfo[];
}

export async function apiGetOllamaModels(role: string): Promise<string[]> {
  const { data } = await api.get(`/api/ollama/models/${role}`);
  return (data.models ?? []) as string[];
}

export async function apiDeleteOllamaModel(role: string, model: string): Promise<{ ok: boolean }> {
  const { data } = await api.delete(`/api/ollama/models/${role}/${encodeURIComponent(model)}`);
  return data as { ok: boolean };
}

export async function apiPullModel(role: string, model: string): Promise<{ ok: boolean }> {
  const { data } = await api.post(`/api/ollama/pull/${role}`, { model });
  return data as { ok: boolean };
}

export async function apiGetPullStatus(role: string): Promise<OllamaPullStatus> {
  const { data } = await api.get(`/api/ollama/pull/status/${role}`);
  return data as OllamaPullStatus;
}

export type OllamaDiscoverResult = { url: string; models: string[] };
export async function apiDiscoverOllama(): Promise<{ found: OllamaDiscoverResult[] }> {
  const { data } = await api.post("/api/settings/llm/discover_ollama");
  return data as { found: OllamaDiscoverResult[] };
}

export type WarmupStatus = { status: "idle" | "waiting" | "loading" | "done" | "error" | "warning_cpu"; model?: string; detail?: string; vram_bytes?: number };
export async function apiGetWarmupStatus(role: string): Promise<WarmupStatus> {
  const { data } = await api.get(`/api/settings/llm/${role}/warmup_status`);
  return data as WarmupStatus;
}
export async function apiTriggerWarmup(role: string, model?: string): Promise<{ ok: boolean; model?: string }> {
  const { data } = await api.post(`/api/settings/llm/${role}/warmup`, model ? { model } : {});
  return data as { ok: boolean; model?: string };
}

export type DockerControlSettings = { allow_docker_control: boolean; socket_available: boolean };
export async function apiGetDockerControl(): Promise<DockerControlSettings> {
  const { data } = await api.get("/api/settings/system/docker_control");
  return data as DockerControlSettings;
}
export async function apiPutDockerControl(allow: boolean): Promise<{ ok: boolean }> {
  const { data } = await api.put("/api/settings/system/docker_control", { allow_docker_control: allow });
  return data as { ok: boolean };
}

export type ContainerInfo = { name: string; ports: number[] };
export async function apiDiscoverContainers(): Promise<{ containers: ContainerInfo[]; error?: string }> {
  const { data } = await api.get("/api/settings/llm/discover-containers");
  return data as { containers: ContainerInfo[]; error?: string };
}

export type VramEntry = { role: string; model: string; vram_bytes: number; on_cpu: boolean; base_url: string };
export async function apiGetVramOverview(): Promise<{ entries: VramEntry[] }> {
  const { data } = await api.get("/api/settings/llm/vram_overview");
  return data as { entries: VramEntry[] };
}

export async function apiRestartOllama(role: string): Promise<{ ok: boolean; container?: string; warmup_started?: boolean; error?: string }> {
  const { data } = await api.post(`/api/settings/llm/${role}/restart_ollama`);
  return data as { ok: boolean; container?: string; warmup_started?: boolean; error?: string };
}


// ── Settings: Services ────────────────────────────────────────────────────────

export type ServicesConfig = {
  home_assistant: { url: string; timeout: number; verify_seconds?: number; enabled: boolean };
  mqtt: {
    host: string;
    port: number;
    username?: string;
    tls_enabled?: boolean;
    client_id?: string;
    reconnect_interval?: number;
  };
  // P144-s19: Frigate's MQTT topic prefix sits with Frigate (absent on an older API)
  frigate: { url: string; timeout: number; snapshot_timeout: number; enabled: boolean; mqtt_topic_prefix?: string };
  plex: { url: string; timeout: number };
  voice?: { stt_enabled?: boolean };
};

export async function apiGetServices(): Promise<ServicesConfig> {
  const { data } = await api.get("/api/settings/services");
  return data;
}

export async function apiPutHa(payload: { url?: string; timeout?: number; verify_seconds?: number; enabled?: boolean }) {
  const { data } = await api.put("/api/settings/services/ha", payload);
  return data as { ok: boolean; applies?: string };
}

export async function apiPutMqtt(payload: {
  host?: string;
  port?: number;
  username?: string;
  password?: string;
  tls_enabled?: boolean;
  client_id?: string;
  reconnect_interval?: number;
}) {
  const { data } = await api.put("/api/settings/services/mqtt", payload);
  return data as { ok: boolean; applies?: string };
}

export async function apiGetVpnSettings(): Promise<{ duckdns_host: string; wg_port: number }> {
  const { data } = await api.get("/api/settings/vpn");
  return data;
}

export async function apiPutVpnSettings(payload: { duckdns_host?: string; wg_port?: number }) {
  const { data } = await api.put("/api/settings/vpn", payload);
  return data as { ok: boolean };
}

export async function apiPutFrigate(payload: { url?: string; timeout?: number; snapshot_timeout?: number; enabled?: boolean; mqtt_topic_prefix?: string }) {
  const { data } = await api.put("/api/settings/services/frigate", payload);
  return data as { ok: boolean; applies?: string };
}

export async function apiPutPlex(payload: { url?: string; timeout?: number }) {
  const { data } = await api.put("/api/settings/services/plex", payload);
  return data as { ok: boolean; applies?: string };
}

export async function apiPutEmbeddingBackend(payload: {
  device?: string;
  hf_model?: string;
  model_path?: string;
  emb_enabled?: boolean;
}) {
  const { data } = await api.put("/api/settings/services/embedding", payload);
  return data as { ok: boolean; applies?: string };
}

export async function apiPutMemoryEmbedBackend(payload: {
  enabled?: boolean;
  model_dir?: string;
}) {
  const { data } = await api.put("/api/settings/services/memory-embed", payload);
  return data as { ok: boolean; applies?: string };
}

export type MemoryEmbedModel = {
  key: string;
  tier: "small" | "medium" | "large" | "legacy";
  repo: string;
  dim: number;
  size_mb: number;
  backend: "local" | "embedding_service";
  downloaded: boolean;
  active: boolean;
  model_dir: string;
  points: number | null;
};

export type MemoryEmbedJob = {
  id: string;
  kind: "download" | "activate";
  model: string;
  status: "running" | "done" | "error";
  phase: string;
  bytes: number;
  total_bytes: number;
  progress: { total?: number; done?: number; indexed?: number; skipped_encrypted?: number; failed?: number };
  error: string | null;
  detail: string | null;
  result: { total: number; indexed: number; skipped_encrypted: number; failed: number } | null;
};

export type MemoryEmbedStatus = {
  enabled: boolean;
  active: string;
  models: MemoryEmbedModel[];
  models_dir: string;
  service_dim: number | null;
  embedding_service_enabled: boolean;
  job: MemoryEmbedJob | null;
};

export async function apiGetMemoryEmbedStatus(): Promise<MemoryEmbedStatus> {
  const { data } = await api.get("/api/settings/memory-embed/status");
  return data as MemoryEmbedStatus;
}

export async function apiPostMemoryEmbedDownload(model: string): Promise<MemoryEmbedJob> {
  const { data } = await api.post("/api/settings/memory-embed/download", { model });
  return data as MemoryEmbedJob;
}

export async function apiPostMemoryEmbedActivate(model: string): Promise<MemoryEmbedJob> {
  const { data } = await api.post("/api/settings/memory-embed/activate", { model });
  return data as MemoryEmbedJob;
}

export type SttPreset = { repo: string; tier: "small" | "medium" | "large"; size_mb: number; downloaded: boolean; active: boolean };

export type SttModelsStatus = {
  lang: string;
  backend: string;
  active: string | null;
  presets: SttPreset[];
  custom: { repo: string; downloaded: boolean; active: boolean } | null;
  cache_dir: string;
  openvino_available: boolean;
  job: MemoryEmbedJob | null;
};

export async function apiGetSttModelsStatus(): Promise<SttModelsStatus> {
  const { data } = await api.get("/api/settings/stt-models/status");
  return data as SttModelsStatus;
}

export async function apiPostSttModelDownload(repo: string): Promise<MemoryEmbedJob> {
  const { data } = await api.post("/api/settings/stt-models/download", { repo });
  return data as MemoryEmbedJob;
}

export async function apiPostSttModelActivate(repo: string): Promise<MemoryEmbedJob> {
  const { data } = await api.post("/api/settings/stt-models/activate", { repo });
  return data as MemoryEmbedJob;
}

export async function apiGetModelsDir(): Promise<{ models_dir: string }> {
  const { data } = await api.get("/api/settings/models-dir");
  return data as { models_dir: string };
}

export async function apiPutModelsDir(models_dir: string): Promise<{ ok: boolean; models_dir: string }> {
  const { data } = await api.put("/api/settings/models-dir", { models_dir });
  return data as { ok: boolean; models_dir: string };
}

export async function apiPutVoiceBackend(payload: {
  cpu_threads?: number;
  stt_backend?: string;
  faster_whisper_model?: string;
  compute_type?: string;
  language?: string;
  stt_enabled?: boolean;
  model_dir?: string;
}) {
  const { data } = await api.put("/api/settings/services/voice", payload);
  return data as { ok: boolean; applies?: string };
}

// ── Settings: HA token ────────────────────────────────────────────────────────

export async function apiGetHaToken(): Promise<{ is_set: boolean; masked: string }> {
  const { data } = await api.get("/api/settings/ha-token");
  return data;
}

export async function apiPutHaToken(token: string) {
  const { data } = await api.put("/api/settings/ha-token", { token });
  return data as { ok: boolean };
}

// ── Settings: HA Bridge ───────────────────────────────────────────────────────

export type HaBridgeConfig = {
  log_url: string;
  timeout: string;
  allowed_actions: string;
  actions?: string[]; // every action the gateway knows (read-only, P139)
};

export async function apiGetHaBridge(): Promise<HaBridgeConfig> {
  const { data } = await api.get("/api/settings/ha-bridge");
  return data;
}

export async function apiPutHaBridge(payload: Partial<HaBridgeConfig>) {
  const { data } = await api.put("/api/settings/ha-bridge", payload);
  return data as { ok: boolean; applies?: string };
}

// ── Settings: Secrets ─────────────────────────────────────────────────────────

export type SecretsStatus = {
  brave:  { is_set: boolean; masked: string };
  nvidia: { is_set: boolean; masked: string };
};

export async function apiGetSecrets(): Promise<SecretsStatus> {
  const { data } = await api.get("/api/settings/secrets");
  return data;
}

export async function apiPutSecret(name: "brave" | "nvidia", key: string) {
  const { data } = await api.put(`/api/settings/secrets/${name}`, { key });
  return data as { ok: boolean };
}

// ── Settings: Connection test ─────────────────────────────────────────────────

export async function apiTestConnection(url: string): Promise<{ ok: boolean; status_code?: number; error?: string }> {
  const { data } = await api.post("/api/settings/test-connection", { url });
  return data;
}

// ── Tool permissions ──────────────────────────────────────────────────────────

export type ToolPermissions = {
  always_included: string[];
  no_always_included?: string[];   // roles without the always_included tools (P120)
  roles: Record<string, string[]>;
};

export async function apiGetToolPermissions(): Promise<ToolPermissions> {
  const { data } = await api.get("/api/admin/tool_permissions");
  return data;
}

export async function apiSaveToolPermissions(data: ToolPermissions): Promise<{ ok: boolean; error?: string }> {
  const { data: res } = await api.put("/api/admin/tool_permissions", data);
  return res;
}

// ── Service restart ───────────────────────────────────────────────────────────

export type ServiceKey =
  | "kaare" | "gateway" | "semantic_embed" | "agents" | "embedding"
  | "argus" | "voice" | "frontend" | "ha-log-bridge" | "jing" | "jang";

export type AdminServiceStatus = {
  unit: string; active: boolean;
  // Docker-only: richer state than `active` alone can express — a container
  // that was never created (profile off) needs different UI than one the
  // restart mechanism itself can't reach at all.
  state?: "running" | "stopped" | "not_created" | "unavailable";
  reason?: string;
};

export async function apiAdminServices(): Promise<Record<string, AdminServiceStatus>> {
  const { data } = await api.get("/api/admin/services");
  return data;
}

export async function apiGetMediaHost(): Promise<{ media_host: string; detected: string; in_container: boolean }> {
  const { data } = await api.get("/api/settings/voice/media-host");
  return data;
}

export async function apiPutMediaHost(media_host: string): Promise<{ ok: boolean; applied: boolean }> {
  const { data } = await api.put("/api/settings/voice/media-host", { media_host });
  return data;
}

export async function apiRestartService(key: ServiceKey): Promise<{ ok: boolean; unit?: string; error?: string }> {
  const { data } = await api.post(`/api/admin/restart/${key}`);
  return data;
}

export async function apiSettingsRollback(): Promise<{ ok: boolean; restored: string[]; errors: string[] }> {
  const { data } = await api.post("/api/admin/settings/rollback");
  return data;
}

// ── Config snapshots ──────────────────────────────────────────────────────────

export type BackupPoint = {
  id: string;
  name: string;
  created: string;
  categories: string[];
  size_bytes: number;
  ktsb_version: string;
  /** "pre_update" = taken automatically before an in-app update (P57); not counted against the 5-point limit. */
  kind?: string;
  to_version?: string;
};

// ── In-app update (P57) ───────────────────────────────────────────────────────

export type UpdateStep = { ts: string; phase: string; message: string };
export type UpdateState = {
  phase: string;
  target?: string;
  from_version?: string;
  started?: string;
  finished?: string;
  error?: string;
  result?: string;
  steps?: UpdateStep[];
};
export type UpdateStatus = {
  docker: boolean;
  running_version: string;
  reachable: boolean;
  busy?: boolean;
  current?: string;
  problem?: string;
  can_rollback?: boolean;
  state?: UpdateState;
};
export type UpdateCheck = {
  ok: boolean;
  current: string;
  available?: boolean;
  latest?: { version: string; published: string; notes: string; url: string } | null;
  checked?: string;
  error?: string;
};

export async function apiUpdateStatus(): Promise<UpdateStatus> {
  const { data } = await api.get("/api/update/status");
  return data;
}

export async function apiUpdateCheck(force = false): Promise<UpdateCheck> {
  const { data } = await api.post("/api/update/check", { force });
  return data;
}

// DT04 R0.7: Maintenance → «Include test versions» (Expert, off by default).
export async function apiGetUpdateSettings(): Promise<{ include_test_versions: boolean }> {
  const { data } = await api.get("/api/update/settings");
  return data;
}

export async function apiPutUpdateSettings(include_test_versions: boolean): Promise<{ include_test_versions: boolean }> {
  const { data } = await api.put("/api/update/settings", { include_test_versions });
  return data;
}

export async function apiUpdateStart(version: string): Promise<{ ok: boolean; started?: string; save_point?: string }> {
  const { data } = await api.post("/api/update/start", { version });
  return data;
}

export async function apiUpdateRollback(): Promise<{ ok: boolean }> {
  const { data } = await api.post("/api/update/rollback");
  return data;
}

export async function apiSaveBackupPoint(
  categories: string[],
  name: string,
): Promise<{ ok: boolean; id?: string; name?: string; error?: string }> {
  const { data } = await api.post("/api/backup/save-point", { categories, name });
  return data;
}

export async function apiListBackupPoints(): Promise<{ points: BackupPoint[] }> {
  const { data } = await api.get("/api/backup/points");
  return data;
}

export async function apiRestoreBackupPoint(
  id: string,
  categories: string[],
  pin: string,
): Promise<RestoreResult> {
  const { data } = await api.post(`/api/backup/points/${id}/restore`, { categories, pin });
  return data;
}

export async function apiDeleteBackupPoint(id: string): Promise<{ ok: boolean }> {
  const { data } = await api.delete(`/api/backup/points/${id}`);
  return data;
}

export async function apiDownloadBackupPoint(id: string, downloadName: string): Promise<void> {
  const token = sessionStorage.getItem("kaare_token");
  const resp = await fetch(
    `${API_BASE}/api/backup/points/${id}/download`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!resp.ok) throw new Error("Download failed");
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = downloadName;
  a.click();
  URL.revokeObjectURL(url);
}

export async function apiExportBackup(categories: string[], downloadName: string): Promise<void> {
  const token = sessionStorage.getItem("kaare_token");
  const params = new URLSearchParams({ categories: categories.join(",") });
  const resp = await fetch(
    `${API_BASE}/api/backup/export?${params}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!resp.ok) throw new Error("Export failed");
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = downloadName;
  a.click();
  URL.revokeObjectURL(url);
}

export type RestoreResult = {
  ok: boolean;
  restored: string[];
  errors: string[];
  restart_needed: boolean;
  meta?: { ktsb_version?: string; created?: string };
};

export async function apiRestoreBackup(
  file: File,
  categories: string[],
  pin: string,
): Promise<RestoreResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("categories", JSON.stringify(categories));
  form.append("pin", pin);
  const { data } = await api.post("/api/backup/restore", form);
  return data;
}

export async function apiVerifyPin(username: string, pin: string): Promise<boolean> {
  try {
    await api.post("/api/auth/login", { username, pin });
    return true;
  } catch {
    return false;
  }
}

// ── Settings: Weather ─────────────────────────────────────────────────────────

export type WeatherProvider = "met.no" | "open-meteo" | "openweathermap" | "weatherapi" | "pirateweather";

export type WeatherConfig = {
  provider: WeatherProvider;
  forecast_days: number;
  show_feels_like: boolean;
  show_uv_index: boolean;
  show_sun_times: boolean;
  show_alerts: boolean;
  show_air_quality: boolean;
  use_ha_sensors: boolean;
  ha_temp_entity: string;
  ha_wind_entity: string;
  ha_wind_gust_entity: string;
  ha_wind_direction_entity: string;
  ha_precip_entity: string;
  ha_precip_last_hour_entity: string;
  ha_precip_today_entity: string;
  ha_humidity_entity: string;
  ha_pressure_entity: string;
  show_tides: boolean;
  tide_provider: "auto" | "kartverket" | "stormglass";
  use_camera_for_weather: boolean;
  weather_camera: string;
  openweathermap_key_set: boolean;
  openweathermap_key_masked: string;
  weatherapi_key_set: boolean;
  weatherapi_key_masked: string;
  pirateweather_key_set: boolean;
  pirateweather_key_masked: string;
  stormglass_key_set: boolean;
  stormglass_key_masked: string;
};

export async function apiGetWeather(): Promise<WeatherConfig> {
  const { data } = await api.get("/api/settings/weather");
  return data;
}

export async function apiPutWeather(payload: {
  provider: WeatherProvider;
  forecast_days: number;
  show_feels_like: boolean;
  show_uv_index: boolean;
  show_sun_times: boolean;
  show_alerts: boolean;
  show_air_quality: boolean;
  use_ha_sensors: boolean;
  ha_temp_entity: string;
  ha_wind_entity: string;
  ha_wind_gust_entity: string;
  ha_wind_direction_entity: string;
  ha_precip_entity: string;
  ha_precip_last_hour_entity: string;
  ha_precip_today_entity: string;
  ha_humidity_entity: string;
  ha_pressure_entity: string;
  show_tides?: boolean;
  tide_provider?: string;
  use_camera_for_weather?: boolean;
  weather_camera?: string;
  openweathermap_key?: string;
  weatherapi_key?: string;
  pirateweather_key?: string;
  stormglass_key?: string;
}) {
  const { data } = await api.put("/api/settings/weather", payload);
  return data as { ok: boolean };
}

// ── Settings: Websearch ───────────────────────────────────────────────────────

export type WebsearchConfig = {
  provider: string;
  fallback: string;
  fetch_count: number;
  max_results: number;
  content_max: number;
  searxng_url: string;
  brave_country: string;
  brave_search_lang: string;
  search_total_timeout_s: number;
  fetch_timeout_s: number;
  max_download_bytes: number;
  per_host_interval_s: number;
  page_cache_ttl_s: number;
  page_cache_max_entries: number;
  trusted_slots: number;
  official_timeout_s: number;
  background_timeout_s: number;
  open_web: boolean;
  caution: "careful" | "balanced" | "open";
  paid_calls_today?: Record<string, number>;
};

export async function apiGetWebsearch(): Promise<WebsearchConfig> {
  const { data } = await api.get("/api/settings/websearch");
  return data;
}

export async function apiPutWebsearch(payload: Partial<WebsearchConfig>) {
  const { data } = await api.put("/api/settings/websearch", payload);
  return data as { ok: boolean };
}

// ── Settings: Kåre refleksjon ─────────────────────────────────────────────────

export type ReflectionConfig = {
  enabled: boolean;
  interval_seconds: number;
};

export async function apiGetReflectionSettings(): Promise<ReflectionConfig> {
  const { data } = await api.get("/api/settings/reflection");
  return data;
}

export async function apiPutReflectionSettings(payload: Partial<ReflectionConfig>) {
  const { data } = await api.put("/api/settings/reflection", payload);
  return data as { ok: boolean };
}

// ── Settings: Kåre-innstillinger ─────────────────────────────────────────────

export type ContributorMode = "all" | "selected" | "admin_only";

export type PersonalityMode = "minimal" | "letvekt" | "standard" | "full" | "komplett" | "egendefinert";

export type KareSettings = {
  assistant_name: string;
  hotword: string;
  personality_mode: PersonalityMode;
  personality_core_custom: string;
  personality_core_default: string;
  personality_self: {
    contributors: ContributorMode;
    allowed_users: string[];
  };
  automation_reflex: {
    contributors: ContributorMode;
    allowed_users: string[];
  };
};

export async function apiGetKareSettings(): Promise<KareSettings> {
  const { data } = await api.get("/api/settings/kare");
  return data;
}

export async function apiPutKareSettings(payload: Partial<KareSettings> & { personality_self?: KareSettings["personality_self"] } & { automation_reflex?: KareSettings["automation_reflex"] } & { personality_core_custom?: string }) {
  const { data } = await api.put("/api/settings/kare", payload);
  return data as { ok: boolean };
}

// ── Settings: Kåre's memory drawers in the prompt (P133 C6) ───────────────────

export type MemoryPromptSettings = {
  relevance_enabled: boolean;
  top_k: number;
  min_score: number;
  echo_min_score: number;
  lessons_max_chars: number;
  lessons_write_max_chars: number;
  core_facts_max_chars: number;
  index_max_chars: number;
  resolve_roles: string[];
  kare_reminders: {
    injection_max_chars: number;
    note_prompt_chars: number;
    write_max_chars: number;
    max_entries: number;
  };
  roles: string[];
  relevance_active: boolean; // read-only: switch on AND an embedding service enabled (N25)
};

export async function apiGetMemoryPrompt(): Promise<MemoryPromptSettings> {
  const { data } = await api.get("/api/settings/memory-prompt");
  return data;
}

export async function apiPutMemoryPrompt(payload: Partial<Omit<MemoryPromptSettings, "roles" | "relevance_active">>) {
  await api.put("/api/settings/memory-prompt", payload);
}

// ── Settings: Language ────────────────────────────────────────────────────────

export type LanguageSettings = { language: string; kare_language: string };

export type NetworkSettings = {
  local_subnets: string[];
  vpn_subnet: string;
  trusted_proxies: string[];
  api_docs: boolean;
};

export async function apiGetNetwork(): Promise<NetworkSettings> {
  const { data } = await api.get("/api/settings/network");
  return data;
}

export async function apiPutNetwork(payload: Partial<NetworkSettings>): Promise<NetworkSettings> {
  const { data } = await api.put("/api/settings/network", payload);
  return data;
}

export type RetentionDays = {
  interactions_retention_days: number;
  episodes_retention_days: number;
};  // P150: the think cache is kept by size (Logs/Storage), not by days

export type LogsAndDataSettings = {
  flush_interval_seconds: number;
  retention: RetentionDays;
  think_response_max_chars: number;   // P142: reply length kept in the think log
  defaults: { flush_interval_seconds: number; retention: RetentionDays; think_response_max_chars: number };
  limits: { flush_max: number; think_response_min: number; think_response_max: number };
  recommended: { flush_min: number; flush_max: number; retention_warn_below_days: number };
};

export async function apiGetLogsAndData(): Promise<LogsAndDataSettings> {
  const { data } = await api.get("/api/settings/logs-and-data");
  return data;
}

export async function apiPutLogsAndData(payload: {
  flush_interval_seconds?: number;
  retention?: Partial<RetentionDays>;
  think_response_max_chars?: number;
}): Promise<LogsAndDataSettings> {
  const { data } = await api.put("/api/settings/logs-and-data", payload);
  return data;
}

// Settings → Logs/Storage (P150): size limits, oldest first, nothing deleted for being old.
export type StorageGroup = {
  key: string; used_mb: number; limit_mb: number | null; controlled: boolean;
  classes: string[]; uncontrolled: string[]; per_user: boolean;
  home: string;                    // "frigate" | "images" = set in that GUI place, not here
};
export type StorageClass = {
  key: string; group: string; used_mb: number; limit_mb: number | null; controlled: boolean;
  per_user: boolean; phase: string; home: string;
};
export type StorageDisk = {
  mount: string; total_gb: number; free_gb: number; classes: string[]; used_by_ktsb_gb: number;
  limits_gb: number; unlimited: string[]; over_commit: boolean;
};

export type DiskUsage = {
  groups: StorageGroup[];
  classes: StorageClass[];
  disks: StorageDisk[];
  users: number;
  argus: {
    limit_mb: number;              // 0 = off
    default_mb: number;
    max_mb: number;
    confirmed: boolean;            // saved by the user in the GUI
    floor_mb: number | null;       // upgrade guard: kept until the user saves a limit
    used_mb: number;
    points: number;
    measured_by: "telemetry" | "estimate" | "unavailable";
    last_run: null | {
      ts?: string; status?: string; deleted?: number;
      rolled_days?: number; first_day?: string; last_day?: string;
    };
  };
};

export async function apiGetDiskUsage(): Promise<DiskUsage> {
  const { data } = await api.get("/api/settings/disk-usage");
  return data;
}

export async function apiPutDiskUsage(
  payload: { argus_events_mb?: number } | { group: string; limit_mb: number } | { class: string; limit_mb: number },
): Promise<DiskUsage> {
  const { data } = await api.put("/api/settings/disk-usage", payload);
  return data;
}

export type NetworkSuggestion = { suggested_subnet: string; local_subnets: string[]; vpn_subnet: string; is_default: boolean };

export async function apiGetNetworkSuggestion(): Promise<NetworkSuggestion> {
  const { data } = await api.get("/api/onboarding/network-suggestion");
  return data;
}

export async function apiGetLanguage(): Promise<LanguageSettings> {
  const { data } = await api.get("/api/settings/language");
  return data;
}

export async function apiPutLanguage(language: string, kare_language?: string) {
  const payload: Record<string, string> = { language };
  if (kare_language !== undefined) payload.kare_language = kare_language;
  const { data } = await api.put("/api/settings/language", payload);
  return data as { ok: boolean; language: string; kare_language: string };
}

// ── Settings: Trusted sources ─────────────────────────────────────────────────

export type SourceType = "public_authority" | "encyclopedia" | "newsroom" | "research" | "official" | "other_serious";
export type TrustLevel = "high" | "normal" | "low";
export type TrustedEntry = { domain: string; description?: string; type?: SourceType; trust?: TrustLevel };
export type TrustedSources = Record<string, TrustedEntry[]>;

export async function apiGetTrustedSources(): Promise<TrustedSources> {
  const { data } = await api.get("/api/settings/trusted-sources");
  return data;
}

export async function apiPutTrustedSources(payload: TrustedSources) {
  const { data } = await api.put("/api/settings/trusted-sources", payload);
  return data as { ok: boolean; categories: number; domains: number };
}

export type LawSettings = {
  enabled: boolean;
  status: { built_at: string | null; documents: number; paragraphs: number };
  refreshing: boolean;
  attribution: string;
};

export async function apiGetLaw(): Promise<LawSettings> {
  const { data } = await api.get("/api/settings/law");
  return data;
}

export async function apiPutLaw(enabled: boolean) {
  const { data } = await api.put("/api/settings/law", { enabled });
  return data as { ok: boolean; enabled: boolean };
}

export async function apiRefreshLaw() {
  const { data } = await api.post("/api/settings/law/refresh");
  return data as { ok: boolean; started: boolean; refreshing: boolean };
}

export async function apiGetBlockedSources(): Promise<string[]> {
  const { data } = await api.get("/api/settings/blocked-sources");
  return (data?.blocked ?? []) as string[];
}

export async function apiPutBlockedSources(blocked: string[]) {
  const { data } = await api.put("/api/settings/blocked-sources", { blocked });
  return data as { ok: boolean; blocked: string[] };
}

// ── Settings: Images ──────────────────────────────────────────────────────────

export type ImageSettings = {
  max_per_user_count: number;
  max_per_user_mb: number;
};

export type ImageUserStats = {
  user_id: string;
  count: number;
  mb: number;
};

export async function apiGetImageSettings(): Promise<ImageSettings> {
  const { data } = await api.get("/api/settings/images");
  return data;
}

export async function apiPutImageSettings(payload: Partial<ImageSettings>) {
  const { data } = await api.put("/api/settings/images", payload);
  return data as { ok: boolean };
}

export async function apiGetImageStats(): Promise<ImageUserStats[]> {
  const { data } = await api.get("/api/admin/images/stats");
  return data;
}

// ── Settings: Plex token ──────────────────────────────────────────────────────

export async function apiGetPlexToken(): Promise<{ is_set: boolean; masked: string }> {
  const { data } = await api.get("/api/settings/plex-token");
  return data;
}

export async function apiPutPlexToken(token: string) {
  const { data } = await api.put("/api/settings/plex-token", { token });
  return data as { ok: boolean; restart_required: boolean };
}

// P140: Plex listener state + server accounts (for linking a KTSB user)
export type PlexListenerState = "starting" | "not_configured" | "connecting" | "connected" | "token_rejected" | "unreachable";

export async function apiGetPlexStatus(): Promise<{ state: PlexListenerState; detail: string; since: number }> {
  const { data } = await api.get("/api/settings/plex/status");
  return data;
}

export async function apiGetPlexAccounts(): Promise<{ accounts: { id: number; name: string }[]; error: string }> {
  const { data } = await api.get("/api/settings/plex/accounts");
  return data;
}

// P144-s02: «Frigate may track me» + the Frigate face library for linking
export type FrigateTracking = {
  username: string;
  tracking: boolean;
  expressions?: boolean;  // P144-s27b: «Uttrykk og gester», same who-decides rule; absent on an old API
  faces: string[];
  decided_by: "self" | "guardian" | "admin" | "none";
  can_edit: boolean;
};

export async function apiGetFrigateTracking(username: string): Promise<FrigateTracking> {
  const { data } = await api.get(`/api/users/${username}/frigate`);
  return data;
}

export async function apiPutFrigateTracking(username: string, tracking: boolean): Promise<FrigateTracking> {
  const { data } = await api.put(`/api/users/${username}/frigate`, { tracking });
  return data;
}

// P144-s27b: «Uttrykk og gester» for one person (same endpoint and rule as «Frigate kan spore meg»)
export async function apiPutFrigateExpressions(username: string, expressions: boolean): Promise<FrigateTracking> {
  const { data } = await api.put(`/api/users/${username}/frigate`, { expressions });
  return data;
}

export async function apiGetFrigateFaces(): Promise<{ faces: string[]; error: "" | "not_configured" | "unreachable" }> {
  const { data } = await api.get("/api/frigate/faces");
  return data;
}

// ── Frigate user: face classification (P144-s05) ─────────────────────────────

export type FaceClass = "household" | "safe_guest" | "not_welcome" | "unclassified";
export type FaceAttributes = { beard: string; glasses: string; age_band: string; headwear_often: string };
export type FrigateFaceRow = {
  name: string;
  class: FaceClass;
  attributes: FaceAttributes;
  in_library: boolean;
  images: number;
  linked_user: { username: string; display_name: string } | null;
};
export type FrigateFaceList = {
  faces: FrigateFaceRow[];
  unclassified_count: number;
  unclassified_name_days: number;
  classes: FaceClass[];
  attribute_values: Record<keyof FaceAttributes, string[]>;
  error: "" | "not_configured" | "unreachable";
};

export async function apiGetFrigateFaceList(): Promise<FrigateFaceList> {
  const { data } = await api.get("/api/frigate_user/faces");
  return data;
}

export async function apiPutFrigateFace(name: string, cls: FaceClass, attributes: Partial<FaceAttributes>): Promise<FrigateFaceRow> {
  const { data } = await api.put(`/api/frigate_user/faces/${encodeURIComponent(name)}`, { class: cls, attributes });
  return data;
}

export async function apiPutFrigateFaceSettings(unclassified_name_days: number): Promise<{ unclassified_name_days: number }> {
  const { data } = await api.put("/api/frigate_user/settings", { unclassified_name_days });
  return data;
}

/** One training image of a face as an object URL (header auth — never a token in the URL). Caller revokes it. */
export async function apiGetFrigateFaceThumb(name: string): Promise<string> {
  const { data } = await api.get(`/api/frigate_user/faces/${encodeURIComponent(name)}/thumb`, { responseType: "blob" });
  return URL.createObjectURL(data);
}

// ── Settings: Aliases ─────────────────────────────────────────────────────────

export type AliasesConfig = {
  aliases: Record<string, string>;
  rooms: Record<string, string[]>;
  room_entities: Record<string, string[]>;
};

export async function apiGetAliases(): Promise<AliasesConfig> {
  const { data } = await api.get("/api/settings/aliases");
  return data;
}

export async function apiPutAliases(payload: Partial<AliasesConfig>) {
  const { data } = await api.put("/api/settings/aliases", payload);
  return data as { ok: boolean };
}

// ── Settings: Rooms (P137 place register) ──────────────────────────────────────

export type ImportMode = "rooms_only" | "pick" | "assist" | "label" | "all";

export type RoomsRaw = {
  settings?: { import_mode?: ImportMode; import_label?: string; import_domains?: string[] };
  places?: Record<string, { name?: string; floor?: string; synonyms?: string[]; hidden?: boolean; template_example?: boolean }>;
  floors?: Record<string, { name?: string; level?: number; synonyms?: string[] }>;
  things?: Record<string, { place?: string; note?: string; name?: string }>;
};

export type PlaceView = {
  id: string; name: string; source: "ha" | "manual"; floor: string; synonyms: string[]; ha_aliases: string[];
  hidden: boolean; template_example: boolean; things: number; unregistered: number;
};
export type FloorView = { id: string; name: string; source: "ha" | "manual"; level: number | null; synonyms: string[]; ha_aliases: string[] };
export type ThingView = {
  ref: string; name: string; source: "ha" | "mqtt" | "node" | "frigate" | "manual"; place: string; note: string;
  domain: string; explicit: boolean; in_ha: boolean;
  missing?: "" | "gone" | "orphan";  // HA has nothing by this id / only a leftover of it
};
export type RoomsView = {
  settings: { import_mode: ImportMode; import_label: string; import_domains: string[] };
  import_modes: ImportMode[];
  ha: { state: string; last_sync: string | null; error: string; counts: Record<string, number>; connected: boolean };
  floors: FloorView[];
  places: PlaceView[];
  things: ThingView[];
  similar: { manual: string; ha: string }[];
  collisions: Record<string, string[]>;
  refs: Record<string, { place: string; name: string; registered: boolean; in_ha: boolean; ha_place: string; missing?: "" | "gone" | "orphan" }>;
  nodes: Record<string, { place: string; note: string; room: string }>;
  raw: RoomsRaw;
};
export type HaTreeEntity = { ref: string; name: string; domain: string; user_facing: boolean; exposed: boolean; labels: string[]; orphan?: boolean };
export type HaTreeDevice = { id: string; name: string; entities: HaTreeEntity[] };
export type HaTreeArea = { id: string; name: string; floor: string; devices: HaTreeDevice[] };
export type HaTree = { areas: HaTreeArea[]; unplaced: HaTreeArea; labels: { id: string; name: string }[]; fetched_at: string };

export async function apiGetRooms(): Promise<RoomsView> {
  const { data } = await api.get("/api/settings/rooms");
  return data;
}

export async function apiPutRooms(raw: RoomsRaw): Promise<RoomsView> {
  const { data } = await api.put("/api/settings/rooms", raw);
  return data;
}

export async function apiSyncRooms(): Promise<RoomsView> {
  const { data } = await api.post("/api/settings/rooms/sync");
  return data;
}

export async function apiLinkPlace(manual: string, ha: string): Promise<RoomsView> {
  const { data } = await api.post("/api/settings/rooms/link", { manual, ha });
  return data;
}

export async function apiGetHaTree(): Promise<HaTree> {
  const { data } = await api.get("/api/settings/rooms/ha_tree");
  return data;
}

// ── Settings: Nodes ───────────────────────────────────────────────────────────

export type NodeConfig = {
  room: string;
  description?: string;
  type:
    | "ha_media_player"
    | "esp32"
    | "wyoming"
    | "chromecast"
    | "snapcast"
    | "airplay"
    | "dlna"
    | "apple_tv"
    | "samsung_tv"
    | "android_tv"
    | "google_tv"
    | "fire_tv"
    | "lg_tv"
    | "projector"
    | string;
  entity_id?: string;
  host?: string;
  api_port?: number;
  mac?: string;
  adb_port?: number;
  tvoverlay_port?: number;
  lead_in_ms?: number;          // N30: silence before a reply (cold speaker)
  lead_in_playing_ms?: number;  // N30: silence when the speaker was already playing
  mic_enabled?: boolean;
  has_audio?: boolean;
  has_display?: boolean;
  token?: string;
  default_user?: string;
  place?: string;          // P137: place id from the place register
  location_note?: string;  // P137: where in the place the node stands
  trust?: "indoor" | "outdoor";   // who an unidentified speaker is here (P120)
  audio_out?: string[] | "auto";  // P138: where the spoken reply goes ("local" = this machine)
  image_out?: string[] | "auto";  // P138: screens tools show things on when no target is named
  channels?: Record<string, NodeChannel>;  // P139: ways KTSB reaches this device (found by probing)
  screen_max?: string;            // P151: largest live video this screen gets ("640x360" | "1280x720" | "1920x1080")
  trigger?: NodeTrigger | null;   // P138: start this node's mic from a Home Assistant press
  webhook_token?: string;         // P138: token for POST /trigger/{node} (HA automation fallback)
  enabled: boolean;
};

export type NodeTrigger =
  | { kind: "ha_event"; event_type: string; match: Record<string, string | number>; label?: string }
  | { kind: "entity"; entity_id: string; to?: string; label?: string };

export type NodeTriggersStatus = {
  state: "idle" | "not_configured" | "connected" | "error";
  error: string;
  nodes: Record<string, { ok: boolean; reason: string }>;
};

export async function apiNodeTriggersStatus(): Promise<NodeTriggersStatus> {
  const { data } = await api.get("/api/settings/nodes/triggers/status");
  return data;
}

export async function apiNodeTriggersLearn(): Promise<
  { ok: true; trigger: NodeTrigger; admin: boolean } | { ok: false; reason: string; admin?: boolean; error?: string }
> {
  const { data } = await api.post("/api/settings/nodes/triggers/learn", {}, { timeout: 30000 });
  return data;
}

export async function apiNodeWebhook(nodeId: string, action: "new" | "remove") {
  const { data } = await api.post(`/api/settings/nodes/${encodeURIComponent(nodeId)}/webhook`, { action });
  return data as { ok: boolean; token: string; url: string };
}

export type VoicePipelineSettings = {
  end_of_speech: "auto" | "fixed";
  silence_seconds: number;
  silence_amplitude: number;
  max_record_seconds: number;
  duck_on_listen: boolean;
  duck_volume: number;
  duck_mpd: boolean;
};

export async function apiGetVoicePipeline(): Promise<VoicePipelineSettings> {
  const { data } = await api.get("/api/settings/voice_pipeline");
  return data;
}

export async function apiPutVoicePipeline(settings: Partial<VoicePipelineSettings>) {
  const { data } = await api.put("/api/settings/voice_pipeline", settings);
  return data as VoicePipelineSettings & { ok: boolean };
}

// N29 — is each node reachable now (Nodes page WiFi icon)
export type NodeChannel = { enabled?: boolean; port?: number; found_at?: string; last_seen?: string; description_url?: string };  // description_url: DLNA only (DT04 R0.1)

export type NodeStatus = {
  status: "online" | "offline" | "unknown";
  via: string;
  detail: string;
  checked_at: number;
  channels?: Record<string, boolean>;  // P139: did each channel answer
};

export async function apiNodePair(nodeId: string, step: "start" | "finish" | "status", code = "") {
  const { data } = await api.post(`/api/settings/nodes/${encodeURIComponent(nodeId)}/pair/${step}`, { code });
  return data as { ok?: boolean; step?: string; error?: string; channel?: string; paired?: boolean };
}

// P151 — KTSB's screen page on a node (pairing a screen without Cast with the code it shows)
export type ScreenStatus = {
  enabled: boolean; paired: boolean; seen_recently: boolean; page_url: string;
  content: { kind: string; camera?: string; title?: string; expires_in?: number; playing?: boolean } | null;
};

export async function apiNodeScreen(nodeId: string, step: "status" | "pair" | "unpair", code = "") {
  const { data } = await api.post(`/api/settings/nodes/${encodeURIComponent(nodeId)}/screen/${step}`, { code });
  return data as ScreenStatus & { ok?: boolean; off?: boolean; error?: string; was_paired?: boolean };
}

export async function apiGetScreens() {
  const { data } = await api.get("/api/settings/screens");
  return data as { enabled: boolean; live_minutes: number; rtsp_port: number };
}

export async function apiPutScreens(payload: { enabled: boolean; live_minutes: number; rtsp_port: number }) {
  const { data } = await api.put("/api/settings/screens", payload);
  return data as { ok: boolean };
}

export async function apiProbeNodeChannels(node: NodeConfig) {
  const { data } = await api.post("/api/settings/nodes/probe_channels", { node });
  return data as { found: Record<string, number>; channels: Record<string, NodeChannel> };
}

export async function apiGetNodesStatus(): Promise<Record<string, NodeStatus>> {
  const { data } = await api.get("/api/settings/nodes/status");
  return data.nodes ?? {};
}

// P138 4b — the KTSB Direct node's microphone (this machine)
export type AudioInputDevice = { index: number; name: string; rate: number; channels: number; default: boolean };
export type DirectAudioView = {
  mic_device: string | number | null;
  mic_sample_rate: number | null;
  inputs: AudioInputDevice[];
  error: "" | "no_audio_backend" | "no_input" | "scan_failed" | "bridge_unreachable";
};

export async function apiGetDirectAudio(): Promise<DirectAudioView> {
  const { data } = await api.get("/api/settings/nodes/direct_audio");
  return data;
}

export async function apiPutDirectAudio(mic_device: string | null) {
  const { data } = await api.put("/api/settings/nodes/direct_audio", { mic_device });
  return data as { ok: boolean; mic_device: string | null };
}

export async function apiGetNodes(): Promise<{ nodes: Record<string, NodeConfig> }> {
  const { data } = await api.get("/api/settings/nodes");
  return data;
}

export async function apiPutNodes(nodes: Record<string, NodeConfig>) {
  const { data } = await api.put("/api/settings/nodes", { nodes });
  return data as { ok: boolean };
}

export async function apiTestDisplayNode(nodeId: string) {
  const { data } = await api.post(`/api/settings/nodes/${encodeURIComponent(nodeId)}/test_display`);
  return data as { ok: boolean; method?: string; channel?: string; tried?: { channel: string; error: string }[] };
}

// ── Settings: SSH Nodes ───────────────────────────────────────────────────────

export interface SshNodeConfig {
  label?: string;
  host: string;
  user: string;
  port: number;
  ssh_key: string;
  node_type: "linux" | "ha_os";
  sudo_enabled: boolean;
  sudo_commands: string[];
}

export interface SshNodesData {
  local: { sudo_enabled: boolean };
  nodes: Record<string, SshNodeConfig>;
}

export async function apiGetSshNodes(): Promise<SshNodesData> {
  const { data } = await api.get("/api/settings/ssh-nodes");
  return data;
}

export async function apiPutSshNodes(payload: Partial<SshNodesData>) {
  const { data } = await api.put("/api/settings/ssh-nodes", payload);
  return data as { ok: boolean };
}

export async function apiTestSshNode(params: {
  host: string; user: string; port: number; ssh_key: string;
}) {
  const { data } = await api.post("/api/settings/ssh-nodes/test", params);
  return data as { ok: boolean; latency_ms: number; error?: string };
}

// ── Settings: Capabilities ────────────────────────────────────────────────────

export type DomainEntry = {
  enabled: boolean;
  url?: string;
  notes?: string;
};

export type ServiceEntry = {
  enabled: boolean;
  notes?: string;
};

export type CapabilitiesConfig = {
  domains: Record<string, DomainEntry>;
  distribution_profile: string;
  services?: Record<string, ServiceEntry>;
};

export async function apiGetCapabilities(): Promise<CapabilitiesConfig> {
  const { data } = await api.get("/api/settings/capabilities");
  return data;
}

export async function apiPutCapabilities(payload: Partial<CapabilitiesConfig>) {
  const { data } = await api.put("/api/settings/capabilities", payload);
  return data as { ok: boolean };
}

// ── Onboarding ────────────────────────────────────────────────────────────────

export type OnboardingStep = { id: string; label: string; complete: boolean };
export type OnboardingHint = { id: string; label: string; set: boolean };

export type OnboardingStatus = {
  complete: boolean;
  steps: OnboardingStep[];
  optional_hints: OnboardingHint[];
};

export async function apiGetOnboardingStatus(): Promise<OnboardingStatus> {
  const { data } = await api.get("/api/onboarding/status");
  return data;
}

// ── Agent tools ───────────────────────────────────────────────────────────────

export type AgentToolsConfig = {
  mechanic: Record<string, boolean>;
  miss_kare: Record<string, boolean>;
  miss_library: Record<string, boolean>;
};

export async function apiGetAgentTools(): Promise<AgentToolsConfig> {
  const { data } = await api.get("/api/settings/agent_tools");
  return data;
}

export async function apiPutAgentTools(payload: AgentToolsConfig) {
  const { data } = await api.put("/api/settings/agent_tools", payload);
  return data as { ok: boolean };
}

// Model guard (P142): the enforcer box between KTSB and the LLM.
export type EnforcerMode = "off" | "shadow" | "on";

export type EnforcerProfileMode = {
  profile: string;
  default_mode: EnforcerMode;
  mode: EnforcerMode;
};

export type EnforcerDetector = {
  name: string;
  kind: "reply" | "tool_call";
  profiles: EnforcerProfileMode[];   // one switch per caller (P142 Phase 3)
  on_fail: string;
  on_empty: string;
};

export type EnforcerDetectorStats = {
  retry: number;
  shadow: number;
  tool_correction: number;
  outcomes: Record<string, number>;
  last_ts: string;
};

export type EnforcerHit = {
  ts: string;
  rid: string;
  detector: string;
  event: string;
  phrase: string;
  outcome: string;
};

export type ModelEnforcerStatus = {
  modes: EnforcerMode[];
  detectors: EnforcerDetector[];
  stats: { days: number; per_detector: Record<string, EnforcerDetectorStats>; recent: EnforcerHit[] };
};

export async function apiGetModelEnforcer(): Promise<ModelEnforcerStatus> {
  const { data } = await api.get("/api/settings/model-enforcer");
  return data;
}

// Keys are «profile:detector».
export async function apiPutModelEnforcer(modes: Record<string, EnforcerMode>) {
  const { data } = await api.put("/api/settings/model-enforcer", { modes });
  return data as { ok: boolean; stored: Record<string, EnforcerMode> };
}

export type MeetingRolesConfig = {
  mechanic: string;
  mechanic_custom: string;
  mechanic_default: string;
};

export async function apiGetMeetingRoles(): Promise<MeetingRolesConfig> {
  const { data } = await api.get("/api/settings/meeting-roles");
  return data;
}

export async function apiPutMeetingRoles(payload: Partial<MeetingRolesConfig>) {
  const { data } = await api.put("/api/settings/meeting-roles", payload);
  return data as { ok: boolean };
}

// ── Settings: Piper TTS ───────────────────────────────────────────────────────

export type PiperPreset = {
  model: string;
  tier: string;
  tier_label: string;
  size_mb: number;
  gender: string;
  local_path: string;
  downloaded: boolean;
  active: boolean;
};

export async function apiGetPiperModels(): Promise<Record<string, PiperPreset[]>> {
  const { data } = await api.get("/api/settings/piper/models");
  return data;
}

export async function apiPostPiperDownload(lang: string, model_name: string): Promise<{ job_id: string }> {
  const { data } = await api.post("/api/settings/piper/download", { lang, model_name });
  return data;
}

export async function apiGetPiperDownloadStatus(job_id: string): Promise<{ status: string; error?: string }> {
  const { data } = await api.get(`/api/settings/piper/download/${job_id}`);
  return data;
}

export async function apiPutPiperActivate(lang: string, model_path: string): Promise<{ ok: boolean }> {
  const { data } = await api.put("/api/settings/piper/activate", { lang, model_path });
  return data;
}

// ── Inner voices (Jing + Jang) ────────────────────────────────────────────────

export type InnerVoicesData = {
  jing: { provider: string; model_path: string; push_url?: string; interval_seconds: number; max_tokens: number };
  jang: { provider: string; model_path: string; push_url?: string; interval_seconds: number; max_tokens: number; turns_back: number };
  node_label: string;
  push_token: string;
};

export async function apiGetInnerVoices(): Promise<InnerVoicesData> {
  const { data } = await api.get("/api/settings/inner-voices");
  return data;
}

export async function apiPutInnerVoices(payload: {
  jing_provider?: string;
  jing_model_path?: string;
  jing_push_url?: string;
  jang_provider?: string;
  jang_model_path?: string;
  jang_push_url?: string;
  node_label?: string;
  push_token?: string;
  generate_token?: boolean;
}): Promise<{ ok: boolean }> {
  const { data } = await api.put("/api/settings/inner-voices", payload);
  return data;
}

// ── Voice: browser STT/TTS ────────────────────────────────────────────────────

// HTTPS (Caddy): same origin /voice/* — the browser blocks http:// calls from an https page and the mic
// needs HTTPS (P127). HTTP (direct, as at home with vite preview): port 8011, like the API on 8000.
const VOICE_BRIDGE = window.location.protocol === "https:"
    ? `${window.location.origin}/voice`
    : `http://${window.location.hostname}:8011`;
// The voice bridge checks the login with the main API (P120)
const voiceAuth = (): Record<string, string> => {
  const tok = sessionStorage.getItem("kaare_token");
  return tok ? { Authorization: `Bearer ${tok}` } : {};
};

export async function apiTranscribe(audioBlob: Blob): Promise<{ text: string }> {
  const form = new FormData();
  form.append("file", audioBlob, "recording.webm");
  const res = await fetch(`${VOICE_BRIDGE}/transcribe`, { method: "POST", body: form, headers: voiceAuth() });
  if (!res.ok) throw new Error(`Transcribe error: ${res.status}`);
  return res.json();
}

export async function apiTtsFile(text: string): Promise<{ url: string }> {
  const res = await fetch(`${VOICE_BRIDGE}/tts_file`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...voiceAuth() },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`TTS error: ${res.status}`);
  // A voice service from before P127 still answers with an absolute url — use it until that service restarts
  const body = (await res.json()) as { path?: string; url?: string };
  return { url: body.path ? `${VOICE_BRIDGE}${body.path}` : (body.url ?? "") };
}

// ── Notifications + Normalcy ──────────────────────────────────────────────────

export type NormalcyEvent = {
  ts: string;
  camera: string;
  camera_friendly: string;
  label: string;
  score: number;
  deviation_score: number;
  baseline_confidence: number;
  source_key: string;
  hour_bucket: string;
  weekday: string;
  correction: { verdict: string; comment: string; by: string; ts: string } | null;
  event_id: string;
};

export async function apiGetNotifications() {
  const { data } = await api.get("/api/settings/notifications");
  return data as {
    enabled: boolean;
    normalcy_min_score: number;
    normalcy_min_confidence: number;
    normalcy_cameras: string[];
    ha_notify_entity: string;
  };
}

export async function apiPutNotifications(payload: {
  enabled?: boolean;
  normalcy_min_score?: number;
  normalcy_min_confidence?: number;
  normalcy_cameras?: string[];
  ha_notify_entity?: string;
}) {
  const { data } = await api.put("/api/settings/notifications", payload);
  return data as { ok: boolean };
}

export async function apiGetNormalcyEvents(params?: {
  days?: number;
  min_score?: number;
  min_confidence?: number;
  limit?: number;
  label?: string;
}) {
  const { data } = await api.get("/api/normalcy/events", { params });
  return data as { events: NormalcyEvent[] };
}

export async function apiPostNormalcyCorrect(body: {
  source_key: string;
  hour_bucket: string;
  weekday: string;
  verdict: string;
  comment?: string;
}) {
  const { data } = await api.post("/api/normalcy/correct", body);
  return data as { ok: boolean };
}

export async function apiDeleteNormalcyCorrect(body: {
  source_key: string;
  hour_bucket: string;
  weekday: string;
}) {
  const { data } = await api.delete("/api/normalcy/correct", { data: body });
  return data as { ok: boolean; removed: boolean };
}

// ── P144-s06a: the Frigate user's shadow pipe — switch + settings (configs/frigate_user.yaml) ──
export type FrigateUserPipeSettings = {
  enabled: boolean;
  episode_gap_s: number;
  episode_min_movement: number;
  visit_gap_s: number;
  visit_fold_interval_s: number;
  l0_min_face_px: number;
  l0_min_claim_score: number;
  episode_frames_max_mb: number;   // P144-s11: whole frame sets per episode (0 = store none)
  episode_frames_keep_mb: number;  // the one kept frame per episode
  frigate_db_max_mb: number;       // P150: size limit of frigate.db (0 = off), oldest detail first
  retention_dry_run: boolean;      // the night job logs what it would trim, deletes nothing
  keep_unknown_plates: "7_days" | "always";
  expressions?: boolean;           // P144-s27b: expression and gesture in the frame run (house switch); absent on an old API
  questions?: { chat: boolean; wrong_rules?: boolean };   // J11 chat questions; J20 confirmed-wrong rules (absent on an old API)
  episode_weather?: boolean;       // P144-s14: weather as code context (from Settings → Weather); absent on an old API
  face_votes?: FaceVotesSettings;  // P144-s19: extra face recognition; absent on an old API
  face_sensors?: FaceSensorsSettings;  // P144-s29b: how much each face sensor counts
  conclude: {
    idle_gate_s: number;
    urgent_classes: string[];
    urgent_when_away_person: boolean;
    urgent_labels: string[];
    max_attempts: number;
    poll_s: number;
    analyse_per_10min?: number;  // P144-s24: «Analyser» starts per 10 min (absent on an old API)
    stuck_after_min?: number;  // N40: an episode open / without a conclusion this long «henger»
  };
};

// ── P144-s19: extra face recognition (the second face sensor, Double Take first) ──
export type FaceVotesSettings = {
  enabled: boolean;
  provider: "double_take";
  topic_prefix: string;
  rest_url: string;
  min_confidence: number;
};
export type FaceVotesDetector = { detector: string; label: string; configured: boolean; last_seen_ts: number | null };
// P144-s29b: face sensors as words — «When this sensor alone says a name, it is …»
export type SensorTrust = "very_likely" | "likely" | "uncertain" | "unlikely" | "off";
export type FaceSensorSetting = { mode: "auto" | "admin"; trust: SensorTrust; label: string; auto_learning?: boolean };
export type FaceSensorsSettings = { auto_learning?: boolean; frigate: FaceSensorSetting; detectors: Record<string, FaceSensorSetting> };
export type FaceSensorRow = {
  sensor: string; provider: string | null; label: string; mode: "auto" | "admin"; trust: SensorTrust; word: string;
  learned: number | null;  // P149: human «Var det deg?» answers behind this sensor (shadow: shown, not acted on)
  learned_word?: string | null; learned_goal?: number | null;
  acting?: boolean; auto_learning?: boolean;  // J2b: Auto uses the learned word now / this sensor's learning switch
  last_seen_ts: number | null; counts: { votes: number; agree: number; disagree: number };
};
// P144 #9 / P149: the logged-in person's own «Var det deg?» questions (camera + time only) and their answers
// J9: one list — «camera» (event, camera + time) and «theme» (a night-meeting theme set aside as a question)
export type CameraQuestion = { kind?: "camera" | "theme"; id?: string; event_id?: string; camera?: string; ts?: number;
  text?: string };
export type CameraAnswer = "yes" | "no" | "private" | "unsure" | "skip";
export async function apiGetMyCameraQuestions(): Promise<{ questions: CameraQuestion[] }> {
  const { data } = await api.get("/api/frigate_user/questions/mine");
  return data;
}
export async function apiAnswerCameraQuestion(event_id: string, answer: CameraAnswer,
  kind: "camera" | "theme" = "camera"): Promise<{ ok: boolean }> {
  const { data } = await api.post("/api/frigate_user/questions/answer", { event_id, answer, kind });
  return data;
}
// P144 R139 / J12: «Er dette det samme?» — admin's house question about one thing on two cameras
export type ThingQuestion = { kind: "thing"; id: string; events: [string, string]; cameras: [string, string];
  label: string; belief: number };
export async function apiGetThingQuestions(): Promise<{ questions: ThingQuestion[] }> {
  const { data } = await api.get("/api/frigate_user/questions/things");
  return data;
}
export async function apiAnswerThingQuestion(pair: string, answer: "yes" | "no" | "skip"): Promise<{ ok: boolean }> {
  const { data } = await api.post("/api/frigate_user/questions/things/answer", { pair, answer });
  return data;
}
// Run-queue J18: «Hva er her?» — a spot where persons appear/disappear mid-view (admin's house question)
export type PlaceQuestion = { kind: "place"; id: string; camera: string; camera_name?: string; cell: string;
  box: [number, number, number, number]; starts: number; ends: number; days: number };
export async function apiGetPlaceQuestions(): Promise<{ questions: PlaceQuestion[] }> {
  const { data } = await api.get("/api/frigate_user/questions/places");
  return data;
}
export async function apiAnswerPlaceQuestion(id: string, answer: "named" | "not_a_place" | "skip",
  name?: string): Promise<{ ok: boolean }> {
  const { data } = await api.post("/api/frigate_user/questions/places/answer", { id, answer, name: name ?? null });
  return data;
}
// P150 6b: vehicles — the same path as faces: a pseudonymous id (vehicle-<hex>), never a plate text
export type VehicleRow = {
  vehicle_id: string; reads: number; days_seen: number; first_seen: number; last_seen: number;
  newest_event_id: string; class: string; name: string; skipped: boolean;
};
export type VehicleList = { vehicles: VehicleRow[]; known: { name: string; reads: number }[]; classes: string[];
  question_days: number };
export async function apiGetVehicles(): Promise<VehicleList> {
  const { data } = await api.get("/api/frigate_user/vehicles");
  return data;
}
export async function apiPutVehicle(vehicle_id: string, cls: string, name: string): Promise<VehicleRow> {
  const { data } = await api.put(`/api/frigate_user/vehicles/${encodeURIComponent(vehicle_id)}`, { class: cls, name });
  return data;
}
export async function apiGetVehicleQuestions(): Promise<{ questions: VehicleRow[] }> {
  const { data } = await api.get("/api/frigate_user/questions/vehicles");
  return data;
}
export async function apiSkipVehicleQuestion(vehicle_id: string): Promise<{ ok: boolean }> {
  const { data } = await api.post("/api/frigate_user/questions/vehicles/skip", { vehicle_id });
  return data;
}
export async function apiPutVehicleSettings(question_days: number): Promise<{ question_days: number }> {
  const { data } = await api.put("/api/frigate_user/vehicles_settings", { question_days });
  return data;
}
// J18b: read-only history for the three question cards (same access as their question lists)
export type CameraHistoryItem = { event_id: string; camera: string; ts: number | null; answer: CameraAnswer;
  answered_ts: number | null };
export async function apiGetMyCameraHistory(): Promise<{ answered: CameraHistoryItem[] }> {
  const { data } = await api.get("/api/frigate_user/questions/mine/history");
  return data;
}
export type ThingHistoryItem = { id: string; answer: "yes" | "no" | "skip"; label: string; cameras: [string, string];
  events: [string | null, string | null]; ts: string | null };
export async function apiGetThingHistory(): Promise<{ answered: ThingHistoryItem[] }> {
  const { data } = await api.get("/api/frigate_user/questions/things/answered");
  return data;
}
export type PlaceHistoryItem = { id: string; camera: string; camera_name?: string; cell: string;
  box: [number, number, number, number]; status: "named" | "not_a_place" | "skip"; name: string | null; ts: number | null };
export async function apiGetPlaceHistory(): Promise<{ answered: PlaceHistoryItem[] }> {
  const { data } = await api.get("/api/frigate_user/questions/places/answered");
  return data;
}
// J20: «Hva er dette?» — admin names a still thing, or says Frigate misreads it (narrow confirmed-wrong rule)
export type WhatQuestion = { kind: "what"; id: string; camera: string; camera_name?: string; label: string;
  label_name?: string; cell: string; box: [number, number, number, number]; misread: boolean; days: number; seen: number };
export type WhatAnswer = "named" | "not_label" | "wrong" | "skip";
export type WhatHistoryItem = { id: string; camera: string; camera_name?: string; label: string; label_name?: string;
  cell: string; box: [number, number, number, number]; status: WhatAnswer; name: string | null; ts: number | null };
export async function apiGetWhatQuestions(): Promise<{ questions: WhatQuestion[] }> {
  const { data } = await api.get("/api/frigate_user/questions/what");
  return data;
}
export async function apiAnswerWhatQuestion(id: string, answer: WhatAnswer, name?: string): Promise<{ ok: boolean }> {
  const { data } = await api.post("/api/frigate_user/questions/what/answer", { id, answer, name: name ?? null });
  return data;
}
export async function apiGetWhatHistory(): Promise<{ answered: WhatHistoryItem[] }> {
  const { data } = await api.get("/api/frigate_user/questions/what/answered");
  return data;
}
// P144 6e: «Siste deteksjoner» = the image model's own single-image reads (L3 per event, module 4 per frame)
export type FrigateModelRead = { kind: "l3" | "frame"; ts: number; camera: string; camera_name?: string; label: string;
  event_id?: string; episode_id: number | null; frame?: number; index?: number; words: Record<string, unknown>;
  raw?: string };
export async function apiGetFrigateModelReads(before?: number): Promise<{ reads: FrigateModelRead[]; has_more: boolean }> {
  const { data } = await api.get("/api/frigate_user/reads", { params: before != null ? { before } : {} });
  return data;
}
// J21 5e: the module panel «Moduler» (only m1/m2 switch anything today)
export type FrigateModuleRow = { n: number; setting: "always" | "auto" | "on" | "off"; on: boolean; why: string;
  needs: string[]; needs_missing: string[]; switchable: boolean; replaced?: boolean };
export async function apiGetFrigateModules(): Promise<{ modules: FrigateModuleRow[]; warnings: string[] }> {
  const { data } = await api.get("/api/frigate_user/modules");
  return data;
}
// J22: admin may name any household member with tracking on («det var <member>»)
export type WhoForOthers = { questions: { event_id: string; camera: string; ts: number }[];
  members: { username: string; name: string }[] };
export async function apiGetWhoForOthers(): Promise<WhoForOthers> {
  const { data } = await api.get("/api/frigate_user/questions/who");
  return data;
}
export async function apiAnswerWhoForOther(event_id: string, member: string): Promise<{ ok: boolean }> {
  const { data } = await api.post("/api/frigate_user/questions/who/answer", { event_id, member });
  return data;
}
export async function apiGetFaceSensors(): Promise<{ sensors: FaceSensorRow[]; count_days: number; auto_learning?: boolean }> {
  const { data } = await api.get("/api/frigate_user/face_sensors");
  return data;
}
export type FaceVotesStatus = {
  enabled: boolean; configured: boolean; provider: string; topic_prefix: string;
  available: "online" | "offline" | "unknown"; available_ts: number | null;
  votes_seen: number; last_vote_ts: number | null; buffered_events: number;
  detectors?: FaceVotesDetector[];  // P144-s29: seen from the provider + configured
  rest_read?: { ok: boolean | null; error: string; last_ts: number | null };
};

export async function apiGetFaceVotesStatus(): Promise<FaceVotesStatus> {
  const { data } = await api.get("/api/frigate_user/face_votes/status");
  return data;
}

export async function apiTestFaceVotes(rest_url?: string): Promise<{ ok: boolean; status: number | null; error: string }> {
  const { data } = await api.post("/api/frigate_user/face_votes/test", { rest_url: rest_url ?? null });
  return data;
}

export async function apiGetFrigateUserPipe(): Promise<FrigateUserPipeSettings> {
  const { data } = await api.get("/api/frigate_user/pipe");
  return data;
}

export async function apiPutFrigateUserPipe(
  changes: Partial<Omit<FrigateUserPipeSettings, "conclude" | "face_votes" | "questions">> & {
    conclude?: Partial<FrigateUserPipeSettings["conclude"]>; face_votes?: Partial<FaceVotesSettings>;
    face_sensors?: Partial<FaceSensorsSettings>; questions?: { chat?: boolean; wrong_rules?: boolean };
    modules?: Partial<Record<"m1" | "m2" | "m3" | "m4" | "m5" | "m6", "auto" | "on" | "off">>;
  },
): Promise<FrigateUserPipeSettings> {
  const { data } = await api.put("/api/frigate_user/pipe", changes);
  return data;
}

// ── P144-s11: the Frigate user's episodes (read only) ──
export type EpisodeClaimStatus = "confirmed" | "unverified" | "rejected" | "none";
export type EpisodeUnusual = { value: boolean; reason: string; baseline_days?: number };
export type EpisodeItem = {
  id: number;
  status: "open" | "closed";
  start_ts: number;
  end_ts: number;
  date: string;   // household timezone, YYYY-MM-DD
  start: string;  // HH:MM:SS
  end: string;
  duration_s: number;
  cameras: { camera: string; name: string }[];  // in order of activity
  labels: string[];
  event_count: number;
  persons: { name: string; claim_status: EpisodeClaimStatus; kind: string }[];
  unnamed_persons: number;
  conclusion_status: string;  // done | failed | pending | open
  by: "code" | "model" | null;
  summary: string;
  unusual: EpisodeUnusual | null;
  unusual_suppressed: EpisodeUnusual | null;
  urgent: boolean;
  frame_count: number;
  false_detection?: boolean;  // P144-s14: code concluded «likely false detection» (label gate)
  // N40: «henger» — only what code knows; null/absent = not stuck
  stuck?: EpisodeStuck | null;
};
export type EpisodeStuck = {
  kind: "not_closed" | "queued" | "waiting" | "failed" | "running";
  minutes: number;
  attempts?: number;
  next_try_ts?: number | null;
  started_ts?: number | null;
};
// P144-s14: the Frigate store's last write problem (Integrations → Frigate shows a red line while ok is false)
export type FrigateStoreStatus = {
  ok: boolean; op?: string | null; cause?: string | null; error?: string | null; since?: number | null; failures?: number;
};
export type EpisodeSensors = {
  provider: string | null; provider_label: string; relation: "agree" | "disagree" | "split" | "double_take_only";
  frigate: string | null; frigate_kind: string | null; frigate_rejected: boolean;
  double_take: string | null; double_take_kind: string | null; confidence: number | null;
  lean: string | null; review: boolean;
  // P144-s29: one vote per detector (the user's label); name null = none / opted out (kind says which)
  detectors?: { detector: string; label: string; name: string | null; kind: string | null; split: boolean;
    discarded: number; weight: number | null }[];
};
export type EpisodeCheckLayer = { layer: string; verdict: string; strong: boolean; reason: string };
// P144-s27: chapters and change positions from code; lines hold the image model's own words
export type EpisodeChapter = {
  n: number; kind: "active" | "calm" | "empty"; signal: string; start: string; end: string; cameras: string[];
  frames: number[]; lines: string[];
};
export type EpisodeStory = { chapters: EpisodeChapter[]; lines: string[];
  changes: { camera: string; where: string; words: string; fact?: string }[];  // J15: «fact» = what stood there (code)
  after_frames?: { camera: string; n: number }[] };  // J15: the stored after frame per changed camera (GUI only)

export type EpisodeDetail = EpisodeItem & {
  story?: EpisodeStory;
  route: string;
  scene: {
    status?: string; reason?: string; scene?: string; persons?: number | null; vehicles?: string; at_door?: string;
    people?: { age: string; gender: string; clothing: string; carried: string }[];
    animals?: { kind: string; count: number }[];  // P144-s14
    scene_thin?: boolean;  // P144-s14: the P142 detector frigate_thin_scene fired
  } | null;
  conclusion: {
    status?: string; summary?: string; reason?: string;
    notify_suggestion?: { who?: string; why?: string } | null;
    raise_at_dev_meeting?: { topic: string; text: string; count?: number | null; raised?: boolean | null } | null;
    confidence?: { level: string; reasons?: string[] };
    false_detection?: { frigate_labels: string[]; seen: string; weather?: Record<string, unknown> | null } | null;
    images_checked?: boolean;  // P144-s24: false = no model looked at a frame («Ikke sjekket med bilder»)
    history?: { replaced_ts: number; conclusion: { summary?: string; status?: string; by?: string }; scene: { status?: string } }[];
  } | null;
  frames: { index: number; file?: number; camera: string; camera_name: string; ts: number; clock: string; stored: boolean }[];
  history_count?: number;  // P144-s24: earlier conclusions kept after «Analyser på nytt»
  events: {
    event_id: string; ts: number; clock: string; camera: string; camera_name: string; label: string;
    person: string | null; person_kind: string; claim_status: EpisodeClaimStatus;
    certainty?: "very_likely" | "likely" | "uncertain" | "unlikely" | null;  // P144-s19
    check: {
      decision: string | null; layers: EpisodeCheckLayer[]; doubt: { reason?: string } | null; storage_gate: string | null;
      sensors?: EpisodeSensors | null;  // P144-s19: both face sensors
    };
  }[];
};

export async function apiGetFrigateEpisodes(before?: number, limit = 50): Promise<{ episodes: EpisodeItem[]; has_more: boolean; enabled: boolean; store?: FrigateStoreStatus }> {
  const { data } = await api.get("/api/frigate_user/episodes", { params: before == null ? { limit } : { limit, before } });
  return data;
}

export async function apiGetFrigateEpisode(id: number): Promise<EpisodeDetail> {
  const { data } = await api.get(`/api/frigate_user/episodes/${id}`);
  return data;
}

// P144-s24: «Analyser» / «Analyser på nytt» — one run at a time (admin); the card polls the job.
export type EpisodeAnalysisJob = {
  state: "idle" | "running" | "done" | "failed";
  episode_id?: number | null; started_ts?: number | null; finished_ts?: number | null;
  result?: { ok: boolean; reason?: string; status?: string; by?: string; ltm?: string } | null;
};

export async function apiAnalyseFrigateEpisode(id: number): Promise<EpisodeAnalysisJob> {
  const { data } = await api.post(`/api/frigate_user/episodes/${id}/analyse`);
  return data;
}

export async function apiGetEpisodeAnalysis(): Promise<EpisodeAnalysisJob> {
  const { data } = await api.get("/api/frigate_user/episode_analysis");
  return data;
}

export type EpisodeFramesUsage = { full_mb: number; keep_mb: number; episodes: number; max_mb: number; keep_cap_mb: number;
  db_mb?: number };  // P150: used part of frigate.db (absent from an API not yet restarted)

export async function apiGetEpisodeFramesUsage(): Promise<EpisodeFramesUsage> {
  const { data } = await api.get("/api/frigate_user/episode_frames/usage");
  return data;
}

/** One episode frame as an object URL (header auth — never a token in the URL). Caller revokes it.
 *  Rejects with status 404 when Frigate's recordings no longer have it. */
export async function apiGetFrigateEpisodeAfterFrame(id: number, n: number): Promise<string> {
  const { data } = await api.get(`/api/frigate_user/episodes/${id}/after/${n}`, { responseType: "blob" });
  return URL.createObjectURL(data);
}
export async function apiGetFrigateEpisodeFrame(id: number, index: number): Promise<string> {
  const { data } = await api.get(`/api/frigate_user/episodes/${id}/frames/${index}`, { responseType: "blob" });
  return URL.createObjectURL(data);
}
