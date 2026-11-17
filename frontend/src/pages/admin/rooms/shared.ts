// Shared look for Rom og aliaser (P137) — same palette as the Security and Tools pages.
import { useState, type CSSProperties } from "react";

export const ACCENT = "#7c6aff";
export const WARN = "#ff9f43";
export const HA_COLOR = "#4db67a";

// One colour per HA domain; domains not listed fall back to DOMAIN_OTHER.
const DOMAIN_COLOR: Record<string, string> = {
  light:         "#f5c542",
  switch:        "#4caf50",
  sensor:        "#5b9cff",
  binary_sensor: "#ff9f43",
  climate:       "#ff6b6b",
  media_player:  "#c084fc",
  camera:        "#5ba8a0",
  vacuum:        "#a3e635",
  person:        "#2dd4bf",
};
const DOMAIN_OTHER = "#888";

export const domainOf = (ref: string) => /^[a-z_]+\.[a-z0-9_]+$/.test(ref) ? ref.split(".")[0] : "";
export const domainColor = (domain: string) => DOMAIN_COLOR[domain] ?? DOMAIN_OTHER;
export const roomLabel = (room: string) => room.replace(/_/g, " ");
export const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
export const parseList = (text: string) => [...new Set(text.split(",").map(x => x.trim()).filter(Boolean))];

export const pill = (active: boolean): CSSProperties => ({
  padding: "5px 13px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 12,
  background: active ? "#646cff" : "#1a1a1a",
  color: active ? "#fff" : "#999",
  fontWeight: active ? 600 : 400,
});

export const badge = (color: string): CSSProperties => ({
  padding: "1px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600, whiteSpace: "nowrap",
  background: color + "20", color, border: `1px solid ${color}35`,
});

export const chip: CSSProperties = {
  padding: "1px 7px", borderRadius: 4, fontSize: 11, color: "#999", background: "#1e1e1e", border: "1px solid #2a2a2a",
};

export const sectionLabel: CSSProperties = { color: "#777", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, marginRight: 2 };

export const card = (accent?: string | null): CSSProperties => ({
  marginBottom: 10, border: `1px solid ${accent ? accent + "55" : "#222"}`, borderRadius: 10, overflow: "hidden",
});
export const cardHeader: CSSProperties = {
  display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", background: "#141414", userSelect: "none", flexWrap: "wrap",
};
export const cardRow: CSSProperties = {
  display: "flex", alignItems: "center", gap: 10, padding: "7px 14px", borderTop: "1px solid #1c1c1c", flexWrap: "wrap",
};
export const smallBtn: CSSProperties = {
  display: "flex", alignItems: "center", gap: 4, padding: "3px 8px", borderRadius: 6, border: "1px solid #2a2a2a",
  background: "#1a1a1a", color: "#aaa", cursor: "pointer", fontSize: 12,
};

// Dialog in the Users-page modal pattern (dark overlay, centred box, accent top border).
export const dlg = {
  overlay: { position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100, padding: 16, overflowY: "auto" } as CSSProperties,
  box: { background: "#1a1a1a", border: `1px solid ${ACCENT}55`, borderTop: `3px solid ${ACCENT}`, borderRadius: 12, padding: 28, width: "100%", maxWidth: 460, boxShadow: "0 8px 32px rgba(0,0,0,0.6)", maxHeight: "calc(100vh - 32px)", overflowY: "auto" } as CSSProperties,
  title: { color: "#fff", fontSize: 18, fontWeight: 700, marginBottom: 20 } as CSSProperties,
  label: { color: "#aaa", fontSize: 13, marginBottom: 5, display: "block" } as CSSProperties,
  input: { width: "100%", padding: "9px 12px", borderRadius: 7, border: "1px solid #333", background: "#111", color: "#fff", fontSize: 14, boxSizing: "border-box", marginBottom: 4 } as CSSProperties,
  readonly: { width: "100%", padding: "9px 12px", borderRadius: 7, border: "1px solid #222", background: "#151515", color: "#bbb", fontSize: 14, boxSizing: "border-box", marginBottom: 4 } as CSSProperties,
  hint: { color: "#777", fontSize: 12, marginBottom: 14, minHeight: 16 } as CSSProperties,
  err: { color: "#ff6b6b", fontSize: 13, marginTop: 4, marginBottom: 8 } as CSSProperties,
  btn: (bg: string): CSSProperties => ({ padding: "8px 18px", borderRadius: 8, border: "none", background: bg, color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer" }),
};

export type SaveState = "idle" | "saving" | "saved" | "error";

export function useSaveState() {
  const [state, setState] = useState<SaveState>("idle");
  const saved = () => { setState("saved"); setTimeout(() => setState("idle"), 3000); };
  const error = () => { setState("error"); setTimeout(() => setState("idle"), 4000); };
  return { state, saving: () => setState("saving"), saved, error };
}

