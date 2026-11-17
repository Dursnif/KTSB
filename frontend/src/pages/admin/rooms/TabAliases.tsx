// Rom og aliaser → Aliaser (N28, P137): the names people say → the thing they mean, grouped by the place the
// register gives that thing. The room choice in the dialog writes to rooms.yaml (a thing outside HA, or an HA entity
// HA has not placed); an HA entity HA has placed shows HA's room read-only (P137 decision 18). Adding an alias also
// registers the thing, so Kåre knows it. Pending room changes are kept as a delta and merged into the latest
// rooms.yaml at save time, so this tab never overwrites what the Rom tab saved.
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, Plus, Trash2, Search, ChevronDown, ChevronRight } from "lucide-react";
import type { RoomsRaw, RoomsView } from "@/services/api";
import {
  HA_COLOR, WARN, badge, capitalize, card, cardHeader, cardRow, chip, dlg, domainColor, domainOf, pill,
  sectionLabel, smallBtn, useSaveState,
} from "./shared";
import { DomainBadge, MissingBadge, SaveFeedback } from "./SharedUi";

// Stable id per row — edit/delete must never use the index in a filtered list.
type AliasPair = { id: number; key: string; value: string };

const UNASSIGNED = "__unassigned__";
const NEW_PLACE = "__new_place__";
const slug = (name: string) => name.trim().toLowerCase().replace(/\s+/g, "_");

type Delta = { things: Record<string, { place?: string }>; places: Record<string, { name: string }> };

type AliasDraft = { key: string; value: string; place: string; newPlaceName: string };

