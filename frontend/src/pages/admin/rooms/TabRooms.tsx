// Rom og aliaser → Rom (P137): places and floors (from Home Assistant and/or KTSB), the things KTSB knows in each
// place with their location note, the import mode, and the HA sync. Edits are buffered in `raw` (rooms.yaml) and
// saved with one «Lagre rom»; linking and syncing act at once.
import { useEffect, useMemo, useState } from "react";
import { isAxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Loader2, Plus, Trash2, Search, ChevronDown, ChevronRight, RefreshCw, Pencil, EyeOff, Eye, Link2,
} from "lucide-react";
import {
  apiGetHaTree, apiLinkPlace, apiPutRooms, apiSyncRooms,
  type HaTree, type HaTreeArea, type ImportMode, type PlaceView, type RoomsRaw, type RoomsView,
} from "@/services/api";
import { apiErrorDetail } from "@/lib/apiError";
import {
  HA_COLOR, WARN, badge, card, cardHeader, cardRow, chip, dlg, domainOf, parseList, pill, sectionLabel,
  smallBtn, useSaveState,
} from "./shared";
import { DomainBadge, MissingBadge, SaveFeedback, SourceBadge } from "./SharedUi";

const NO_FLOOR = "__no_floor__";
const NEW_FLOOR = "__new_floor__";
const MANUAL_SOURCES = ["mqtt", "node", "frigate", "manual"] as const;
const COMMON_DOMAINS = ["light", "switch", "climate", "cover", "fan", "media_player", "sensor", "binary_sensor", "vacuum", "lock"];

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x ?? {}));
const slug = (name: string) => name.trim().toLowerCase().replace(/\s+/g, "_");

// ── Place dialog (new KTSB place / edit synonyms, floor, hidden) ─────────────

type PlaceDraft = { id: string; name: string; floor: string; newFloor: string; synonyms: string; hidden: boolean };

function PlaceDialog({ mode, place, view, onSubmit, onClose }: {
  mode: "create" | "edit";
  place: PlaceView | null;
  view: RoomsView;
  onSubmit: (d: PlaceDraft) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const isHa = place?.source === "ha";
  const [d, setD] = useState<PlaceDraft>({
    id: place?.id ?? "", name: place?.name ?? "", floor: place?.floor || "", newFloor: "",
    synonyms: (place?.synonyms ?? []).join(", "), hidden: place?.hidden ?? false,
  });
  const [error, setError] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isHa && !d.name.trim()) return setError(t("rooms.dlg_err_name"));
    if (mode === "create" && view.places.some(p => p.id === slug(d.name))) return setError(t("rooms.dlg_err_taken"));
    if (d.floor === NEW_FLOOR && !d.newFloor.trim()) return setError(t("rooms.dlg_err_floor"));
    onSubmit({ ...d, id: mode === "create" ? slug(d.name) : d.id });
  };

  const haFloor = isHa ? view.floors.find(f => f.id === place?.floor)?.name : "";

  return (
    <div style={dlg.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <form style={dlg.box} onSubmit={submit}>
        <div style={dlg.title}>{mode === "create" ? t("rooms.dlg_title_new") : t("rooms.dlg_title_edit")}</div>

        <label style={dlg.label}>{t("rooms.dlg_name")}</label>
        {isHa ? (
          <>
            <div style={dlg.readonly}>{place?.name}</div>
            <div style={dlg.hint}>{t("rooms.dlg_name_ha_hint")}</div>
          </>
        ) : (
          <>
            <input style={dlg.input} value={d.name} autoFocus placeholder={t("rooms.dlg_name_ph")}
              onChange={e => { setD(p => ({ ...p, name: e.target.value })); setError(""); }} />
            <div style={dlg.hint}>{t("rooms.dlg_name_hint")}</div>
          </>
        )}

        <label style={dlg.label}>{t("rooms.dlg_floor")}</label>
        {isHa ? (
          <>
            <div style={dlg.readonly}>{haFloor || t("rooms.no_floor")}</div>
            <div style={dlg.hint}>{t("rooms.dlg_floor_ha_hint")}</div>
          </>
        ) : (
          <>
            <select style={dlg.input} value={d.floor} onChange={e => setD(p => ({ ...p, floor: e.target.value }))}>
              <option value="">{t("rooms.no_floor")}</option>
              {view.floors.map(f => <option key={f.id} value={f.id}>{f.name}{f.source === "ha" ? " (HA)" : ""}</option>)}
              <option value={NEW_FLOOR}>{t("rooms.dlg_floor_new")}</option>
            </select>
            {d.floor === NEW_FLOOR && (
              <input style={{ ...dlg.input, marginTop: 6 }} value={d.newFloor} placeholder={t("rooms.dlg_floor_new_ph")}
                onChange={e => { setD(p => ({ ...p, newFloor: e.target.value })); setError(""); }} />
            )}
            <div style={dlg.hint} />
          </>
        )}

        <label style={dlg.label}>{t("rooms.dlg_synonyms")}</label>
        <input style={dlg.input} value={d.synonyms} placeholder="stua, dagligstue"
          onChange={e => setD(p => ({ ...p, synonyms: e.target.value }))} />
        <div style={dlg.hint}>
          {t("rooms.dlg_synonyms_hint")}
          {isHa && place && place.ha_aliases.length > 0 && <> {t("rooms.dlg_ha_aliases", { list: place.ha_aliases.join(", ") })}</>}
        </div>

        <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#bbb", fontSize: 13, marginBottom: 14, cursor: "pointer" }}>
          <input type="checkbox" checked={d.hidden} onChange={e => setD(p => ({ ...p, hidden: e.target.checked }))} />
          {t("rooms.dlg_hidden")}
        </label>

        {error && <div style={dlg.err}>{error}</div>}
        <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
          <button type="submit" style={dlg.btn("#646cff")}>{mode === "create" ? t("common.add") : t("aliases.dlg_apply")}</button>
          <button type="button" style={{ ...dlg.btn("transparent"), border: "1px solid #333", color: "#aaa" }} onClick={onClose}>{t("common.cancel")}</button>
        </div>
      </form>
    </div>
  );
}

