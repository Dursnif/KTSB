import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Loader2, CheckCircle2, XCircle, Pencil, Plus, Trash2, Volume2, Monitor, Mic, Wifi, WifiOff } from "lucide-react";
import { apiGetNodes, apiGetNodesStatus, apiGetRooms, apiNodePair, apiNodeScreen, apiProbeNodeChannels, apiPutNodes, apiPutRooms, apiTestDisplayNode, type NodeConfig, type NodeStatus, type RoomsView, type ScreenStatus } from "@/services/api";
import { withoutKey } from "@/lib/collections";
import { DirectMicPicker, OutputPicker, TriggerSection, VoicePipelineCard } from "./nodes/NodeVoiceRouting";
import { DIRECT_TYPE } from "./nodes/nodeLabel";

type SaveState = "idle" | "saving" | "saved" | "error";

function useSaveState() {
  const [state, setState] = useState<SaveState>("idle");
  const saved = () => { setState("saved"); setTimeout(() => setState("idle"), 3000); };
  const error = () => { setState("error"); setTimeout(() => setState("idle"), 4000); };
  return { state, saving: () => setState("saving"), saved, error };
}

function SaveFeedback({ state }: { state: SaveState }) {
  const { t } = useTranslation();
  if (state === "saving") return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />;
  if (state === "saved")  return <span className="flex items-center gap-1 text-sm text-green-500"><CheckCircle2 className="h-4 w-4" /> {t("common.saved")}</span>;
  if (state === "error")  return <span className="flex items-center gap-1 text-sm text-destructive"><XCircle className="h-4 w-4" /> {t("common.error")}</span>;
  return null;
}

const TYPE_META: Record<string, { color: string; label: string; defaultAudio: boolean; defaultDisplay: boolean }> = {
  direct:          { color: "#14b8a6", label: "KTSB Direct", defaultAudio: true,  defaultDisplay: false },
  ha_media_player: { color: "#6c8ebf", label: "HA",          defaultAudio: true,  defaultDisplay: false },
  esp32:           { color: "#82b366", label: "ESP32",       defaultAudio: true,  defaultDisplay: false },
  wyoming:         { color: "#d6a84e", label: "Wyoming",     defaultAudio: true,  defaultDisplay: false },
  chromecast:      { color: "#e8453c", label: "Chromecast",  defaultAudio: true,  defaultDisplay: true  },
  snapcast:        { color: "#5d9cec", label: "Snapcast",    defaultAudio: true,  defaultDisplay: false },
  airplay:         { color: "#c0c0c0", label: "AirPlay",     defaultAudio: true,  defaultDisplay: false },
  dlna:            { color: "#9b59b6", label: "DLNA",        defaultAudio: true,  defaultDisplay: false },
  apple_tv:        { color: "#a8b8d0", label: "Apple TV",    defaultAudio: true,  defaultDisplay: true  },
  samsung_tv:      { color: "#1428a0", label: "Samsung",     defaultAudio: true,  defaultDisplay: true  },
  android_tv:      { color: "#3ddc84", label: "Android TV",  defaultAudio: true,  defaultDisplay: true  },
  google_tv:       { color: "#4285f4", label: "Google TV",   defaultAudio: true,  defaultDisplay: true  },
  fire_tv:         { color: "#ff9900", label: "Fire TV",     defaultAudio: true,  defaultDisplay: true  },
  lg_tv:           { color: "#a50034", label: "LG TV",       defaultAudio: true,  defaultDisplay: true  },
  projector:       { color: "#8d6e63", label: "Projektor",   defaultAudio: false, defaultDisplay: true  },
};
const DEFAULT_TYPE_META = { color: "#888888", label: "Custom", defaultAudio: true, defaultDisplay: false };

const DEFAULT_PORTS: Partial<Record<string, number>> = {
  esp32: 6053,
  wyoming: 10300,
  snapcast: 4953,
  android_tv: 5555,
  google_tv: 5555,
  fire_tv: 5555,
};

const EMPTY_NODE: NodeConfig = {
  room: "",
  description: "",
  type: "ha_media_player",
  entity_id: "",
  host: "",
  api_port: undefined,
  mac: undefined,
  adb_port: undefined,
  tvoverlay_port: undefined,
  mic_enabled: false,
  has_audio: true,
  has_display: false,
  default_user: "",
  trust: "indoor",
  enabled: true,
};

type NodeSection = "audio" | "multi" | "display";

function getSection(node: NodeConfig): NodeSection {
  const meta = TYPE_META[node.type] ?? DEFAULT_TYPE_META;
  const audio = node.has_audio ?? meta.defaultAudio;
  const display = node.has_display ?? meta.defaultDisplay;
  if (audio && display) return "multi";
  if (display) return "display";
  return "audio";
}

const NEW_PLACE = "__new_place__";

// P139 phase 2: channels in the order the dialog lists them; `driver` = KTSB can use it today.
const CHANNELS: { name: string; driver: boolean }[] = [
  { name: "tvoverlay", driver: true },
  { name: "cast", driver: true },
  { name: "dlna", driver: true },
  { name: "ha", driver: true },
  { name: "atv_remote", driver: true },
  { name: "samsung_ws", driver: true },
  { name: "lg_ssap", driver: false },
  { name: "airplay", driver: false },
  { name: "adb", driver: false },
];
const SCREEN_TYPES = ["chromecast", "apple_tv", "samsung_tv", "android_tv", "google_tv", "fire_tv", "lg_tv", "projector"];

