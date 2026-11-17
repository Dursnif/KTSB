// P138 — node dialog sections: where the reply goes (audio/image out), starting a node from Home Assistant
// (learnt trigger + webhook fallback), the KTSB Direct microphone, and the voice pipeline settings card.
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Loader2, Radio, Copy, CheckCircle2, XCircle, RefreshCw } from "lucide-react";
import {
  apiGetDirectAudio,
  apiGetVoicePipeline,
  apiNodeTriggersLearn,
  apiNodeTriggersStatus,
  apiNodeWebhook,
  apiPutDirectAudio,
  apiPutVoicePipeline,
  type DirectAudioView,
  type NodeConfig,
  type NodeTrigger,
  type NodeTriggersStatus,
  type VoicePipelineSettings,
} from "@/services/api";
import { nodeLabel } from "./nodeLabel";

const LOCAL = "local";

function asList(v: string[] | "auto" | undefined): string[] {
  return Array.isArray(v) ? v : [];
}

// ── Audio out / image out ─────────────────────────────────────────────────────

export function OutputPicker({
  kind,
  nodeKey,
  value,
  allNodes,
  onChange,
}: {
  kind: "audio" | "image";
  nodeKey: string;
  value: string[] | "auto" | undefined;
  allNodes: Record<string, NodeConfig>;
  onChange: (v: string[] | "auto") => void;
}) {
  const { t } = useTranslation();
  const chosen = asList(value);
  // Auto = no list at all. An empty list is «choose yourself, nothing picked yet» — reading it as auto made the
  // switch snap back on as soon as it was turned off. Saving an empty list stores auto (Nodes.tsx handleSave).
  const auto = !Array.isArray(value);
  const candidates = Object.entries(allNodes).filter(([id, n]) =>
    n.enabled !== false && (kind === "audio" ? (n.has_audio ?? true) : !!n.has_display) && (kind === "image" || id !== nodeKey),
  );
  const toggle = (id: string) => {
    const next = chosen.includes(id) ? chosen.filter(x => x !== id) : [...chosen, id];
    onChange(next);
  };
  return (
    <div>
      <Label className="text-xs text-muted-foreground mb-1 block">
        {kind === "audio" ? t("nodes.routing.audio_out") : t("nodes.routing.image_out")}
      </Label>
      <div className="flex items-center gap-2 mb-1">
        <Switch checked={auto} onCheckedChange={v => onChange(v ? "auto" : [])} />
        <span className="text-sm">{t("nodes.routing.auto")}</span>
      </div>
      <p className="text-xs text-muted-foreground mb-1">
        {kind === "audio" ? t("nodes.routing.audio_auto_hint") : t("nodes.routing.image_auto_hint")}
      </p>
      {!auto && (
        <div className="space-y-1 pl-1">
          {kind === "audio" && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={chosen.includes(nodeKey)} onChange={() => toggle(nodeKey)} />
              {t("nodes.routing.this_node")}
            </label>
          )}
          {/* «local» from before KTSB Direct existed: shown only while chosen, so it can be unticked */}
          {kind === "audio" && chosen.includes(LOCAL) && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked onChange={() => toggle(LOCAL)} />
              {t("nodes.routing.local")}
            </label>
          )}
          {candidates.map(([id, n]) => (
            <label key={id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={chosen.includes(id)} onChange={() => toggle(id)} />
              {nodeLabel(id, n, t)}
              {n.room ? <span className="text-xs text-muted-foreground">· {n.room}</span> : null}
            </label>
          ))}
          {candidates.length === 0 && <p className="text-xs text-muted-foreground">{t("nodes.routing.none")}</p>}
        </div>
      )}
    </div>
  );
}

// ── Start from Home Assistant ─────────────────────────────────────────────────

function describeTrigger(tr: NodeTrigger): string {
  if (tr.kind === "entity") return `${tr.label || tr.entity_id}${tr.to ? ` → ${tr.to}` : ""}`;
  const m = Object.entries(tr.match || {}).map(([k, v]) => `${k}=${v}`).join(", ");
  return `${tr.label || tr.event_type}${m ? ` (${m})` : ""}`;
}