// ── Add-thing dialog (pick from HA, or add by hand) ──────────────────────────

type NewThing = { ref: string; name: string; note: string; place: string };

function AddThingDialog({ place, view, tree, onSubmit, onClose }: {
  place: PlaceView;
  view: RoomsView;
  tree: HaTree | null;
  onSubmit: (things: NewThing[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const canHa = place.source === "ha" && !!tree;
  const [tab, setTab] = useState<"ha" | "manual">(canHa ? "ha" : "manual");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState("");
  const [src, setSrc] = useState<(typeof MANUAL_SOURCES)[number]>("manual");
  const [manual, setManual] = useState({ ref: "", name: "", note: "" });
  const [error, setError] = useState("");
  const known = useMemo(() => new Set(view.things.map(x => x.ref)), [view.things]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Devices of this place; with a search, every HA place (and unplaced entities) is searched.
  const areas: HaTreeArea[] = useMemo(() => {
    if (!tree) return [];
    const q = search.trim().toLowerCase();
    const all = [...tree.areas, tree.unplaced];
    const pool = q ? all : all.filter(a => a.id === place.id);
    return pool.map(a => ({
      ...a,
      devices: a.devices.map(dv => ({
        ...dv,
        entities: dv.entities.filter(e => (showAll || e.user_facing) &&
          (!q || e.name.toLowerCase().includes(q) || e.ref.includes(q) || dv.name.toLowerCase().includes(q))),
      })).filter(dv => dv.entities.length > 0),
    })).filter(a => a.devices.length > 0);
  }, [tree, place.id, search, showAll]);

  const toggle = (ref: string) => setPicked(prev => {
    const next = new Set(prev);
    if (next.has(ref)) next.delete(ref); else next.add(ref);
    return next;
  });
  const toggleDevice = (refs: string[]) => setPicked(prev => {
    const free = refs.filter(r => !known.has(r));
    const allOn = free.every(r => prev.has(r));
    const next = new Set(prev);
    free.forEach(r => (allOn ? next.delete(r) : next.add(r)));
    return next;
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (tab === "ha") {
      if (picked.size === 0) return setError(t("rooms.add_err_none"));
      onSubmit([...picked].map(ref => ({ ref, name: "", note: "", place: "" })));
      return;
    }
    const raw = manual.ref.trim();
    if (!raw) return setError(t("rooms.add_err_ref"));
    const ref = src === "manual" ? `manual:${raw}` : raw.startsWith(`${src}:`) ? raw : `${src}:${raw}`;
    if (known.has(ref)) return setError(t("rooms.add_err_known"));
    onSubmit([{ ref, name: manual.name.trim(), note: manual.note.trim(), place: place.id }]);
  };

  return (
    <div style={dlg.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <form style={{ ...dlg.box, maxWidth: 620 }} onSubmit={submit}>
        <div style={dlg.title}>{t("rooms.add_title", { place: place.name })}</div>
        <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
          {canHa && <button type="button" style={pill(tab === "ha")} onClick={() => setTab("ha")}>{t("rooms.add_tab_ha")}</button>}
          <button type="button" style={pill(tab === "manual")} onClick={() => setTab("manual")}>{t("rooms.add_tab_manual")}</button>
        </div>

        {tab === "ha" ? (
          <>
            <div className="relative mb-2">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input value={search} onChange={e => setSearch(e.target.value)} placeholder={t("rooms.add_search_ph")} className="pl-8 h-8 text-sm" />
            </div>
            <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#999", fontSize: 12, marginBottom: 10, cursor: "pointer" }}>
              <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
              {t("rooms.add_show_all")}
            </label>
            <div style={{ border: "1px solid #222", borderRadius: 8, maxHeight: 360, overflowY: "auto", background: "#0f0f0f" }}>
              {areas.length === 0 && <div style={{ color: "#888", fontSize: 13, padding: 14 }}>{t("rooms.add_empty")}</div>}
              {areas.map(a => (
                <div key={a.id || "_unplaced"}>
                  {(search.trim() !== "") && (
                    <div style={{ color: "#777", fontSize: 11, padding: "6px 12px 0", textTransform: "uppercase", letterSpacing: 0.5 }}>
                      {a.id ? a.name : t("rooms.add_unplaced")}
                    </div>
                  )}
                  {a.devices.map(dv => {
                    const refs = dv.entities.map(e => e.ref);
                    return (
                      <div key={(a.id || "_") + dv.id} style={{ padding: "6px 12px", borderTop: "1px solid #1a1a1a" }}>
                        <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#ddd", fontSize: 13, fontWeight: 600, cursor: "pointer" }}>
                          <input type="checkbox"
                            checked={refs.filter(r => !known.has(r)).length > 0 && refs.filter(r => !known.has(r)).every(r => picked.has(r))}
                            onChange={() => toggleDevice(refs)} />
                          {dv.name || t("rooms.add_no_device")} <span style={{ color: "#666", fontWeight: 400 }}>{dv.entities.length}</span>
                        </label>
                        {dv.entities.map(e => (
                          <label key={e.ref} style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0 3px 22px", fontSize: 12, cursor: known.has(e.ref) ? "default" : "pointer" }}>
                            <input type="checkbox" disabled={known.has(e.ref)} checked={known.has(e.ref) || picked.has(e.ref)} onChange={() => toggle(e.ref)} />
                            <span style={{ width: 92, flexShrink: 0 }}><DomainBadge domain={e.domain} /></span>
                            <span style={{ color: "#ddd", flex: 1, minWidth: 0 }} className="truncate">{e.name}</span>
                            <span style={{ color: "#666", fontFamily: "monospace", maxWidth: "40%" }} className="truncate hidden sm:inline">{e.ref}</span>
                            {e.exposed && <span style={badge(HA_COLOR)} title={t("rooms.add_exposed_hint")}>Assist</span>}
                            {e.orphan && <MissingBadge missing="orphan" />}
                          </label>
                        ))}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
            <div style={{ ...dlg.hint, marginTop: 8 }}>{t("rooms.add_ha_hint")}</div>
          </>
        ) : (
          <>
            <label style={dlg.label}>{t("rooms.add_source")}</label>
            <select style={dlg.input} value={src} onChange={e => setSrc(e.target.value as (typeof MANUAL_SOURCES)[number])}>
              {MANUAL_SOURCES.map(s => <option key={s} value={s}>{t(`rooms.source.${s}`)}</option>)}
            </select>
            <div style={dlg.hint}>{t(`rooms.add_source_hint_${src}`)}</div>
            <label style={dlg.label}>{t("rooms.add_ref")}</label>
            <input style={{ ...dlg.input, fontFamily: "monospace" }} value={manual.ref}
              placeholder={src === "mqtt" ? "zigbee2mqtt/bod_dor" : src === "node" ? "kjokken" : src === "frigate" ? "utekamera_ost" : "lampe_hjornet"}
              onChange={e => { setManual(m => ({ ...m, ref: e.target.value })); setError(""); }} />
            <div style={dlg.hint} />
            <label style={dlg.label}>{t("rooms.add_name")}</label>
            <input style={dlg.input} value={manual.name} onChange={e => setManual(m => ({ ...m, name: e.target.value }))} />
            <div style={dlg.hint} />
            <label style={dlg.label}>{t("rooms.note")}</label>
            <input style={dlg.input} value={manual.note} placeholder={t("rooms.note_ph")} onChange={e => setManual(m => ({ ...m, note: e.target.value }))} />
            <div style={dlg.hint}>{t("rooms.note_hint")}</div>
          </>
        )}

        {error && <div style={dlg.err}>{error}</div>}
        <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
          <button type="submit" style={dlg.btn("#646cff")}>{t("common.add")}{tab === "ha" && picked.size > 0 ? ` (${picked.size})` : ""}</button>
          <button type="button" style={{ ...dlg.btn("transparent"), border: "1px solid #333", color: "#aaa" }} onClick={onClose}>{t("common.cancel")}</button>
        </div>
      </form>
    </div>
  );
}

// ── The tab ───────────────────────────────────────────────────────────────────

export default function TabRooms({ view, onView }: { view: RoomsView; onView: (v: RoomsView) => void }) {
  const { t } = useTranslation();
  const ss = useSaveState();
  const [raw, setRaw] = useState<RoomsRaw>(() => clone(view.raw));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"" | "sync" | "link">("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [placeDlg, setPlaceDlg] = useState<{ mode: "create" | "edit"; place: PlaceView | null } | null>(null);
  const [addDlg, setAddDlg] = useState<PlaceView | null>(null);
  const [tree, setTree] = useState<HaTree | null>(null);

  // A server view after save/link/sync becomes the new starting point (unsaved edits are only ever local).
  const accept = (v: RoomsView) => { setRaw(clone(v.raw)); setDirty(false); onView(v); };
  useEffect(() => { if (view.ha.connected) apiGetHaTree().then(setTree).catch(() => setTree(null)); }, [view.ha.connected, view.ha.last_sync]);

  const settings = { import_mode: "rooms_only" as ImportMode, import_label: "", import_domains: [] as string[], ...raw.settings };
  const edit = (fn: (r: RoomsRaw) => void) => { setRaw(prev => { const next = clone(prev); fn(next); return next; }); setDirty(true); };

  // What the page shows: the server's view for structure, local raw for pending names/synonyms/notes.
  const places = view.places.map(p => {
    const ov = raw.places?.[p.id];
    return {
      ...p,
      name: p.source === "manual" ? ov?.name ?? p.name : p.name,
      floor: p.source === "manual" ? ov?.floor ?? p.floor : p.floor,
      synonyms: ov?.synonyms ?? (ov ? [] : p.synonyms),
      hidden: ov?.hidden ?? (ov ? false : p.hidden),
    };
  }).filter(p => p.source === "ha" || raw.places?.[p.id]);
  // Places created locally and not saved yet
  for (const [id, ov] of Object.entries(raw.places ?? {})) {
    if (!places.some(p => p.id === id)) {
      places.push({ id, name: ov.name ?? id, source: "manual", floor: ov.floor ?? "", synonyms: ov.synonyms ?? [], ha_aliases: [],
        hidden: !!ov.hidden, template_example: !!ov.template_example, things: 0, unregistered: 0 });
    }
  }
  const things = view.things.filter(th => !th.explicit || raw.things?.[th.ref]);
  const localThings = Object.entries(raw.things ?? {}).filter(([ref]) => !view.things.some(th => th.ref === ref));
  const floorName = (id: string) => view.floors.find(f => f.id === id)?.name ?? raw.floors?.[id]?.name ?? id;
  const floorIds = [...view.floors.map(f => f.id), ...Object.keys(raw.floors ?? {}).filter(id => !view.floors.some(f => f.id === id))];

  const visible = places.filter(p => !p.hidden);
  const hidden = places.filter(p => p.hidden);
  const groups = [
    ...floorIds.map(fid => ({ id: fid, name: floorName(fid), places: visible.filter(p => p.floor === fid) })),
    { id: NO_FLOOR, name: t("rooms.no_floor"), places: visible.filter(p => !p.floor || !floorIds.includes(p.floor)) },
  ].filter(g => g.places.length > 0);

  const haAreaOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of tree?.areas ?? []) for (const dv of a.devices) for (const e of dv.entities) m.set(e.ref, a.id);
    return m;
  }, [tree]);
  const haNameOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of [...(tree?.areas ?? []), ...(tree ? [tree.unplaced] : [])]) for (const dv of a.devices) for (const e of dv.entities) m.set(e.ref, e.name);
    return m;
  }, [tree]);
  const thingsAt = (pid: string) => [
    ...things.filter(th => th.place === pid),
    ...localThings.filter(([ref, v]) => (v.place || haAreaOf.get(ref) || "") === pid).map(([ref, v]) => ({
      ref, name: v.name || haNameOf.get(ref) || ref, source: ref.includes(":") ? ref.split(":")[0] as "manual" : "ha" as const, place: pid,
      note: v.note ?? "", domain: domainOf(ref), explicit: true, in_ha: false,
    })),
  ];
  const noteOf = (ref: string, fallback: string) => raw.things?.[ref]?.note ?? fallback;

  const toggleOpen = (id: string) => setOpen(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const save = async () => {
    ss.saving(); setError("");
    try {
      accept(await apiPutRooms(raw));
      ss.saved();
    } catch (err) {
      const coll = isAxiosError(err)
        ? (err.response?.data?.detail as { collisions?: Record<string, string[]> } | undefined)?.collisions
        : undefined;
      setError(coll ? t("rooms.err_collisions", { list: Object.entries(coll).map(([w, ids]) => `«${w}» (${ids.join(", ")})`).join("; ") })
        : apiErrorDetail(err) ?? t("common.error"));
      ss.error();
    }
  };

  const sync = async () => {
    setBusy("sync"); setError("");
    try {
      const v = await apiSyncRooms();
      if (dirty) onView(v); else accept(v);
    } catch { setError(t("rooms.err_sync")); } finally { setBusy(""); }
  };

  const link = async (manual: string, ha: string) => {
    if (dirty) { setError(t("rooms.err_save_first")); return; }
    setBusy("link"); setError("");
    try { accept(await apiLinkPlace(manual, ha)); } catch { setError(t("common.error")); } finally { setBusy(""); }
  };

  const submitPlace = (d: PlaceDraft) => {
    edit(r => {
      r.places = r.places ?? {};
      let floor = d.floor;
      if (floor === NEW_FLOOR) {
        floor = slug(d.newFloor);
        r.floors = r.floors ?? {};
        r.floors[floor] = { name: d.newFloor.trim() };
      }
      const prev = r.places[d.id] ?? {};
      const isHa = view.places.some(p => p.id === d.id && p.source === "ha");
      r.places[d.id] = isHa
        ? { ...prev, synonyms: parseList(d.synonyms), hidden: d.hidden }
        : { ...prev, name: d.name.trim(), floor, synonyms: parseList(d.synonyms), hidden: d.hidden };
    });
    setOpen(prev => new Set(prev).add(d.id));
    setPlaceDlg(null);
  };

  const submitThings = (list: NewThing[]) => {
    edit(r => {
      r.things = r.things ?? {};
      for (const th of list) {
        r.things[th.ref] = { ...(th.place ? { place: th.place } : {}), ...(th.note ? { note: th.note } : {}), ...(th.name ? { name: th.name } : {}) };
      }
    });
    setAddDlg(null);
  };

  const removeThing = (ref: string) => edit(r => { if (r.things) delete r.things[ref]; });
  const setNote = (ref: string, note: string) => edit(r => {
    r.things = r.things ?? {};
    r.things[ref] = { ...(r.things[ref] ?? {}), note };
  });
  const removePlace = (id: string) => {
    if (!window.confirm(t("rooms.confirm_delete_place", { name: id }))) return;
    edit(r => {
      if (r.places) delete r.places[id];
      for (const th of Object.values(r.things ?? {})) if (th.place === id) th.place = "";
    });
  };

  const setMode = (mode: ImportMode) => edit(r => { r.settings = { ...settings, import_mode: mode }; });
  const toggleDomain = (dom: string) => edit(r => {
    const cur = new Set(settings.import_domains);
    if (cur.has(dom)) cur.delete(dom); else cur.add(dom);
    r.settings = { ...settings, import_domains: [...cur] };
  });

  const ha = view.ha;
  const examples = places.filter(p => p.template_example);

  return (
    <div className="space-y-4">
      {/* Home Assistant status */}
      <div style={{ ...card(ha.connected ? HA_COLOR : null), marginBottom: 0 }}>
        <div style={{ ...cardHeader, gap: 12 }}>
          <span style={{ width: 8, height: 8, borderRadius: 4, background: ha.connected ? HA_COLOR : ha.state === "error" ? "#ff6b6b" : "#666" }} />
          <span style={{ color: "#eee", fontWeight: 700, fontSize: 14 }}>Home Assistant</span>
          <span style={{ color: "#999", fontSize: 12 }}>
            {ha.connected
              ? t("rooms.ha_connected", { areas: ha.counts.areas ?? view.places.filter(p => p.source === "ha").length, floors: ha.counts.floors ?? view.floors.length, entities: ha.counts.entities ?? "?" })
              : ha.state === "error" ? t("rooms.ha_error", { error: ha.error }) : t("rooms.ha_not_connected")}
            {ha.last_sync && <> · {t("rooms.ha_last_sync", { time: new Date(ha.last_sync).toLocaleString() })}</>}
          </span>
          <button style={{ ...smallBtn, marginLeft: "auto" }} onClick={sync} disabled={busy !== ""}>
            {busy === "sync" ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} {t("rooms.sync_now")}
          </button>
        </div>
      </div>

      {/* Import mode */}
      <div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <span style={sectionLabel}>{t("rooms.import_label")}</span>
          {view.import_modes.map(m => (
            <button key={m} style={pill(settings.import_mode === m)} onClick={() => setMode(m)}>{t(`rooms.mode.${m}`)}</button>
          ))}
        </div>
        <p style={{ color: "#888", fontSize: 12, margin: "6px 0 0" }}>{t(`rooms.mode_hint.${settings.import_mode}`)}</p>
        {settings.import_mode === "label" && (
          <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ color: "#aaa", fontSize: 12 }}>{t("rooms.import_label_pick")}</span>
            <select style={{ ...dlg.input, width: 220, marginBottom: 0, padding: "5px 10px", fontSize: 13 }} value={settings.import_label}
              onChange={e => edit(r => { r.settings = { ...settings, import_label: e.target.value }; })}>
              <option value="">—</option>
              {(tree?.labels ?? []).map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            {(tree?.labels ?? []).length === 0 && <span style={{ color: WARN, fontSize: 12 }}>{t("rooms.no_labels")}</span>}
          </div>
        )}
        {settings.import_mode === "all" && (
          <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <span style={{ color: "#aaa", fontSize: 12 }}>{t("rooms.import_domains")}</span>
            {COMMON_DOMAINS.map(dom => (
              <button key={dom} style={pill(settings.import_domains.includes(dom))} onClick={() => toggleDomain(dom)}>
                {t(`aliases.domain.${dom}`, { defaultValue: dom })}
              </button>
            ))}
            <span style={{ color: "#777", fontSize: 12 }}>{settings.import_domains.length === 0 ? t("rooms.import_domains_all") : ""}</span>
          </div>
        )}
      </div>

      {/* Hints: similar places, old template examples */}
      {(view.similar.length > 0 || examples.length > 0) && (
        <div style={{ ...card(WARN), marginBottom: 0 }}>
          <div style={{ ...cardHeader, color: WARN, fontWeight: 700, fontSize: 13 }}>{t("rooms.hints_title")}</div>
          {view.similar.map(s => (
            <div key={s.manual} style={cardRow}>
              <span style={{ color: "#ddd", fontSize: 13, flex: 1 }}>
                {t("rooms.hint_similar", { manual: view.places.find(p => p.id === s.manual)?.name ?? s.manual, ha: view.places.find(p => p.id === s.ha)?.name ?? s.ha })}
              </span>
              <button style={smallBtn} onClick={() => link(s.manual, s.ha)} disabled={busy !== ""}><Link2 size={13} /> {t("rooms.link")}</button>
            </div>
          ))}
          {examples.map(p => (
            <div key={p.id} style={cardRow}>
              <span style={{ color: "#ddd", fontSize: 13, flex: 1 }}>{t("rooms.hint_example", { name: p.id })}</span>
              <button style={{ ...smallBtn, color: "#ff6b6b" }} onClick={() => removePlace(p.id)}><Trash2 size={13} /> {t("rooms.delete")}</button>
            </div>
          ))}
        </div>
      )}

      {/* Places by floor */}
      <div className="flex items-center gap-2">
        <span style={{ color: "#888", fontSize: 12 }}>
          <span style={{ color: "#a78bfa", fontWeight: 600 }}>{visible.length}</span> {t("rooms.stat_places")} ·{" "}
          <span style={{ color: "#ddd", fontWeight: 600 }}>{view.floors.length}</span> {t("rooms.stat_floors")} ·{" "}
          <span style={{ color: "#ddd", fontWeight: 600 }}>{things.length + localThings.length}</span> {t("rooms.stat_things")}
        </span>
        <Button size="sm" className="ml-auto" onClick={() => setPlaceDlg({ mode: "create", place: null })}><Plus className="h-3.5 w-3.5 mr-1" /> {t("rooms.new_place")}</Button>
      </div>

      {groups.map(g => (
        <div key={g.id}>
          <div style={{ ...sectionLabel, fontSize: 11, margin: "14px 0 6px", color: "#999" }}>{g.name}</div>
          {g.places.map(p => {
            const list = thingsAt(p.id);
            const isOpen = open.has(p.id);
            return (
              <div key={p.id} style={card()}>
                <div style={{ ...cardHeader, cursor: "pointer" }} onClick={() => toggleOpen(p.id)}>
                  <span style={{ color: "#777", display: "flex" }}>{isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</span>
                  <span style={{ color: "#eee", fontSize: 14, fontWeight: 700 }}>{p.name}</span>
                  <SourceBadge source={p.source} />
                  <span style={{ color: "#777", fontSize: 12 }}>{list.length}</span>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {[...p.ha_aliases, ...p.synonyms].map(s => <span key={s} style={chip}>{s}</span>)}
                  </div>
                  <div style={{ display: "flex", gap: 6, marginLeft: "auto", alignItems: "center" }}>
                    {p.unregistered > 0 && <span style={badge(WARN)} title={t("rooms.unregistered_hint")}>{t("rooms.unregistered", { count: p.unregistered })}</span>}
                    <button style={smallBtn} title={t("rooms.edit_place")} onClick={e => { e.stopPropagation(); setPlaceDlg({ mode: "edit", place: p }); }}><Pencil size={13} /></button>
                    <button style={smallBtn} title={t("rooms.add_thing")} onClick={e => { e.stopPropagation(); setAddDlg(p); }}><Plus size={13} /></button>
                  </div>
                </div>
                {isOpen && (
                  <div style={{ background: "#0f0f0f" }}>
                    {list.length === 0 && <div style={{ ...cardRow, color: "#777", fontSize: 13 }}>{t("rooms.place_empty")}</div>}
                    {list.map(th => (
                      <div key={th.ref} style={cardRow}>
                        <div style={{ width: 92, flexShrink: 0 }}>{th.source === "ha" ? <DomainBadge domain={th.domain} /> : <SourceBadge source={th.source} />}</div>
                        <span style={{ flex: "1 1 180px", minWidth: 0, color: "#e6e6e6", fontSize: 13 }} className="truncate">{th.name}</span>
                        <span style={{ flex: "1 1 200px", minWidth: 0, color: "#888", fontFamily: "monospace", fontSize: 12 }} className="truncate">{th.ref}</span>
                        <MissingBadge missing={"missing" in th ? th.missing : ""} />
                        <input
                          style={{ flex: "1 1 200px", minWidth: 140, padding: "4px 8px", borderRadius: 6, border: "1px solid #262626", background: "#121212", color: "#ccc", fontSize: 12 }}
                          placeholder={t("rooms.note_ph")}
                          value={noteOf(th.ref, th.note)}
                          onChange={e => setNote(th.ref, e.target.value)}
                        />
                        {th.explicit
                          ? <Button variant="ghost" size="sm" className="h-7 px-2 text-destructive hover:text-destructive" title={t("rooms.remove_thing")} onClick={() => removeThing(th.ref)}><Trash2 className="h-3 w-3" /></Button>
                          : <span style={badge("#888")} title={t("rooms.imported_hint")}>{t("rooms.imported")}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}

      {hidden.length > 0 && (
        <details>
          <summary style={{ color: "#888", fontSize: 12, cursor: "pointer", margin: "10px 0" }}>{t("rooms.hidden_places", { count: hidden.length })}</summary>
          {hidden.map(p => (
            <div key={p.id} style={{ ...cardRow, border: "1px solid #1c1c1c", borderRadius: 8, marginBottom: 6 }}>
              <EyeOff size={13} color="#666" />
              <span style={{ color: "#aaa", fontSize: 13, flex: 1 }}>{p.name} <SourceBadge source={p.source} /></span>
              <button style={smallBtn} onClick={() => edit(r => { r.places = r.places ?? {}; r.places[p.id] = { ...(r.places[p.id] ?? {}), hidden: false }; })}><Eye size={13} /> {t("rooms.show")}</button>
              {p.source === "manual" && <button style={{ ...smallBtn, color: "#ff6b6b" }} onClick={() => removePlace(p.id)}><Trash2 size={13} /></button>}
            </div>
          ))}
        </details>
      )}

      {error && <div style={{ color: "#ff6b6b", fontSize: 13 }}>{error}</div>}
      {/* One save for the whole page (places, things, aliases) — sticky while something is unsaved, so the button
          cannot be scrolled out of sight (2026-10-18: lights added but never saved; «Lagre rom» read as rooms
          only). */}
      <div
        className="flex items-center gap-3"
        style={dirty ? { position: "sticky", bottom: 0, zIndex: 20, background: "#141414", padding: "10px 0", borderTop: `1px solid ${WARN}66` } : undefined}
      >
        <Button onClick={save} disabled={ss.state === "saving"}>
          {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {t("rooms.save")}
        </Button>
        {dirty && ss.state === "idle" && <span style={badge(WARN)}>{t("aliases.unsaved")}</span>}
        <SaveFeedback state={ss.state} />
      </div>
      <p style={{ color: "#888", fontSize: 12 }}>{t("rooms.footer_note")}</p>

      {placeDlg && <PlaceDialog mode={placeDlg.mode} place={placeDlg.place} view={view} onSubmit={submitPlace} onClose={() => setPlaceDlg(null)} />}
      {addDlg && <AddThingDialog place={addDlg} view={view} tree={tree} onSubmit={submitThings} onClose={() => setAddDlg(null)} />}
    </div>
  );
}