const REMOTE_CHANNELS = ["atv_remote", "samsung_ws"];

/** P139 phase 5: pair a TV's remote-control channel (Android TV: code on screen; Samsung: «Allow» on the TV). */
function PairControl({ nodeKey, channel }: { nodeKey: string; channel: string }) {
  const { t } = useTranslation();
  const [paired, setPaired] = useState<boolean | null>(null);
  const [step, setStep] = useState<"idle" | "busy" | "code">("idle");
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!nodeKey) return;
    apiNodePair(nodeKey, "status").then(r => setPaired(!!r.paired)).catch(() => setPaired(null));
  }, [nodeKey]);

  if (!nodeKey) return <span className="text-xs text-muted-foreground">({t("nodes.channels.pair_save_first")})</span>;

  const fail = (e: unknown) => {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    setMsg(detail ?? t("nodes.channels.pair_failed"));
    setStep("idle");
  };
  const start = async () => {
    setStep("busy");
    setMsg(channel === "samsung_ws" ? t("nodes.channels.pair_allow_hint") : "");
    try {
      const r = await apiNodePair(nodeKey, "start");
      if (!r.ok) { setMsg(r.error ?? t("nodes.channels.pair_failed")); setStep("idle"); return; }
      if (r.step === "code") { setStep("code"); setMsg(t("nodes.channels.pair_code_hint")); return; }
      setPaired(true); setStep("idle"); setMsg(t("nodes.channels.pair_done"));
    } catch (e) { fail(e); }
  };
  const finish = async () => {
    setStep("busy");
    try {
      const r = await apiNodePair(nodeKey, "finish", code);
      if (!r.ok) { setMsg(r.error ?? t("nodes.channels.pair_failed")); setStep("code"); return; }
      setPaired(true); setStep("idle"); setCode(""); setMsg(t("nodes.channels.pair_done"));
    } catch (e) { fail(e); }
  };

  return (
    <span className="flex items-center gap-2" style={{ flexWrap: "wrap" }}>
      {paired !== null && (
        <span className="text-xs" style={{ color: paired ? "#4caf50" : "#ff9800" }}>
          {paired ? t("nodes.channels.paired") : t("nodes.channels.not_paired")}
        </span>
      )}
      {step === "code" ? (
        <>
          <Input value={code} onChange={e => setCode(e.target.value)} placeholder="ABC123" className="w-24" />
          <Button variant="outline" size="sm" onClick={finish} disabled={!code.trim()}>{t("nodes.channels.pair_finish")}</Button>
        </>
      ) : (
        <Button variant="outline" size="sm" onClick={start} disabled={step === "busy"}>
          {step === "busy" && <Loader2 className="h-3 w-3 mr-1 animate-spin" />}
          {paired ? t("nodes.channels.pair_again") : t("nodes.channels.pair")}
        </Button>
      )}
      {msg && <span className="text-xs text-muted-foreground">{msg}</span>}
    </span>
  );
}

/** DT04 R0.1: the DLNA renderer's description URL — lets KTSB reach it without the network search (Docker Desktop). */
function DlnaUrlField({ form, onChange }: { form: NodeConfig; onChange: (c: NodeConfig["channels"]) => void }) {
  const { t } = useTranslation();
  const chans = form.channels ?? {};
  const entry = chans.dlna ?? { enabled: true };
  return (
    <div className="mt-1" style={{ width: "100%" }}>
      <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.channels.dlna_url")}</Label>
      <Input
        value={entry.description_url ?? ""}
        onChange={e => onChange({ ...chans, dlna: { ...entry, description_url: e.target.value.trim() || undefined } })}
        placeholder={`http://${form.host || "192.168.0.xxx"}:9197/dmr`}
      />
      <p className="text-xs text-muted-foreground mt-1">{t("nodes.channels.dlna_url_hint")}</p>
    </div>
  );
}

/** P151: the node's KTSB screen page. Max video size (all screens); for a screen without Cast (a Samsung browser, a
 *  wall tablet) the one-time setup: open the address in the screen's browser, a 4-digit code shows, type it here. */
