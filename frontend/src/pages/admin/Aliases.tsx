import { useEffect, useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, CheckCircle2, XCircle, Plus, Trash2, Search } from "lucide-react";
import { apiGetAliases, apiPutAliases, type AliasesConfig } from "@/services/api";

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

// ── Aliases tab ───────────────────────────────────────────────────────────────

type AliasPair = { key: string; value: string };

function TabAliases({ data, onSave, ss }: { data: Record<string, string>; onSave: (d: Record<string, string>) => void; ss: ReturnType<typeof useSaveState> }) {
  const { t } = useTranslation();
  const [pairs, setPairs] = useState<AliasPair[]>([]);
  const [filter, setFilter] = useState("");
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [editPair, setEditPair] = useState<AliasPair>({ key: "", value: "" });

  useEffect(() => {
    setPairs(Object.entries(data).map(([key, value]) => ({ key, value })));
  }, [data]);

  const filtered = useMemo(() => {
    if (!filter.trim()) return pairs;
    const f = filter.toLowerCase();
    return pairs.filter(p => p.key.toLowerCase().includes(f) || p.value.toLowerCase().includes(f));
  }, [pairs, filter]);

  const commitEdit = () => {
    if (editIdx === null) return;
    const updated = [...pairs];
    updated[editIdx] = editPair;
    setPairs(updated);
    setEditIdx(null);
  };

  const addNew = () => {
    setPairs(p => [{ key: "", value: "" }, ...p]);
    setEditIdx(0);
    setEditPair({ key: "", value: "" });
  };

  const deleteAt = (idx: number) => {
    setPairs(p => p.filter((_, i) => i !== idx));
  };

  const save = () => {
    const obj: Record<string, string> = {};
    for (const { key, value } of pairs) {
      if (key.trim()) obj[key.trim()] = value.trim();
    }
    onSave(obj);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input value={filter} onChange={e => setFilter(e.target.value)} placeholder={t("aliases.search_ph")} className="pl-8 h-8 text-sm" />
        </div>
        <Button size="sm" onClick={addNew}><Plus className="h-3.5 w-3.5 mr-1" /> {t("common.add")}</Button>
      </div>

      <div className="border border-border rounded-lg overflow-hidden">
        <div className="grid grid-cols-[1fr_1fr_auto] bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground">
          <span>{t("aliases.col_key")}</span>
          <span>{t("aliases.col_entity")}</span>
          <span />
        </div>
        <div className="divide-y divide-border max-h-[520px] overflow-y-auto">
          {filtered.length === 0 && (
            <div className="px-3 py-4 text-sm text-muted-foreground">{t("aliases.empty")}</div>
          )}
          {filtered.map((pair, idx) => (
            <div key={idx} className="grid grid-cols-[1fr_1fr_auto] gap-2 px-3 py-2 items-center">
              {editIdx === idx ? (
                <>
                  <Input
                    value={editPair.key}
                    onChange={e => setEditPair(p => ({ ...p, key: e.target.value }))}
                    placeholder="norsk tekst"
                    className="h-7 text-xs"
                    autoFocus
                  />
                  <Input
                    value={editPair.value}
                    onChange={e => setEditPair(p => ({ ...p, value: e.target.value }))}
                    placeholder="entity_id"
                    className="h-7 text-xs font-mono"
                  />
                  <Button size="sm" className="h-7 px-2 text-xs" onClick={commitEdit}>OK</Button>
                </>
              ) : (
                <>
                  <button className="text-left text-sm truncate hover:text-foreground text-muted-foreground" onClick={() => { setEditIdx(idx); setEditPair(pair); }}>{pair.key || <em>tom</em>}</button>
                  <span className="text-xs font-mono text-muted-foreground truncate">{pair.value}</span>
                  <Button variant="ghost" size="sm" className="h-7 px-2 text-destructive hover:text-destructive" onClick={() => deleteAt(idx)}><Trash2 className="h-3 w-3" /></Button>
                </>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={ss.state === "saving"}>
          {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {t("aliases.save_btn")}
        </Button>
        <SaveFeedback state={ss.state} />
      </div>
      <p className="text-xs text-muted-foreground">{t("aliases.hot_reload_note")}</p>
    </div>
  );
}

// ── Rooms tab ─────────────────────────────────────────────────────────────────

function TabRooms({ data, onSave, ss }: { data: Record<string, string[]>; onSave: (d: Record<string, string[]>) => void; ss: ReturnType<typeof useSaveState> }) {
  const { t } = useTranslation();
  const [rooms, setRooms] = useState<Record<string, string[]>>({});
  const [newRoom, setNewRoom] = useState("");

  useEffect(() => { setRooms(data); }, [data]);

  const updateSynonyms = (room: string, val: string) => {
    setRooms(p => ({ ...p, [room]: val.split(",").map(s => s.trim()).filter(Boolean) }));
  };

  const addRoom = () => {
    const key = newRoom.trim();
    if (!key || rooms[key]) return;
    setRooms(p => ({ ...p, [key]: [] }));
    setNewRoom("");
  };

  const deleteRoom = (room: string) => {
    const { [room]: _, ...rest } = rooms;
    setRooms(rest);
  };

  const save = () => onSave(rooms);

  return (
    <div className="space-y-4">
      <div className="border border-border rounded-lg overflow-hidden">
        <div className="grid grid-cols-[180px_1fr_auto] bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground">
          <span>{t("aliases.room_col_room")}</span>
          <span>{t("aliases.room_col_synonyms")}</span>
          <span />
        </div>
        <div className="divide-y divide-border max-h-[480px] overflow-y-auto">
          {Object.entries(rooms).map(([room, synonyms]) => (
            <div key={room} className="grid grid-cols-[180px_1fr_auto] gap-2 px-3 py-2 items-center">
              <span className="text-sm font-medium truncate">{room}</span>
              <Input
                value={synonyms.join(", ")}
                onChange={e => updateSynonyms(room, e.target.value)}
                className="h-7 text-xs"
                placeholder="synonym1, synonym2"
              />
              <Button variant="ghost" size="sm" className="h-7 px-2 text-destructive hover:text-destructive" onClick={() => deleteRoom(room)}>
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
          ))}
        </div>
      </div>

      <div className="flex gap-2">
        <Input value={newRoom} onChange={e => setNewRoom(e.target.value)} placeholder={t("aliases.room_new_ph")} className="h-8 text-sm w-48" onKeyDown={e => e.key === "Enter" && addRoom()} />
        <Button size="sm" onClick={addRoom} disabled={!newRoom.trim()}><Plus className="h-3.5 w-3.5 mr-1" /> {t("common.add")}</Button>
      </div>

      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={ss.state === "saving"}>
          {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {t("aliases.room_save_btn")}
        </Button>
        <SaveFeedback state={ss.state} />
      </div>
    </div>
  );
}

// ── Room entities tab ─────────────────────────────────────────────────────────

function TabRoomEntities({ data, onSave, ss }: { data: Record<string, string[]>; onSave: (d: Record<string, string[]>) => void; ss: ReturnType<typeof useSaveState> }) {
  const { t } = useTranslation();
  const [roomEntities, setRoomEntities] = useState<Record<string, string[]>>({});
  const [newEntity, setNewEntity] = useState<Record<string, string>>({});

  useEffect(() => { setRoomEntities(data); }, [data]);

  const addEntity = (room: string) => {
    const val = (newEntity[room] ?? "").trim();
    if (!val) return;
    setRoomEntities(p => ({ ...p, [room]: [...(p[room] ?? []), val] }));
    setNewEntity(p => ({ ...p, [room]: "" }));
  };

  const removeEntity = (room: string, idx: number) => {
    setRoomEntities(p => ({ ...p, [room]: p[room].filter((_, i) => i !== idx) }));
  };

  const save = () => onSave(roomEntities);

  return (
    <div className="space-y-3">
      {Object.entries(roomEntities).map(([room, entities]) => (
        <details key={room} className="border border-border rounded-lg">
          <summary className="px-4 py-2.5 cursor-pointer text-sm font-medium hover:bg-muted/20 rounded-lg">
            {room} <span className="text-xs text-muted-foreground ml-1">({entities.length})</span>
          </summary>
          <div className="px-4 pb-3 pt-1 space-y-1.5">
            {entities.map((eid, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <span className="text-xs font-mono text-muted-foreground flex-1">{eid}</span>
                <Button variant="ghost" size="sm" className="h-6 px-1.5 text-destructive hover:text-destructive" onClick={() => removeEntity(room, idx)}>
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
            ))}
            <div className="flex gap-2 pt-1">
              <Input
                value={newEntity[room] ?? ""}
                onChange={e => setNewEntity(p => ({ ...p, [room]: e.target.value }))}
                placeholder="light.entity_id"
                className="h-7 text-xs font-mono"
                onKeyDown={e => e.key === "Enter" && addEntity(room)}
              />
              <Button size="sm" className="h-7 px-2" onClick={() => addEntity(room)}>
                <Plus className="h-3 w-3" />
              </Button>
            </div>
          </div>
        </details>
      ))}

      <div className="flex items-center gap-3 pt-2">
        <Button onClick={save} disabled={ss.state === "saving"}>
          {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {t("aliases.entities_save_btn")}
        </Button>
        <SaveFeedback state={ss.state} />
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Aliases() {
  const { t } = useTranslation();
  const [config, setConfig] = useState<AliasesConfig>({ aliases: {}, rooms: {}, room_entities: {} });
  const ssAliases      = useSaveState();
  const ssRooms        = useSaveState();
  const ssRoomEntities = useSaveState();

  useEffect(() => {
    apiGetAliases().then(d => setConfig({
      aliases: d.aliases ?? {},
      rooms: d.rooms ?? {},
      room_entities: d.room_entities ?? {},
    })).catch(() => {});
  }, []);

  const saveAliases = async (aliases: Record<string, string>) => {
    ssAliases.saving();
    try {
      await apiPutAliases({ aliases });
      setConfig(p => ({ ...p, aliases }));
      ssAliases.saved();
    } catch { ssAliases.error(); }
  };

  const saveRooms = async (rooms: Record<string, string[]>) => {
    ssRooms.saving();
    try {
      await apiPutAliases({ rooms });
      setConfig(p => ({ ...p, rooms }));
      ssRooms.saved();
    } catch { ssRooms.error(); }
  };

  const saveRoomEntities = async (room_entities: Record<string, string[]>) => {
    ssRoomEntities.saving();
    try {
      await apiPutAliases({ room_entities });
      setConfig(p => ({ ...p, room_entities }));
      ssRoomEntities.saved();
    } catch { ssRoomEntities.error(); }
  };

  return (
    <div>
      <h1>{t("aliases.title")}</h1>
      <p style={{ color: "#666", fontSize: 13, marginBottom: 24, marginTop: -16 }}>{t("aliases.description")}</p>

      <div className="admin-card" style={{ borderRadius: 12, padding: "20px 24px", borderTop: "3px solid #7c6aff" }}>
        <div style={{ color: "#ddd", fontSize: 15, fontWeight: 600, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: "#7c6aff", fontSize: 13 }}>⚙</span>
          aliases.yaml
        </div>
        <p style={{ color: "#666", fontSize: 13, marginBottom: 20 }}>{t("aliases.card_description")}</p>
        <Tabs defaultValue="aliases">
          <TabsList className="mb-4">
            <TabsTrigger value="aliases">{t("aliases.tab_aliases")} ({Object.keys(config.aliases).length})</TabsTrigger>
            <TabsTrigger value="rooms">{t("aliases.tab_rooms")} ({Object.keys(config.rooms).length})</TabsTrigger>
            <TabsTrigger value="room_entities">{t("aliases.tab_room_entities")} ({Object.keys(config.room_entities).length})</TabsTrigger>
          </TabsList>
          <TabsContent value="aliases">
            <TabAliases data={config.aliases} onSave={saveAliases} ss={ssAliases} />
          </TabsContent>
          <TabsContent value="rooms">
            <TabRooms data={config.rooms} onSave={saveRooms} ss={ssRooms} />
          </TabsContent>
          <TabsContent value="room_entities">
            <TabRoomEntities data={config.room_entities} onSave={saveRoomEntities} ss={ssRoomEntities} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
