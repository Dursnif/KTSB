import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Loader2, CheckCircle2, XCircle, Pencil, Plus, Trash2, Volume2, Monitor, Mic } from "lucide-react";
import { apiGetNodes, apiPutNodes, apiTestDisplayNode, type NodeConfig } from "@/services/api";

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

function NodeModal({
  nodeKey,
  node,
  onSave,
  onClose,
}: {
  nodeKey: string;
  node: NodeConfig;
  onSave: (key: string, updated: NodeConfig) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [key, setKey] = useState(nodeKey);
  const [form, setForm] = useState<NodeConfig>({ ...node });

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
    onSave(key.trim(), form);
    onClose();
  };

  const meta = TYPE_META[form.type] ?? DEFAULT_TYPE_META;
  const effectiveAudio = form.has_audio ?? meta.defaultAudio;
  const effectiveDisplay = form.has_display ?? meta.defaultDisplay;

  const isTV = ["apple_tv", "samsung_tv", "android_tv", "google_tv", "fire_tv", "lg_tv", "projector"].includes(form.type);
  const hasTvOverlay = ["android_tv", "google_tv"].includes(form.type);

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
            <Input value={form.room} onChange={e => set("room", e.target.value)} placeholder="stue" />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.description")}</Label>
            <Input value={form.description ?? ""} onChange={e => set("description", e.target.value)} placeholder="Nest Hub — kjøkkenet" />
          </div>

          {/* Type selector — grouped */}
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.type")}</Label>
            <Select value={form.type ?? "ha_media_player"} onValueChange={handleTypeChange}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectLabel>{t("nodes.modal.type_group_audio")}</SelectLabel>
                  <SelectItem value="ha_media_player">HA media_player</SelectItem>
                  <SelectItem value="esp32">ESP32 (ESPHome)</SelectItem>
                  <SelectItem value="wyoming">Wyoming (satellitt, mic+høyttaler)</SelectItem>
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
                  <SelectItem value="projector">Projektor</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>

          {/* HA entity_id — shown for ha_media_player and all TV types (optional for TV) */}
          {(form.type === "ha_media_player" || isTV) && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">
                {t("nodes.modal.entity_id")}{isTV ? ` (${t("common.optional")})` : ""}
              </Label>
              <Input value={form.entity_id ?? ""} onChange={e => set("entity_id", e.target.value)} placeholder="media_player.stue_tv" />
            </div>
          )}

          {/* Host — shown for all except ha_media_player */}
          {form.type !== "ha_media_player" && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">
                {t("nodes.modal.host")}{form.type === "projector" || form.type === "fire_tv" ? ` (${t("common.optional")})` : ""}
              </Label>
              <Input value={form.host ?? ""} onChange={e => set("host", e.target.value)} placeholder="192.168.0.xxx" />
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

          {/* TvOverlay port — for android_tv, google_tv */}
          {hasTvOverlay && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">
                {t("nodes.modal.tvoverlay_port")} ({t("common.optional")})
              </Label>
              <Input
                type="number"
                value={form.tvoverlay_port ?? 7979}
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

          {/* Capability toggles */}
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

function NodeCard({
  nodeKey,
  node,
  onEdit,
  onDelete,
  onToggle,
}: {
  nodeKey: string;
  node: NodeConfig;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const [testState, setTestState] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const meta = TYPE_META[node.type] ?? DEFAULT_TYPE_META;
  const typeColor = meta.color;
  const topColor = node.enabled ? typeColor : "#2a2a2a";

  const showAudio = node.has_audio ?? meta.defaultAudio;
  const showDisplay = node.has_display ?? meta.defaultDisplay;

  const handleTestDisplay = async () => {
    setTestState("testing");
    try {
      await apiTestDisplayNode(nodeKey);
      setTestState("ok");
    } catch {
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
          {node.description && <p style={{ color: "#666", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.description}</p>}
        </div>
        <div style={{ flexShrink: 0 }}>
          <Switch checked={node.enabled} onCheckedChange={onToggle} />
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <span style={{ background: "#8b7cf822", color: "#8b7cf8", border: "1px solid #8b7cf844", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 4 }}>{node.room}</span>
        <span style={{ background: `${typeColor}22`, color: typeColor, border: `1px solid ${typeColor}44`, fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 4, letterSpacing: 0.3 }}>{meta.label}</span>
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
        {node.mic_enabled && <Mic size={14} style={{ color: "#f59e0b" }} aria-label={t("nodes.modal.mic_enabled")} />}
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
        <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={onDelete}>
          <Trash2 className="h-3 w-3 mr-1" /> {t("nodes.delete")}
        </Button>
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
  const ss = useSaveState();

  useEffect(() => {
    apiGetNodes().then(d => setNodes(d.nodes)).catch(() => {});
  }, []);

  const saveAll = async (updated: Record<string, NodeConfig>) => {
    ss.saving();
    try {
      await apiPutNodes(updated);
      ss.saved();
    } catch { ss.error(); }
  };

  const handleSave = (key: string, node: NodeConfig) => {
    const updated = { ...nodes, [key]: node };
    setNodes(updated);
    saveAll(updated);
  };

  const handleDelete = (key: string) => {
    if (!confirm(t("nodes.delete_confirm", { key }))) return;
    const { [key]: _, ...rest } = nodes;
    setNodes(rest);
    saveAll(rest);
  };

  const toggleEnabled = (key: string) => {
    const updated = { ...nodes, [key]: { ...nodes[key], enabled: !nodes[key].enabled } };
    setNodes(updated);
    saveAll(updated);
  };

  const nodeEntries = Object.entries(nodes);
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

      {editKey !== null && nodes[editKey] && (
        <NodeModal
          nodeKey={editKey}
          node={nodes[editKey]}
          onSave={handleSave}
          onClose={() => setEditKey(null)}
        />
      )}

      {showNew && (
        <NodeModal
          nodeKey=""
          node={EMPTY_NODE}
          onSave={handleSave}
          onClose={() => setShowNew(false)}
        />
      )}
    </div>
  );
}