function ScreenPageSection({ form, nodeKey, onMax }: { form: NodeConfig; nodeKey: string; onMax: (v: string) => void }) {
  const { t } = useTranslation();
  const [st, setSt] = useState<ScreenStatus | null>(null);
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const hasCast = !!form.channels?.cast && (form.channels.cast.enabled ?? true);
  const load = useCallback(() => {
    if (nodeKey) apiNodeScreen(nodeKey, "status").then(setSt).catch(() => setSt(null));
  }, [nodeKey]);
  useEffect(load, [load]);

  const pair = async () => {
    setBusy(true);
    try {
      const r = await apiNodeScreen(nodeKey, "pair", code.trim());
      setMsg(r.ok ? t("nodes.screen.pair_done") : r.off ? t("nodes.screen.off") : t("nodes.screen.pair_wrong"));
      if (r.ok) setCode("");
      load();
    } catch { setMsg(t("nodes.screen.pair_failed")); }
    setBusy(false);
  };
  const unpair = async () => {
    setBusy(true);
    try { await apiNodeScreen(nodeKey, "unpair"); setMsg(t("nodes.screen.unpaired")); load(); }
    catch { setMsg(t("nodes.screen.pair_failed")); }
    setBusy(false);
  };

  return (
    <div>
      <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.screen.label")}</Label>
      <div className="flex items-center gap-2 text-sm" style={{ flexWrap: "wrap" }}>
        <span>{t("nodes.screen.max_label")}</span>
        <select value={form.screen_max || "1280x720"} onChange={e => onMax(e.target.value)}
                style={{ background: "transparent", color: "inherit", border: "1px solid #444", borderRadius: 6, padding: "2px 6px" }}>
          {["640x360", "1280x720", "1920x1080"].map(s => <option key={s} value={s} style={{ color: "#000" }}>{s}</option>)}
        </select>
      </div>
      <p className="text-xs text-muted-foreground mt-1">{t("nodes.screen.max_hint")}</p>
      {!nodeKey ? (
        <p className="text-xs text-muted-foreground mt-1">({t("nodes.channels.pair_save_first")})</p>
      ) : st && !st.enabled ? (
        <p className="text-xs text-muted-foreground mt-1">{t("nodes.screen.off")}</p>
      ) : hasCast ? (
        <p className="text-xs text-muted-foreground mt-1">{t("nodes.screen.cast_no_setup")}</p>
      ) : (
        <div className="mt-2 space-y-1">
          <p className="text-xs">
            <span style={{ color: st?.paired ? "#4caf50" : "#ff9800" }}>
              {st?.paired ? t("nodes.screen.paired") : t("nodes.screen.not_paired")}
            </span>
            {st?.paired && st.seen_recently && <span className="text-muted-foreground"> · {t("nodes.screen.seen")}</span>}
          </p>
          {!st?.paired && (
            <>
              <p className="text-xs text-muted-foreground">{t("nodes.screen.setup_hint")}</p>
              <code className="text-xs" style={{ userSelect: "all" }}>{st?.page_url ?? "…"}</code>
              <div className="flex items-center gap-2 mt-1">
                <Input value={code} onChange={e => setCode(e.target.value.replace(/\D/g, "").slice(0, 4))}
                       placeholder="0000" className="w-24" inputMode="numeric" />
                <Button variant="outline" size="sm" onClick={pair} disabled={busy || code.length !== 4}>
                  {busy && <Loader2 className="h-3 w-3 mr-1 animate-spin" />}{t("nodes.screen.pair")}
                </Button>
              </div>
            </>
          )}
          {st?.paired && (
            <Button variant="outline" size="sm" onClick={unpair} disabled={busy}>{t("nodes.screen.unpair")}</Button>
          )}
          {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
        </div>
      )}
    </div>
  );
}

function ChannelsSection({ form, onChange, nodeKey }: { form: NodeConfig; onChange: (c: NodeConfig["channels"]) => void; nodeKey: string }) {
  const { t } = useTranslation();
  const [probing, setProbing] = useState(false);
  const [result, setResult] = useState<string>("");
  const chans = form.channels ?? {};
  const listed = CHANNELS.filter(c => chans[c.name]);

  const probe = async () => {
    setProbing(true);
    setResult("");
    try {
      const r = await apiProbeNodeChannels(form);
      onChange(r.channels);
      const names = Object.keys(r.found).map(n => t(`nodes.channels.name.${n}`));
      setResult(names.length ? t("nodes.channels.found", { names: names.join(", ") }) : t("nodes.channels.none_found"));
    } catch {
      setResult(t("nodes.channels.probe_failed"));
    }
    setProbing(false);
  };

  const toggle = (name: string, on: boolean) => onChange({ ...chans, [name]: { ...chans[name], enabled: on } });
  const when = (iso?: string) => (iso ? new Date(iso).toLocaleDateString() : "");

  return (
    <div>
      <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.channels.label")}</Label>
      {listed.length === 0 && <p className="text-xs text-muted-foreground">{t("nodes.channels.empty")}</p>}
      <div className="space-y-1">
        {listed.map(c => {
          const entry = chans[c.name];
          return (
            <div key={c.name} className="flex items-center gap-2 text-sm" style={{ flexWrap: "wrap" }}>
              <Switch checked={entry.enabled ?? true} onCheckedChange={v => toggle(c.name, v)} />
              <span>{t(`nodes.channels.name.${c.name}`)}</span>
              {entry.port && <span className="text-xs text-muted-foreground">:{entry.port}</span>}
              {!c.driver && <span className="text-xs text-muted-foreground">({t("nodes.channels.no_driver")})</span>}
              {REMOTE_CHANNELS.includes(c.name) && (entry.enabled ?? true) && <PairControl nodeKey={nodeKey} channel={c.name} />}
              {entry.found_at && (
                <span className="text-xs text-muted-foreground">{t("nodes.channels.found_at", { date: when(entry.found_at) })}</span>
              )}
              {c.name === "dlna" && (entry.enabled ?? true) && <DlnaUrlField form={form} onChange={onChange} />}
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-2 mt-2" style={{ flexWrap: "wrap" }}>
        <Button variant="outline" size="sm" onClick={probe} disabled={probing || !form.host}>
          {probing && <Loader2 className="h-3 w-3 mr-1 animate-spin" />}
          {t("nodes.channels.probe")}
        </Button>
        {result && <span className="text-xs text-muted-foreground">{result}</span>}
      </div>
      <p className="text-xs text-muted-foreground mt-1">{t("nodes.channels.hint")}</p>
    </div>
  );
}

function NodeModal({
  nodeKey,
  node,
  rooms,
  allNodes,
  onSave,
  onClose,
}: {
  nodeKey: string;
  node: NodeConfig;
  rooms: RoomsView | null;
  allNodes: Record<string, NodeConfig>;
  onSave: (key: string, updated: NodeConfig, newPlaceName: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [key, setKey] = useState(nodeKey);
  // P137: the node's place — its own `place`, else what its free-text room resolves to.
  const [form, setForm] = useState<NodeConfig>({ ...node, place: node.place || rooms?.nodes[nodeKey]?.place || "" });
  const [newPlaceName, setNewPlaceName] = useState("");
  const places = (rooms?.places ?? []).filter(p => !p.hidden);

  const set = <K extends keyof NodeConfig>(k: K, v: NodeConfig[K]) =>
    setForm(p => ({ ...p, [k]: v }));

  const handleTypeChange = (v: string | null) => {
    if (!v) return;
    const meta = TYPE_META[v] ?? DEFAULT_TYPE_META;
    const port = DEFAULT_PORTS[v];
    setForm(p => ({
      ...p,
      type: v,
      has_audio: meta.defaultAudio,
      has_display: meta.defaultDisplay,
      ...(port !== undefined ? { api_port: port } : { api_port: undefined }),
    }));
  };

  const handleSave = () => {
    if (!key.trim()) return;
    const meta = TYPE_META[form.type] ?? DEFAULT_TYPE_META;
    const isNew = form.place === NEW_PLACE;
    if (isNew && !newPlaceName.trim()) return;
    const place = isNew ? newPlaceName.trim().toLowerCase().replace(/\s+/g, "_") : form.place ?? "";
    // `room` keeps the place's name for readers that still use the free-text field.
    const room = isNew ? newPlaceName.trim() : places.find(p => p.id === place)?.name ?? form.room;
    // An output switched off «Auto» with nothing picked is still auto.
    const asAuto = (v: NodeConfig["audio_out"]) => (Array.isArray(v) && v.length === 0 ? "auto" : v);
    const resolved = {
      ...form,
      audio_out: asAuto(form.audio_out),
      image_out: asAuto(form.image_out),
      place,
      room,
      has_audio: form.has_audio ?? meta.defaultAudio,
      has_display: form.has_display ?? meta.defaultDisplay,
    };
    onSave(key.trim(), resolved, isNew ? newPlaceName.trim() : "");
    onClose();
  };

  const meta = TYPE_META[form.type] ?? DEFAULT_TYPE_META;
  const effectiveAudio = form.has_audio ?? meta.defaultAudio;
  const effectiveDisplay = form.has_display ?? meta.defaultDisplay;

  const isDirect = form.type === DIRECT_TYPE;
  const isTV = ["apple_tv", "samsung_tv", "android_tv", "google_tv", "fire_tv", "lg_tv", "projector"].includes(form.type);
  // P138: a node people talk to (its own mic, or started by a button) — where its answers go.
  const isInput = isDirect || form.type === "wyoming" || form.type === "esp32" || !!form.mic_enabled || !!form.trigger;
  // Starting from HA records on this machine's mic → KTSB Direct (and old ESP32 button nodes); kept visible while
  // a node still has a trigger or webhook so it can be removed.
  const canTrigger = isDirect || form.type === "esp32" || !!form.trigger || !!form.webhook_token;
  const hasTvOverlay = ["android_tv", "google_tv"].includes(form.type);
  // N30: network speakers that clip the start of a reply — same list and defaults as kaare_core/voice/lead_in.py
  const hasLeadIn = ["ha_media_player", "chromecast"].includes(form.type);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-[#1a1d2e] border border-border rounded-xl w-full max-w-lg mx-4 p-6 space-y-4 overflow-y-auto"
        style={{ maxHeight: "90vh" }}
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold">{nodeKey ? t("nodes.modal.edit_title") : t("nodes.modal.new_title")}</h2>

        <div className="space-y-3">
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.node_id")}</Label>
            <Input value={key} onChange={e => setKey(e.target.value)} placeholder="f.eks. stue_tv" disabled={!!nodeKey} />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.room")}</Label>
            {rooms ? (
              <select
                value={form.place ?? ""}
                onChange={e => set("place", e.target.value)}
                className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                style={{ background: "#111", color: "#eee" }}
              >
                <option value="">{t("nodes.modal.no_place")}</option>
                {places.some(p => p.source === "ha") && (
                  <optgroup label={t("aliases.dlg_places_ha")}>
                    {places.filter(p => p.source === "ha").map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </optgroup>
                )}
                {places.some(p => p.source === "manual") && (
                  <optgroup label={t("aliases.dlg_places_own")}>
                    {places.filter(p => p.source === "manual").map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </optgroup>
                )}
                <option value={NEW_PLACE}>{t("aliases.dlg_room_new_option")}</option>
              </select>
            ) : (
              <Input value={form.room} onChange={e => set("room", e.target.value)} placeholder={t("nodes.modal.room_placeholder")} />
            )}
            {form.place === NEW_PLACE && (
              <Input className="mt-2" value={newPlaceName} onChange={e => setNewPlaceName(e.target.value)} placeholder={t("rooms.dlg_name_ph")} />
            )}
            {rooms && !form.place && node.room && (
              <p className="text-xs mt-1" style={{ color: "#ff9f43" }}>{t("nodes.modal.place_unresolved", { room: node.room })}</p>
            )}
          </div>
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("rooms.note")}</Label>
            <Input value={form.location_note ?? ""} onChange={e => set("location_note", e.target.value)} placeholder={t("nodes.modal.location_note_ph")} />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.description")}</Label>
            <Input value={form.description ?? ""} onChange={e => set("description", e.target.value)} placeholder={t("nodes.modal.description_placeholder")} />
          </div>

          {isDirect && <p className="text-xs text-muted-foreground">{t("nodes.direct.hint")}</p>}

          {/* Type selector — grouped (fixed for KTSB Direct) */}
          {isDirect ? (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.type")}</Label>
              <p className="text-sm">{t("nodes.direct.label")} <span className="text-xs text-muted-foreground">· {t("nodes.direct.type_fixed")}</span></p>
            </div>
          ) : (
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.type")}</Label>
            <Select value={form.type ?? "ha_media_player"} onValueChange={handleTypeChange}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectLabel>{t("nodes.modal.type_group_audio")}</SelectLabel>
                  <SelectItem value="ha_media_player">HA media_player</SelectItem>
                  <SelectItem value="esp32">ESP32 (ESPHome)</SelectItem>
                  <SelectItem value="wyoming">{t("nodes.type_wyoming")}</SelectItem>
                  <SelectItem value="chromecast">Chromecast</SelectItem>
                  <SelectItem value="snapcast">Snapcast</SelectItem>
                  <SelectItem value="airplay">AirPlay</SelectItem>
                  <SelectItem value="dlna">DLNA/UPnP</SelectItem>
                </SelectGroup>
                <SelectGroup>
                  <SelectLabel>{t("nodes.modal.type_group_display")}</SelectLabel>
                  <SelectItem value="apple_tv">Apple TV</SelectItem>
                  <SelectItem value="samsung_tv">Samsung Smart TV</SelectItem>
                  <SelectItem value="android_tv">Android TV</SelectItem>
                  <SelectItem value="google_tv">Google TV / Chromecast TV</SelectItem>
                  <SelectItem value="fire_tv">Amazon Fire TV</SelectItem>
                  <SelectItem value="lg_tv">LG TV (webOS)</SelectItem>
                  <SelectItem value="projector">{t("nodes.type_projector")}</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>
          )}

          {isDirect && <DirectMicPicker />}

          {/* HA entity_id — shown for ha_media_player and all TV types (optional for TV) */}
          {(form.type === "ha_media_player" || isTV) && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">
                {t("nodes.modal.entity_id")}{isTV ? ` (${t("common.optional")})` : ""}
              </Label>
              <Input value={form.entity_id ?? ""} onChange={e => set("entity_id", e.target.value)} placeholder="media_player.stue_tv" />
            </div>
          )}

          {/* Host — shown for all except KTSB Direct; optional for HA players (gives KTSB its own Cast, P139) */}
          {!isDirect && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">
                {t("nodes.modal.host")}{["projector", "fire_tv", "ha_media_player"].includes(form.type) ? ` (${t("common.optional")})` : ""}
              </Label>
              <Input value={form.host ?? ""} onChange={e => set("host", e.target.value)} placeholder="192.168.0.xxx" />
              {form.type === "ha_media_player" && (
                <p className="text-xs text-muted-foreground mt-1">{t("nodes.modal.host_ha_hint")}</p>
              )}
            </div>
          )}

          {/* API port — for esp32, wyoming, snapcast, dlna */}
          {["esp32", "wyoming", "snapcast", "dlna"].includes(form.type) && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">
                {t("nodes.modal.api_port")}{form.type === "dlna" ? ` (${t("common.optional")})` : ""}
              </Label>
              <Input
                type="number"
                value={form.api_port ?? ""}
                onChange={e => set("api_port", e.target.value ? Number(e.target.value) : undefined)}
                className="w-28"
                placeholder={form.type === "dlna" ? "—" : String(DEFAULT_PORTS[form.type] ?? "")}
              />
            </div>
          )}
          {form.type === "dlna" && !!form.host && <DlnaUrlField form={form} onChange={c => set("channels", c)} />}

          {/* MAC — for samsung_tv (Wake-on-LAN) */}
          {form.type === "samsung_tv" && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.mac")}</Label>
              <Input value={form.mac ?? ""} onChange={e => set("mac", e.target.value)} placeholder="aa:bb:cc:dd:ee:ff" />
            </div>
          )}

          {/* ADB port — for android_tv, google_tv, fire_tv */}
          {["android_tv", "google_tv", "fire_tv"].includes(form.type) && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.adb_port")}</Label>
              <Input
                type="number"
                value={form.adb_port ?? 5555}
                onChange={e => set("adb_port", Number(e.target.value))}
                className="w-28"
              />
            </div>
          )}

          {/* N30: silence before the reply — network speakers that clip the start */}
          {hasLeadIn && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.lead_in")}</Label>
              <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                <label className="text-xs text-muted-foreground" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  {t("nodes.modal.lead_in_cold")}
                  <Input
                    type="number"
                    min={0}
                    max={3000}
                    step={100}
                    value={form.lead_in_ms ?? 400}
                    onChange={e => set("lead_in_ms", e.target.value === "" ? undefined : Number(e.target.value))}
                    className="w-24"
                  />
                </label>
                <label className="text-xs text-muted-foreground" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  {t("nodes.modal.lead_in_playing")}
                  <Input
                    type="number"
                    min={0}
                    max={3000}
                    step={100}
                    value={form.lead_in_playing_ms ?? 1200}
                    onChange={e => set("lead_in_playing_ms", e.target.value === "" ? undefined : Number(e.target.value))}
                    className="w-24"
                  />
                </label>
              </div>
              <p className="text-xs text-muted-foreground mt-1">{t("nodes.modal.lead_in_hint")}</p>
            </div>
          )}

          {/* P139 phase 2: channels — for screens and Cast devices with an address */}
          {(SCREEN_TYPES.includes(form.type) || form.type === "ha_media_player") && !!form.host && (
            <ChannelsSection form={form} onChange={c => set("channels", c)} nodeKey={nodeKey} />
          )}

          {/* P151: KTSB's screen page — max video size, and pairing for screens without Cast */}
          {(SCREEN_TYPES.includes(form.type) || form.type === "ha_media_player") && !!form.host && (
            <ScreenPageSection form={form} nodeKey={nodeKey} onMax={v => set("screen_max", v)} />
          )}

          {/* TvOverlay port — for android_tv, google_tv */}
          {hasTvOverlay && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">
                {t("nodes.modal.tvoverlay_port")} ({t("common.optional")})
              </Label>
              <Input
                type="number"
                value={form.tvoverlay_port ?? 5001}
                onChange={e => set("tvoverlay_port", e.target.value ? Number(e.target.value) : undefined)}
                className="w-28"
              />
              <p className="text-xs text-muted-foreground mt-1">{t("nodes.modal.tvoverlay_hint")}</p>
            </div>
          )}

          {/* Mic toggle — for wyoming */}
          {form.type === "wyoming" && (
            <div className="flex items-center gap-2 pt-1">
              <Switch checked={form.mic_enabled ?? false} onCheckedChange={v => set("mic_enabled", v)} />
              <span className="text-sm">{t("nodes.modal.mic_enabled")}</span>
            </div>
          )}

          {/* Default user */}
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.default_user")}</Label>
            <Input value={form.default_user ?? ""} onChange={e => set("default_user", e.target.value)} placeholder="admin" />
          </div>

          {/* Trust: who an unidentified speaker is at this node (P120) */}
          {(isDirect || form.type === "wyoming" || form.type === "esp32" || form.mic_enabled) && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.trust")}</Label>
              <Select value={form.trust ?? "indoor"} onValueChange={v => set("trust", v as "indoor" | "outdoor")}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="indoor">{t("nodes.modal.trust_indoor")}</SelectItem>
                  <SelectItem value="outdoor">{t("nodes.modal.trust_outdoor")}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">{t("nodes.modal.trust_hint")}</p>
            </div>
          )}

          {/* Capability toggles (fixed for KTSB Direct: speaker yes, display no) */}
          {!isDirect && (
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.capabilities")}</Label>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Switch checked={effectiveAudio} onCheckedChange={v => set("has_audio", v)} />
                <span className="text-sm">{t("nodes.modal.has_audio")}</span>
              </div>
              <div className="flex items-center gap-2">
                <Switch checked={effectiveDisplay} onCheckedChange={v => set("has_display", v)} />
                <span className="text-sm">{t("nodes.modal.has_display")}</span>
              </div>
            </div>
          </div>
          )}

          {/* P138: where the reply goes, and starting from Home Assistant */}
          {isInput && (
            <div className="space-y-3 rounded-md border border-border p-3">
              <Label className="text-xs text-muted-foreground block">{t("nodes.routing.title")}</Label>
              <OutputPicker kind="audio" nodeKey={key} value={form.audio_out} allNodes={allNodes} onChange={v => set("audio_out", v)} />
              <OutputPicker kind="image" nodeKey={key} value={form.image_out} allNodes={allNodes} onChange={v => set("image_out", v)} />
              <p className="text-xs text-muted-foreground">{t("nodes.routing.unreachable_hint")}</p>
            </div>
          )}
          {canTrigger && (
            <TriggerSection
              nodeKey={nodeKey}
              trigger={form.trigger}
              webhookToken={form.webhook_token}
              onChange={tr => set("trigger", tr)}
              onWebhook={tok => set("webhook_token", tok || undefined)}
            />
          )}

          {/* Enabled toggle */}
          <div className="flex items-center gap-2 pt-1">
            <Switch checked={form.enabled} onCheckedChange={v => set("enabled", v)} />
            <span className="text-sm">{form.enabled ? t("nodes.enabled") : t("nodes.disabled")}</span>
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose}>{t("nodes.modal.cancel")}</Button>
          <Button onClick={handleSave} disabled={!key.trim()}>{t("nodes.modal.save")}</Button>
        </div>
      </div>
    </div>
  );
}