export function TriggerSection({
  nodeKey,
  trigger,
  webhookToken,
  onChange,
  onWebhook,
}: {
  nodeKey: string;
  trigger: NodeTrigger | null | undefined;
  webhookToken: string | undefined;
  onChange: (tr: NodeTrigger | null) => void;
  onWebhook: (token: string) => void;
}) {
  const { t } = useTranslation();
  const [learning, setLearning] = useState(false);
  const [msg, setMsg] = useState("");
  const [status, setStatus] = useState<NodeTriggersStatus | null>(null);
  const [hook, setHook] = useState<{ url: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    apiNodeTriggersStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  const learn = async () => {
    setLearning(true);
    setMsg(t("nodes.trigger.learn_waiting"));
    try {
      const r = await apiNodeTriggersLearn();
      if (r.ok) {
        onChange(r.trigger);
        setMsg(r.admin ? t("nodes.trigger.learn_ok") : t("nodes.trigger.learn_ok_no_admin"));
      } else {
        setMsg(
          r.reason === "timeout"
            ? r.admin === false
              ? t("nodes.trigger.learn_timeout_no_admin")
              : t("nodes.trigger.learn_timeout")
            : r.reason === "not_configured"
              ? t("nodes.trigger.not_configured")
              : t("nodes.trigger.learn_error", { error: r.error || r.reason }),
        );
      }
    } catch {
      setMsg(t("nodes.trigger.learn_error", { error: "network" }));
    }
    setLearning(false);
  };

  const makeHook = async (action: "new" | "remove") => {
    if (!nodeKey) return;
    try {
      const r = await apiNodeWebhook(nodeKey, action);
      onWebhook(r.token);
      setHook(r.token ? { url: r.url, token: r.token } : null);
    } catch {
      setMsg(t("common.error"));
    }
  };

  const nodeStatus = status?.nodes?.[nodeKey];
  const snippet = hook
    ? [
        "rest_command:",
        `  kare_${nodeKey}:`,
        `    url: "${hook.url}"`,
        "    method: POST",
        "    headers:",
        `      X-Kare-Token: "${hook.token}"`,
        "",
        "automation:",
        `  - alias: "Kåre – ${nodeKey}"`,
        "    triggers:",
        `      - trigger: device  # ${t("nodes.trigger.snippet_pick_button")}`,
        "    actions:",
        `      - action: rest_command.kare_${nodeKey}`,
      ].join("\n")
    : "";

  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <Label className="text-xs text-muted-foreground block">{t("nodes.trigger.title")}</Label>
      <p className="text-xs text-muted-foreground">{t("nodes.trigger.hint")}</p>

      {trigger ? (
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm">
            {trigger.kind === "entity" ? t("nodes.trigger.kind_entity") : t("nodes.trigger.kind_event")}: {describeTrigger(trigger)}
          </span>
          <Button variant="ghost" size="sm" onClick={() => onChange(null)}>{t("nodes.trigger.remove")}</Button>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">{t("nodes.trigger.none")}</p>
      )}

      {trigger?.kind === "entity" && (
        <div className="grid grid-cols-2 gap-2">
          <Input value={trigger.entity_id} onChange={e => onChange({ ...trigger, entity_id: e.target.value })} />
          <Input value={trigger.to ?? ""} placeholder={t("nodes.trigger.to_ph")} onChange={e => onChange({ ...trigger, to: e.target.value })} />
        </div>
      )}

      {nodeStatus && !nodeStatus.ok && (
        <p className="text-xs" style={{ color: "#ff9f43" }}>
          {nodeStatus.reason === "admin_required" ? t("nodes.trigger.admin_required") : nodeStatus.reason}
        </p>
      )}
      {nodeStatus?.ok && trigger && (
        <p className="text-xs flex items-center gap-1 text-green-500"><CheckCircle2 className="h-3 w-3" /> {t("nodes.trigger.listening")}</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={learn} disabled={learning}>
          {learning ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Radio className="h-3 w-3 mr-1" />}
          {t("nodes.trigger.learn")}
        </Button>
        {msg && <span className="text-xs text-muted-foreground">{msg}</span>}
      </div>

      <div className="pt-2 space-y-1">
        <Label className="text-xs text-muted-foreground block">{t("nodes.trigger.webhook_title")}</Label>
        <p className="text-xs text-muted-foreground">{t("nodes.trigger.webhook_hint")}</p>
        {!nodeKey ? (
          <p className="text-xs text-muted-foreground">{t("nodes.trigger.webhook_save_first")}</p>
        ) : (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => makeHook("new")}>
              {webhookToken ? t("nodes.trigger.webhook_renew") : t("nodes.trigger.webhook_make")}
            </Button>
            {webhookToken && (
              <Button size="sm" variant="ghost" onClick={() => makeHook("remove")}>
                <XCircle className="h-3 w-3 mr-1" /> {t("nodes.trigger.webhook_remove")}
              </Button>
            )}
          </div>
        )}
        {snippet && (
          <div>
            <pre className="text-xs p-2 rounded bg-black/40 overflow-x-auto whitespace-pre">{snippet}</pre>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => { navigator.clipboard?.writeText(snippet); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
            >
              <Copy className="h-3 w-3 mr-1" /> {copied ? t("common.saved") : t("nodes.trigger.copy")}
            </Button>
            <p className="text-xs text-muted-foreground">{t("nodes.trigger.webhook_once")}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── KTSB Direct microphone ────────────────────────────────────────────────────

const MIC_DEFAULT = "__system_default__";

export function DirectMicPicker() {
  const { t } = useTranslation();
  const [view, setView] = useState<DirectAudioView | null>(null);
  const [loading, setLoading] = useState(false);
  const [state, setState] = useState<"idle" | "saved" | "error">("idle");

  const load = async () => {
    setLoading(true);
    try { setView(await apiGetDirectAudio()); } catch { setView(null); setState("error"); }
    setLoading(false);
  };
  useEffect(() => {
    apiGetDirectAudio().then(setView).catch(() => { setView(null); setState("error"); });
  }, []);

  const current = view?.mic_device ?? null;
  // A saved index (older installs) is shown as the device with that index.
  // A name whose ALSA card number «(hw:N,M)» changed is the same mic — the bridge matches it the same way.
  const bare = (n: string) => n.replace(/\s*\(hw:\d+,\d+\)\s*$/, "");
  const match = view?.inputs.find(d => (typeof current === "number" ? d.index === current : d.name === current))
    ?? (typeof current === "string" && current ? view?.inputs.filter(d => bare(d.name) === bare(current)).at(0) : undefined);
  const selected = current === null || current === "" ? MIC_DEFAULT : match ? match.name : String(current);
  const save = async (v: string) => {
    try {
      await apiPutDirectAudio(v === MIC_DEFAULT ? null : v);
      setView(p => (p ? { ...p, mic_device: v === MIC_DEFAULT ? null : v } : p));
      setState("saved");
      setTimeout(() => setState("idle"), 3000);
    } catch {
      setState("error");
    }
  };

  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <Label className="text-xs text-muted-foreground block">{t("nodes.direct.mic")}</Label>
      {view && view.error && view.inputs.length === 0 ? (
        <p className="text-xs" style={{ color: "#ff9f43" }}>{t(`nodes.direct.err_${view.error}`)}</p>
      ) : (
        <select
          value={selected}
          onChange={e => save(e.target.value)}
          disabled={!view}
          className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          style={{ background: "#111", color: "#eee" }}
        >
          <option value={MIC_DEFAULT}>{t("nodes.direct.mic_default")}</option>
          {view?.inputs.map(d => (
            <option key={d.index} value={d.name}>{d.name} · {Math.round(d.rate / 100) / 10} kHz</option>
          ))}
          {view && current !== null && current !== "" && !match && <option value={String(current)}>{String(current)}</option>}
        </select>
      )}
      {view && current !== null && current !== "" && !match && view.inputs.length > 0 && (
        <p className="text-xs" style={{ color: "#ff9f43" }}>{t("nodes.direct.mic_missing", { name: String(current) })}</p>
      )}
      <div className="flex items-center gap-2">
        <Button size="sm" variant="ghost" onClick={load} disabled={loading}>
          {loading ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <RefreshCw className="h-3 w-3 mr-1" />}
          {t("nodes.direct.rescan")}
        </Button>
        {state === "saved" && <span className="text-xs text-green-500">{t("nodes.direct.mic_saved")}</span>}
        {state === "error" && <span className="text-xs text-destructive">{t("common.error")}</span>}
      </div>
      <p className="text-xs text-muted-foreground">{t("nodes.direct.speaker_hint")}</p>
    </div>
  );
}

// ── Voice pipeline settings card ──────────────────────────────────────────────

export function VoicePipelineCard() {
  const { t } = useTranslation();
  const [s, setS] = useState<VoicePipelineSettings | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");

  useEffect(() => {
    apiGetVoicePipeline().then(setS).catch(() => setS(null));
  }, []);

  if (!s) return null;
  const set = <K extends keyof VoicePipelineSettings>(k: K, v: VoicePipelineSettings[K]) => setS({ ...s, [k]: v });
  const save = async () => {
    setState("saving");
    try {
      setS(await apiPutVoicePipeline(s));
      setState("saved");
      setTimeout(() => setState("idle"), 3000);
    } catch {
      setState("error");
      setTimeout(() => setState("idle"), 4000);
    }
  };

  return (
    <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px", marginTop: 16 }}>
      <div style={{ color: "#ddd", fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{t("nodes.voice.title")}</div>
      <p style={{ color: "#666", fontSize: 13, marginBottom: 12 }}>{t("nodes.voice.description")}</p>
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
        <div>
          <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.voice.end_of_speech")}</Label>
          <select
            value={s.end_of_speech}
            onChange={e => set("end_of_speech", e.target.value as VoicePipelineSettings["end_of_speech"])}
            className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
            style={{ background: "#111", color: "#eee" }}
          >
            <option value="auto">{t("nodes.voice.end_auto")}</option>
            <option value="fixed">{t("nodes.voice.end_fixed")}</option>
          </select>
          <p className="text-xs text-muted-foreground mt-1">{t(s.end_of_speech === "auto" ? "nodes.voice.end_auto_hint" : "nodes.voice.end_fixed_hint")}</p>
        </div>
        <div>
          <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.voice.silence_seconds")}</Label>
          <Input type="number" step="0.1" min={0.3} max={5} value={s.silence_seconds} onChange={e => set("silence_seconds", Number(e.target.value))} />
          <p className="text-xs text-muted-foreground mt-1">{t("nodes.voice.silence_seconds_hint")}</p>
        </div>
        {s.end_of_speech === "fixed" && (
        <div>
          <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.voice.silence_amplitude")}</Label>
          <Input type="number" step="0.005" min={0.001} max={0.5} value={s.silence_amplitude} onChange={e => set("silence_amplitude", Number(e.target.value))} />
          <p className="text-xs text-muted-foreground mt-1">{t("nodes.voice.silence_amplitude_hint")}</p>
        </div>
        )}
        <div>
          <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.voice.max_record_seconds")}</Label>
          <Input type="number" step="1" min={3} max={60} value={s.max_record_seconds} onChange={e => set("max_record_seconds", Number(e.target.value))} />
        </div>
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Switch checked={s.duck_on_listen} onCheckedChange={v => set("duck_on_listen", v)} />
            <span className="text-sm">{t("nodes.voice.duck_on_listen")}</span>
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={s.duck_mpd} onCheckedChange={v => set("duck_mpd", v)} />
            <span className="text-sm">{t("nodes.voice.duck_mpd")}</span>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">
              {t("nodes.voice.duck_volume", { pct: Math.round(s.duck_volume * 100) })}
            </Label>
            <input type="range" min={0} max={100} value={Math.round(s.duck_volume * 100)} onChange={e => set("duck_volume", Number(e.target.value) / 100)} className="w-full" />
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3 mt-3">
        <Button size="sm" onClick={save} disabled={state === "saving"}>{t("common.save")}</Button>
        {state === "saved" && <span className="text-sm text-green-500">{t("common.saved")}</span>}
        {state === "error" && <span className="text-sm text-destructive">{t("common.error")}</span>}
      </div>
    </div>
  );
}