function AliasDialog({ mode, initial, view, delta, knownEntities, takenKeys, onSubmit, onClose }: {
  mode: "create" | "edit";
  initial: AliasDraft;
  view: RoomsView;
  delta: Delta;
  knownEntities: string[];
  takenKeys: Set<string>;
  onSubmit: (d: AliasDraft) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [d, setD] = useState<AliasDraft>(initial);
  const [error, setError] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const value = d.value.trim();
  const domain = domainOf(value);
  const haPlace = view.refs[value]?.ha_place || (view.things.find(x => x.ref === value && x.in_ha)?.place ?? "");
  const haPlaceName = view.places.find(p => p.id === haPlace)?.name ?? haPlace;
  const haPlaces = view.places.filter(p => p.source === "ha" && !p.hidden);
  const ownPlaces = [
    ...view.places.filter(p => p.source === "manual" && !p.hidden),
    ...Object.entries(delta.places).map(([id, p]) => ({ id, name: p.name })),
  ];

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const key = d.key.trim();
    if (!key) return setError(t("aliases.dlg_err_key_empty"));
    if (takenKeys.has(key.toLowerCase())) return setError(t("aliases.dlg_err_key_taken"));
    if (!value) return setError(t("aliases.dlg_err_entity"));
    if (d.place === NEW_PLACE) {
      if (!d.newPlaceName.trim()) return setError(t("aliases.dlg_err_room_empty"));
      if (view.places.some(p => p.id === slug(d.newPlaceName)) || delta.places[slug(d.newPlaceName)]) {
        return setError(t("aliases.dlg_err_room_taken"));
      }
    }
    onSubmit({ ...d, key, value, place: haPlace ? "" : d.place });
  };

  return (
    <div style={dlg.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <form style={dlg.box} onSubmit={submit}>
        <div style={dlg.title}>{mode === "create" ? t("aliases.dlg_title_new") : t("aliases.dlg_title_edit")}</div>

        <label style={dlg.label}>{t("aliases.dlg_key")}</label>
        <input style={dlg.input} value={d.key} autoFocus placeholder={t("aliases.key_ph")}
          onChange={e => { setD(p => ({ ...p, key: e.target.value })); setError(""); }} />
        <div style={dlg.hint}>{t("aliases.dlg_key_hint")}</div>

        <label style={dlg.label}>{t("aliases.dlg_entity")}</label>
        <input style={{ ...dlg.input, fontFamily: "monospace" }} value={d.value} list="alias-known-entities" placeholder="light.taklys_stue"
          onChange={e => { setD(p => ({ ...p, value: e.target.value })); setError(""); }} />
        <datalist id="alias-known-entities">
          {knownEntities.map(eid => <option key={eid} value={eid} />)}
        </datalist>
        <div style={{ ...dlg.hint, display: "flex", alignItems: "center", gap: 8 }}>
          {domain ? <DomainBadge domain={domain} /> : null}
          <span>{t("aliases.dlg_entity_hint")}</span>
        </div>

        <label style={dlg.label}>{t("aliases.dlg_room")}</label>
        {haPlace ? (
          <>
            <div style={dlg.readonly}>{haPlaceName} <span style={{ ...badge(HA_COLOR), marginLeft: 6 }}>HA</span></div>
            <div style={dlg.hint}>{t("aliases.dlg_room_from_ha")}</div>
          </>
        ) : (
          <>
            <select style={dlg.input} value={d.place} onChange={e => { setD(p => ({ ...p, place: e.target.value })); setError(""); }}>
              <option value="">{t("aliases.group_unassigned")}</option>
              {haPlaces.length > 0 && (
                <optgroup label={t("aliases.dlg_places_ha")}>
                  {haPlaces.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </optgroup>
              )}
              {ownPlaces.length > 0 && (
                <optgroup label={t("aliases.dlg_places_own")}>
                  {ownPlaces.map(p => <option key={p.id} value={p.id}>{capitalize(p.name)}</option>)}
                </optgroup>
              )}
              <option value={NEW_PLACE}>{t("aliases.dlg_room_new_option")}</option>
            </select>
            <div style={dlg.hint}>{t("aliases.dlg_room_hint")}</div>
            {d.place === NEW_PLACE && (
              <>
                <label style={dlg.label}>{t("aliases.dlg_room_name")}</label>
                <input style={dlg.input} value={d.newPlaceName} placeholder={t("rooms.dlg_name_ph")}
                  onChange={e => { setD(p => ({ ...p, newPlaceName: e.target.value })); setError(""); }} />
                <div style={dlg.hint}>{t("aliases.dlg_room_name_hint")}</div>
              </>
            )}
          </>
        )}

        {error && <div style={dlg.err}>{error}</div>}
        <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
          <button type="submit" style={dlg.btn("#646cff")}>{mode === "create" ? t("common.add") : t("aliases.dlg_apply")}</button>
          <button type="button" style={{ ...dlg.btn("transparent"), border: "1px solid #333", color: "#aaa" }} onClick={onClose}>{t("common.cancel")}</button>
        </div>
      </form>
    </div>
  );
}

type DialogState = { mode: "create" | "edit"; id: number | null; initial: AliasDraft } | null;

export default function TabAliases({ data, view, onSave }: {
  data: Record<string, string>;
  view: RoomsView;
  onSave: (aliases: Record<string, string>, rooms: RoomsRaw | null) => Promise<boolean>;
}) {
  const { t } = useTranslation();
  const ss = useSaveState();
  // Initialised from props; the parent remounts this tab (key) when it reloads the aliases.
  const [pairs, setPairs] = useState<AliasPair[]>(() =>
    Object.entries(data).map(([key, value], id) => ({ id, key, value })),
  );
  const [delta, setDelta] = useState<Delta>({ things: {}, places: {} });
  const [dirty, setDirty] = useState(false);
  const [filter, setFilter] = useState("");
  const [domainFilter, setDomainFilter] = useState<string | null>(null);
  const [missingOnly, setMissingOnly] = useState(false);
  const missingOf = (ref: string) => view.refs[ref]?.missing ?? "";
  const [openRooms, setOpenRooms] = useState<Set<string>>(new Set([UNASSIGNED]));
  const [dialog, setDialog] = useState<DialogState>(null);

  const placeName = (id: string) => view.places.find(p => p.id === id)?.name ?? delta.places[id]?.name ?? id.replace(/_/g, " ");
  const placeOf = (ref: string): string => {
    if (delta.things[ref]?.place !== undefined) return delta.things[ref].place ?? "";
    const r = view.refs[ref];
    if (r) return r.ha_place || r.place;
    return view.things.find(x => x.ref === ref)?.place ?? "";
  };

  const roomOrder = useMemo(
    () => [...view.places.filter(p => !p.hidden).map(p => p.id), ...Object.keys(delta.places)],
    [view.places, delta.places],
  );
  const knownEntities = useMemo(
    () => [...new Set([...pairs.map(p => p.value), ...view.things.map(x => x.ref)])].filter(Boolean).sort(),
    [pairs, view.things],
  );

  const domainCounts = useMemo(() => {
    const c = new Map<string, number>();
    for (const p of pairs) c.set(domainOf(p.value), (c.get(domainOf(p.value)) ?? 0) + 1);
    return [...c.entries()].sort((a, b) => b[1] - a[1]);
  }, [pairs]);

  const visible = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return pairs.filter(p =>
      (!missingOnly || missingOf(p.value) !== "") &&
      (!domainFilter || domainOf(p.value) === domainFilter) &&
      (!f || p.key.toLowerCase().includes(f) || p.value.toLowerCase().includes(f)),
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps -- missingOf reads view.refs, listed below
  }, [pairs, filter, domainFilter, missingOnly, view.refs]);

  const groups = (() => {
    const byRoom = new Map<string, AliasPair[]>();
    for (const pair of visible) {
      const place = placeOf(pair.value);
      const group = place && roomOrder.includes(place) ? place : UNASSIGNED;
      byRoom.set(group, [...(byRoom.get(group) ?? []), pair]);
    }
    return [...roomOrder, UNASSIGNED].filter(g => byRoom.has(g)).map(g => ({ room: g, rows: byRoom.get(g)! }));
  })();

  const unassignedCount = pairs.filter(p => { const pl = placeOf(p.value); return !pl || !roomOrder.includes(pl); }).length;
  const roomsInUse = new Set(pairs.map(p => placeOf(p.value)).filter(pl => pl && roomOrder.includes(pl))).size;

  const searching = filter.trim() !== "" || domainFilter !== null || missingOnly;
  const missingCount = pairs.filter(p => missingOf(p.value) !== "").length;
  const isOpen = (room: string) => searching || openRooms.has(room);
  const toggleRoom = (room: string) => setOpenRooms(prev => {
    const next = new Set(prev);
    if (next.has(room)) next.delete(room); else next.add(room);
    return next;
  });
  const allOpen = groups.every(g => openRooms.has(g.room));
  const toggleAll = () => setOpenRooms(allOpen ? new Set([UNASSIGNED]) : new Set(groups.map(g => g.room)));

  const draftFor = (key: string, value: string, place: string): AliasDraft => ({ key, value, place, newPlaceName: "" });
  const openCreate = (place = "") => setDialog({ mode: "create", id: null, initial: draftFor("", "", place) });
  const openEdit = (pair: AliasPair) => setDialog({ mode: "edit", id: pair.id, initial: draftFor(pair.key, pair.value, placeOf(pair.value)) });

  const takenKeys = useMemo(
    () => new Set(pairs.filter(p => p.id !== dialog?.id).map(p => p.key.trim().toLowerCase())),
    [pairs, dialog],
  );

  const submitDialog = (d: AliasDraft) => {
    if (!dialog) return;
    if (dialog.mode === "create") {
      const id = pairs.reduce((max, p) => Math.max(max, p.id), -1) + 1;
      setPairs(p => [...p, { id, key: d.key, value: d.value }]);
    } else {
      setPairs(p => p.map(x => x.id === dialog.id ? { ...x, key: d.key, value: d.value } : x));
    }
    const place = d.place === NEW_PLACE ? slug(d.newPlaceName) : d.place;
    setDelta(prev => {
      const next: Delta = { things: { ...prev.things }, places: { ...prev.places } };
      if (d.place === NEW_PLACE) next.places[place] = { name: d.newPlaceName.trim() };
      // Register the thing; a place only where HA has none for it (decision 18).
      const haPlace = view.refs[d.value]?.ha_place;
      if (!haPlace && place !== placeOf(d.value)) next.things[d.value] = { place };
      else if (!view.refs[d.value]?.registered && !view.things.some(x => x.ref === d.value)) next.things[d.value] = next.things[d.value] ?? {};
      return next;
    });
    setOpenRooms(prev => new Set(prev).add(place || UNASSIGNED));
    setDirty(true);
    setDialog(null);
  };

  const deleteById = (id: number) => {
    setPairs(p => p.filter(x => x.id !== id));
    setDirty(true);
  };

  const save = async () => {
    const obj: Record<string, string> = {};
    for (const { key, value } of pairs) {
      if (key.trim()) obj[key.trim()] = value.trim();
    }
    let rooms: RoomsRaw | null = null;
    if (Object.keys(delta.things).length || Object.keys(delta.places).length) {
      // Merge the delta into the latest rooms.yaml the server gave — never a stale copy.
      rooms = JSON.parse(JSON.stringify(view.raw ?? {})) as RoomsRaw;
      rooms.places = { ...(rooms.places ?? {}), ...Object.fromEntries(Object.entries(delta.places).map(([id, p]) => [id, { name: p.name }])) };
      rooms.things = rooms.things ?? {};
      for (const [ref, th] of Object.entries(delta.things)) {
        const cur = rooms.things[ref] ?? {};
        rooms.things[ref] = th.place !== undefined ? { ...cur, ...(th.place ? { place: th.place } : {}) } : cur;
        if (th.place === "" && rooms.things[ref].place) delete rooms.things[ref].place;
      }
    }
    ss.saving();
    if (await onSave(obj, rooms)) {
      setDelta({ things: {}, places: {} });
      setDirty(false);
      ss.saved();
    } else {
      ss.error();
    }
  };

  const groupTitle = (room: string) => room === UNASSIGNED ? t("aliases.group_unassigned") : placeName(room);

  return (
    <div className="space-y-4">
      {/* Status line */}
      <p style={{ color: "#888", fontSize: 12, margin: 0 }}>
        <span style={{ color: "#a78bfa", fontWeight: 600 }}>{pairs.length}</span> {t("aliases.stat_aliases")}
        {" · "}<span style={{ color: "#ddd", fontWeight: 600 }}>{roomsInUse}</span> {t("aliases.stat_rooms")}
        {" · "}<span style={{ color: unassignedCount > 0 ? WARN : "#ddd", fontWeight: 600 }}>{unassignedCount}</span> {t("aliases.stat_unassigned")}
      </p>

      {/* Search + add */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input value={filter} onChange={e => setFilter(e.target.value)} placeholder={t("aliases.search_ph")} className="pl-8 h-8 text-sm" />
        </div>
        <Button size="sm" onClick={() => openCreate()}><Plus className="h-3.5 w-3.5 mr-1" /> {t("common.add")}</Button>
      </div>

      {/* Type filter */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        <span style={sectionLabel}>{t("aliases.filter_type")}</span>
        <button style={pill(domainFilter === null)} onClick={() => setDomainFilter(null)}>{t("aliases.filter_all")}</button>
        {domainCounts.map(([domain, count]) => {
          const active = domainFilter === domain;
          const color = domainColor(domain);
          return (
            <button
              key={domain}
              onClick={() => setDomainFilter(active ? null : domain)}
              style={{ ...pill(active), background: active ? color + "30" : "#1a1a1a", color: active ? color : "#999", border: `1px solid ${active ? color + "60" : "transparent"}` }}
            >
              <span style={{ display: "inline-block", width: 7, height: 7, borderRadius: 4, background: color, marginRight: 6 }} />
              {t(`aliases.domain.${domain}`, { defaultValue: domain || "?" })} <span style={{ opacity: 0.6 }}>{count}</span>
            </button>
          );
        })}
        {missingCount > 0 && (
          <button
            onClick={() => setMissingOnly(v => !v)}
            style={{ ...pill(missingOnly), background: missingOnly ? "#ff6b6b30" : "#1a1a1a", color: missingOnly ? "#ff6b6b" : "#ff8a8a", border: `1px solid ${missingOnly ? "#ff6b6b60" : "#ff6b6b30"}` }}
          >
            {t("aliases.filter_missing")} <span style={{ opacity: 0.7 }}>{missingCount}</span>
          </button>
        )}
        <button style={{ ...pill(false), marginLeft: "auto", background: "transparent", border: "1px solid #252525" }} onClick={toggleAll} disabled={searching}>
          {allOpen ? t("aliases.collapse_all") : t("aliases.expand_all")}
        </button>
      </div>

      {/* Room cards */}
      {groups.length === 0 && (
        <div style={{ color: "#888", fontSize: 13, padding: "16px 0", textAlign: "center" }}>{t("aliases.empty")}</div>
      )}
      <div>
        {groups.map(({ room, rows }) => {
          const unassigned = room === UNASSIGNED;
          const open = isOpen(room);
          const place = view.places.find(p => p.id === room);
          const synonyms = place ? [...place.ha_aliases, ...place.synonyms] : [];
          const counts = new Map<string, number>();
          for (const pair of rows) counts.set(domainOf(pair.value), (counts.get(domainOf(pair.value)) ?? 0) + 1);
          return (
            <div key={room} style={card(unassigned ? WARN : null)}>
              <div onClick={() => !searching && toggleRoom(room)} style={{ ...cardHeader, cursor: searching ? "default" : "pointer" }}>
                <span style={{ color: "#777", display: "flex" }}>{open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</span>
                <span style={{ color: unassigned ? WARN : "#eee", fontSize: 14, fontWeight: 700 }}>{groupTitle(room)}</span>
                {place?.source === "ha" && <span style={badge(HA_COLOR)}>HA</span>}
                <span style={{ color: "#777", fontSize: 12 }}>{rows.length}</span>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {synonyms.filter(s => s.toLowerCase() !== (place?.name ?? "").toLowerCase()).map(s => <span key={s} style={chip}>{s}</span>)}
                </div>
                <div style={{ display: "flex", gap: 4, marginLeft: "auto", flexWrap: "wrap", alignItems: "center" }}>
                  {[...counts.entries()].map(([d, n]) => (
                    <span key={d} style={badge(domainColor(d))}>{t(`aliases.domain.${d}`, { defaultValue: d || "?" })} {n}</span>
                  ))}
                  <button title={t("aliases.add_to_room")} onClick={e => { e.stopPropagation(); openCreate(unassigned ? "" : room); }} style={{ ...smallBtn, marginLeft: 6 }}>
                    <Plus size={13} />
                  </button>
                </div>
              </div>

              {open && (
                <div style={{ background: "#0f0f0f" }}>
                  {rows.map(pair => {
                    const domain = domainOf(pair.value);
                    const name = domain ? pair.value.slice(domain.length + 1) : pair.value;
                    const haName = view.refs[pair.value]?.name;
                    return (
                      <div key={pair.id} onClick={() => openEdit(pair)} title={t("aliases.click_to_edit")} className="hover:bg-white/[0.03]" style={{ ...cardRow, cursor: "pointer" }}>
                        <div style={{ width: 92, flexShrink: 0 }}><DomainBadge domain={domain} /></div>
                        <span style={{ flex: "1 1 200px", minWidth: 0, color: "#e6e6e6", fontSize: 13 }} className="truncate">
                          {pair.key || <em style={{ color: "#777" }}>{t("aliases.empty_key")}</em>}
                        </span>
                        <span style={{ flex: "1 1 240px", minWidth: 0, fontFamily: "monospace", fontSize: 12 }} className="truncate" title={haName && haName !== pair.value ? haName : undefined}>
                          {domain && <span style={{ color: "#666" }}>{domain}.</span>}
                          <span style={{ color: "#aaa" }}>{name}</span>
                        </span>
                        <MissingBadge missing={missingOf(pair.value)} />
                        <Button variant="ghost" size="sm" className="h-7 px-2 text-destructive hover:text-destructive" onClick={e => { e.stopPropagation(); deleteById(pair.id); }}><Trash2 className="h-3 w-3" /></Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={ss.state === "saving"}>
          {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {t("aliases.save_btn")}
        </Button>
        {dirty && ss.state === "idle" && <span style={badge(WARN)}>{t("aliases.unsaved")}</span>}
        <SaveFeedback state={ss.state} />
      </div>
      <p style={{ color: "#888", fontSize: 12 }}>{t("aliases.hot_reload_note")} {t("aliases.grouping_note")}</p>

      {dialog && (
        <AliasDialog
          mode={dialog.mode}
          initial={dialog.initial}
          view={view}
          delta={delta}
          knownEntities={knownEntities}
          takenKeys={takenKeys}
          onSubmit={submitDialog}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}
