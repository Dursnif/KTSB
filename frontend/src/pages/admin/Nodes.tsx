import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Loader2, CheckCircle2, XCircle, Pencil, Plus, Trash2 } from "lucide-react";
import { apiGetNodes, apiPutNodes, type NodeConfig } from "@/services/api";

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

const TYPE_META: Record<string, { color: string; label: string }> = {
  ha_media_player: { color: "#6c8ebf", label: "HA" },
  esp32:           { color: "#82b366", label: "ESP32" },
  wyoming:         { color: "#d6a84e", label: "Wyoming" },
  chromecast:      { color: "#e8453c", label: "Chromecast" },
  snapcast:        { color: "#5d9cec", label: "Snapcast" },
  airplay:         { color: "#c0c0c0", label: "AirPlay" },
  dlna:            { color: "#9b59b6", label: "DLNA" },
};
const DEFAULT_TYPE_META = { color: "#888888", label: "Custom" };

const DEFAULT_PORTS: Partial<Record<string, number>> = {
  esp32: 6053,
  wyoming: 10300,
  snapcast: 4953,
};

const EMPTY_NODE: NodeConfig = {
  room: "",
  description: "",
  type: "ha_media_player",
  entity_id: "",
  host: "",
  api_port: undefined,
  mic_enabled: false,
  default_user: "",
  enabled: true,
};

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

  const handleSave = () => {
    if (!key.trim()) return;
    onSave(key.trim(), form);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-[#1a1d2e] border border-border rounded-xl w-full max-w-lg mx-4 p-6 space-y-4" onClick={e => e.stopPropagation()}>
        <h2 className="text-base font-semibold">{nodeKey ? t("nodes.modal.edit_title") : t("nodes.modal.new_title")}</h2>

        <div className="space-y-3">
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.node_id")}</Label>
            <Input value={key} onChange={e => setKey(e.target.value)} placeholder="f.eks. kjokken" disabled={!!nodeKey} />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.room")}</Label>
            <Input value={form.room} onChange={e => set("room", e.target.value)} placeholder="kjokken" />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.description")}</Label>
            <Input value={form.description ?? ""} onChange={e => set("description", e.target.value)} placeholder="Nest Hub — kjøkkenet" />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.type")}</Label>
            <Select
              value={form.type ?? "ha_media_player"}
              onValueChange={v => {
                if (!v) return;
                const port = DEFAULT_PORTS[v];
                setForm(p => ({ ...p, type: v, ...(port !== undefined ? { api_port: port } : { api_port: undefined }) }));
              }}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ha_media_player">HA media_player</SelectItem>
                <SelectItem value="esp32">ESP32 (ESPHome)</SelectItem>
                <SelectItem value="wyoming">Wyoming (satellitt, mic+høyttaler)</SelectItem>
                <SelectItem value="chromecast">Chromecast</SelectItem>
                <SelectItem value="snapcast">Snapcast</SelectItem>
                <SelectItem value="airplay">AirPlay</SelectItem>
                <SelectItem value="dlna">DLNA/UPnP</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {form.type === "ha_media_player" && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.entity_id")}</Label>
              <Input value={form.entity_id ?? ""} onChange={e => set("entity_id", e.target.value)} placeholder="media_player.kjokken" />
            </div>
          )}

          {form.type === "esp32" && (
            <>
              <div>
                <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.host")}</Label>
                <Input value={form.host ?? ""} onChange={e => set("host", e.target.value)} placeholder="192.168.0.xxx" />
              </div>
              <div>
                <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.api_port")}</Label>
                <Input type="number" value={form.api_port ?? 6053} onChange={e => set("api_port", Number(e.target.value))} className="w-28" />
              </div>
            </>
          )}

          {form.type === "wyoming" && (
            <>
              <div>
                <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.host")}</Label>
                <Input value={form.host ?? ""} onChange={e => set("host", e.target.value)} placeholder="192.168.0.xxx" />
              </div>
              <div>
                <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.api_port")}</Label>
                <Input type="number" value={form.api_port ?? 10300} onChange={e => set("api_port", Number(e.target.value))} className="w-28" />
              </div>
              <div className="flex items-center gap-2 pt-1">
                <Switch checked={form.mic_enabled ?? false} onCheckedChange={v => set("mic_enabled", v)} />
                <span className="text-sm">{t("nodes.modal.mic_enabled")}</span>
              </div>
            </>
          )}

          {form.type === "chromecast" && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.host")}</Label>
              <Input value={form.host ?? ""} onChange={e => set("host", e.target.value)} placeholder="192.168.0.xxx" />
            </div>
          )}

          {form.type === "snapcast" && (
            <>
              <div>
                <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.host")}</Label>
                <Input value={form.host ?? ""} onChange={e => set("host", e.target.value)} placeholder="192.168.0.xxx" />
              </div>
              <div>
                <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.api_port")}</Label>
                <Input type="number" value={form.api_port ?? 1704} onChange={e => set("api_port", Number(e.target.value))} className="w-28" />
              </div>
            </>
          )}

          {form.type === "airplay" && (
            <div>
              <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.host")}</Label>
              <Input value={form.host ?? ""} onChange={e => set("host", e.target.value)} placeholder="192.168.0.xxx" />
            </div>
          )}

          {form.type === "dlna" && (
            <>
              <div>
                <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.host")}</Label>
                <Input value={form.host ?? ""} onChange={e => set("host", e.target.value)} placeholder="192.168.0.xxx" />
              </div>
              <div>
                <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.api_port")} ({t("common.optional")})</Label>
                <Input type="number" value={form.api_port ?? ""} onChange={e => set("api_port", e.target.value ? Number(e.target.value) : undefined)} className="w-28" placeholder="—" />
              </div>
            </>
          )}

          <div>
            <Label className="text-xs text-muted-foreground mb-1 block">{t("nodes.modal.default_user")}</Label>
            <Input value={form.default_user ?? ""} onChange={e => set("default_user", e.target.value)} placeholder="stian" />
          </div>

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

        {Object.keys(nodes).length === 0 ? (
          <p style={{ color: "#555", fontSize: 13 }}>{t("nodes.empty")}</p>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
            {Object.entries(nodes).map(([key, node]) => {
              const meta = TYPE_META[node.type] ?? DEFAULT_TYPE_META;
              const typeColor = meta.color;
              const topColor = node.enabled ? typeColor : "#2a2a2a";
              return (
                <div key={key} style={{ background: "#141414", border: "1px solid #252525", borderTop: `2px solid ${topColor}`, borderRadius: 10, padding: 16, display: "flex", flexDirection: "column", gap: 8, transition: "border-color 0.2s" }}>
                  <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
                    <div style={{ minWidth: 0 }}>
                      <p style={{ color: node.enabled ? "#e8e8e8" : "#666", fontSize: 14, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{key}</p>
                      {node.description && <p style={{ color: "#666", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.description}</p>}
                    </div>
                    <div style={{ flexShrink: 0 }}>
                      <Switch checked={node.enabled} onCheckedChange={() => toggleEnabled(key)} />
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
                  <div style={{ display: "flex", gap: 8 }}>
                    <Button variant="outline" size="sm" onClick={() => setEditKey(key)}>
                      <Pencil className="h-3 w-3 mr-1" /> {t("nodes.edit")}
                    </Button>
                    <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => handleDelete(key)}>
                      <Trash2 className="h-3 w-3 mr-1" /> {t("nodes.delete")}
                    </Button>
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