/** N29: green = answers, red = does not answer, grey = cannot be checked / not checked yet. */
function OnlineIcon({ status }: { status: NodeStatus | undefined }) {
  const { t } = useTranslation();
  const st = status?.status ?? "unknown";
  const color = st === "online" ? "#4caf50" : st === "offline" ? "#f44336" : "#666";
  const when = status?.checked_at ? new Date(status.checked_at * 1000).toLocaleTimeString() : "";
  const detail = status?.channels
    ? Object.entries(status.channels).map(([n, ok]) => `${t(`nodes.channels.name.${n}`)} ${ok ? "✓" : "✗"}`).join(", ")
    : status?.detail;
  const title = [t(`nodes.online.${st}`), detail, when && t("nodes.online.checked", { time: when })]
    .filter(Boolean).join(" · ");
  const Icon = st === "offline" ? WifiOff : Wifi;
  return <Icon size={15} style={{ color, flexShrink: 0 }} aria-label={title}><title>{title}</title></Icon>;
}

function NodeCard({
  nodeKey,
  node,
  status,
  placeName,
  onEdit,
  onDelete,
  onToggle,
}: {
  nodeKey: string;
  node: NodeConfig;
  status: NodeStatus | undefined;
  placeName: string | null;  // null = rooms not loaded; "" = the node has no place
  onEdit: () => void;
  onDelete: () => void;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const [testState, setTestState] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const [testInfo, setTestInfo] = useState("");
  const meta = TYPE_META[node.type] ?? DEFAULT_TYPE_META;
  const typeColor = meta.color;
  const topColor = node.enabled ? typeColor : "#2a2a2a";

  const showAudio = node.has_audio ?? meta.defaultAudio;
  const showDisplay = node.has_display ?? meta.defaultDisplay;
  const isDirect = node.type === DIRECT_TYPE;

  const handleTestDisplay = async () => {
    setTestState("testing");
    setTestInfo("");
    try {
      const r = await apiTestDisplayNode(nodeKey);
      const tried = (r.tried ?? []).map(x => t(`nodes.channels.name.${x.channel}`)).join(", ");
      const via = r.channel ? t(`nodes.channels.name.${r.channel}`) : (r.method ?? "");
      setTestInfo(tried ? t("nodes.channels.test_via_fallback", { via, tried }) : t("nodes.channels.test_via", { via }));
      setTestState("ok");
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setTestInfo(detail ?? "");
      setTestState("fail");
    }
    setTimeout(() => setTestState("idle"), 3000);
  };

  return (
    <div style={{
      background: "#141414",
      border: "1px solid #252525",
      borderTop: `2px solid ${topColor}`,
      borderRadius: 10,
      padding: 16,
      display: "flex",
      flexDirection: "column",
      gap: 8,
      transition: "border-color 0.2s",
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <p style={{ color: node.enabled ? "#e8e8e8" : "#666", fontSize: 14, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{nodeKey}</p>
          {isDirect && !node.description && <p style={{ color: "#666", fontSize: 12 }}>{t("nodes.direct.name")}</p>}
          {node.description && <p style={{ color: "#666", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.description}</p>}
        </div>
        <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: 8 }}>
          <OnlineIcon status={status} />
          <Switch checked={node.enabled} onCheckedChange={onToggle} />
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        {placeName === "" ? (
          <span title={t("nodes.no_place_hint")} style={{ background: "#ff9f4322", color: "#ff9f43", border: "1px solid #ff9f4344", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 4 }}>{t("nodes.no_place")}{node.room ? ` · ${node.room}` : ""}</span>
        ) : (
          <span style={{ background: "#8b7cf822", color: "#8b7cf8", border: "1px solid #8b7cf844", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 4 }}>{placeName ?? node.room}</span>
        )}
        {node.location_note && <span style={{ color: "#999", fontSize: 11 }}>{node.location_note}</span>}
        <span style={{ background: `${typeColor}22`, color: typeColor, border: `1px solid ${typeColor}44`, fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 4, letterSpacing: 0.3 }}>{node.type === "projector" ? t("nodes.type_projector") : meta.label}</span>
        {node.type === "ha_media_player" && node.entity_id && (
          <span style={{ color: "#666", fontSize: 11, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 180 }}>{node.entity_id}</span>
        )}
        {node.type !== "ha_media_player" && node.host && (
          <span style={{ color: "#666", fontSize: 11, fontFamily: "monospace" }}>
            {node.api_port ? `${node.host}:${node.api_port}` : node.host}
          </span>
        )}
      </div>

      {/* Capability badges */}
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        {showAudio && <Volume2 size={14} style={{ color: "#4caf50" }} aria-label={t("nodes.modal.has_audio")} />}
        {showDisplay && <Monitor size={14} style={{ color: "#60a5fa" }} aria-label={t("nodes.modal.has_display")} />}
        {(node.mic_enabled || isDirect) && <Mic size={14} style={{ color: "#f59e0b" }} aria-label={t("nodes.modal.mic_enabled")} />}
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Button variant="outline" size="sm" onClick={onEdit}>
          <Pencil className="h-3 w-3 mr-1" /> {t("nodes.edit")}
        </Button>
        {showDisplay && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleTestDisplay}
            disabled={testState === "testing"}
            style={{
              color: testState === "ok" ? "#4caf50" : testState === "fail" ? "#f44336" : undefined,
            }}
          >
            {testState === "testing" ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : testState === "ok" ? (
              <CheckCircle2 className="h-3 w-3 mr-1" />
            ) : testState === "fail" ? (
              <XCircle className="h-3 w-3 mr-1" />
            ) : (
              <Monitor className="h-3 w-3 mr-1" />
            )}
            {t("common.test")}
          </Button>
        )}
        {testInfo && <span className="text-xs text-muted-foreground" style={{ alignSelf: "center" }}>{testInfo}</span>}
        {!isDirect && (
          <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={onDelete}>
            <Trash2 className="h-3 w-3 mr-1" /> {t("nodes.delete")}
          </Button>
        )}
      </div>
    </div>
  );
}

const SECTIONS: { key: NodeSection; titleKey: string }[] = [
  { key: "audio",   titleKey: "nodes.section.audio" },
  { key: "multi",   titleKey: "nodes.section.multi" },
  { key: "display", titleKey: "nodes.section.display" },
];

export default function Nodes() {
  const { t } = useTranslation();
  const [nodes, setNodes] = useState<Record<string, NodeConfig>>({});
  const [editKey, setEditKey] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [rooms, setRooms] = useState<RoomsView | null>(null);
  const [statuses, setStatuses] = useState<Record<string, NodeStatus>>({});
  const ss = useSaveState();

  // N29: reachability while the page is open — every 30 s, nothing in the background otherwise.
  useEffect(() => {
    let alive = true;
    const poll = () => apiGetNodesStatus().then(s => { if (alive) setStatuses(s); }).catch(() => {});
    poll();
    const id = setInterval(poll, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    apiGetNodes().then(d => setNodes(d.nodes)).catch(() => {});
    apiGetRooms().then(setRooms).catch(() => setRooms(null));
  }, []);

  const placeNameOf = (key: string, node: NodeConfig): string | null => {
    if (!rooms) return null;
    const id = node.place || rooms.nodes[key]?.place || "";
    return id ? rooms.places.find(p => p.id === id)?.name ?? id : "";
  };

  const saveAll = async (updated: Record<string, NodeConfig>) => {
    ss.saving();
    try {
      await apiPutNodes(updated);
      apiGetRooms().then(setRooms).catch(() => {});
      ss.saved();
    } catch { ss.error(); }
  };

  const handleSave = async (key: string, node: NodeConfig, newPlaceName: string) => {
    if (newPlaceName && rooms) {
      // A new KTSB place made from the node dialog goes into the place register first.
      const raw = JSON.parse(JSON.stringify(rooms.raw ?? {}));
      raw.places = { ...(raw.places ?? {}), [node.place ?? ""]: { name: newPlaceName } };
      try { setRooms(await apiPutRooms(raw)); } catch { ss.error(); return; }
    }
    const updated = { ...nodes, [key]: node };
    setNodes(updated);
    saveAll(updated);
  };

  const handleDelete = (key: string) => {
    if (!confirm(t("nodes.delete_confirm", { key }))) return;
    const rest = withoutKey(nodes, key);
    setNodes(rest);
    saveAll(rest);
  };

  const toggleEnabled = (key: string) => {
    const updated = { ...nodes, [key]: { ...nodes[key], enabled: !nodes[key].enabled } };
    setNodes(updated);
    saveAll(updated);
  };

  // KTSB Direct first: it is this machine.
  const nodeEntries = Object.entries(nodes).sort(([, a], [, b]) => Number(b.type === DIRECT_TYPE) - Number(a.type === DIRECT_TYPE));
  const bySection: Record<NodeSection, [string, NodeConfig][]> = {
    audio:   nodeEntries.filter(([, n]) => getSection(n) === "audio"),
    multi:   nodeEntries.filter(([, n]) => getSection(n) === "multi"),
    display: nodeEntries.filter(([, n]) => getSection(n) === "display"),
  };

  return (
    <div>
      <h1>{t("nodes.title")}</h1>

      <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 20 }}>
          <div>
            <div style={{ color: "#ddd", fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{t("nodes.card_title")}</div>
            <p style={{ color: "#666", fontSize: 13 }}>{t("nodes.card_description")}</p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
            <SaveFeedback state={ss.state} />
            <Button size="sm" onClick={() => setShowNew(true)}>
              <Plus className="h-4 w-4 mr-1" /> {t("nodes.new_node")}
            </Button>
          </div>
        </div>

        {nodeEntries.length === 0 ? (
          <p style={{ color: "#555", fontSize: 13 }}>{t("nodes.empty")}</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
            {SECTIONS.map(({ key: sectionKey, titleKey }) => {
              const sectionNodes = bySection[sectionKey];
              if (sectionNodes.length === 0) return null;
              return (
                <div key={sectionKey}>
                  <div style={{ color: "#888", fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: "uppercase", marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
                    {sectionKey === "audio" && <Volume2 size={13} />}
                    {sectionKey === "multi" && <><Volume2 size={13} /><Monitor size={13} /></>}
                    {sectionKey === "display" && <Monitor size={13} />}
                    {t(titleKey)} ({sectionNodes.length})
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
                    {sectionNodes.map(([key, node]) => (
                      <NodeCard
                        key={key}
                        nodeKey={key}
                        node={node}
                        status={statuses[key]}
                        placeName={placeNameOf(key, node)}
                        onEdit={() => setEditKey(key)}
                        onDelete={() => handleDelete(key)}
                        onToggle={() => toggleEnabled(key)}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <VoicePipelineCard />

      {editKey !== null && nodes[editKey] && (
        <NodeModal
          nodeKey={editKey}
          node={nodes[editKey]}
          rooms={rooms}
          allNodes={nodes}
          onSave={handleSave}
          onClose={() => setEditKey(null)}
        />
      )}

      {showNew && (
        <NodeModal
          nodeKey=""
          node={EMPTY_NODE}
          rooms={rooms}
          allNodes={nodes}
          onSave={handleSave}
          onClose={() => setShowNew(false)}
        />
      )}
    </div>
  );
}
