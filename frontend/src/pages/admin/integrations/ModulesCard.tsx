// J21 5e (P144 phase 5, GUI list G19–G23): the module panel «Moduler» — the camera pipe as modules 0–7, honest about
// what each switch does today: m2 (extra face recognition, Double Take) and, since DT04 R0.6b, m4–m6 act (they ship off
// and carry a «uses the main model» hint); m1 is «erstattet»; m3 is shown with «følger Frigate-brukeren»; m0 and m7 are
// «alltid på». Before m1 is saved as «av» the panel says what then disappears (Siste deteksjoner, and the readers not yet moved
// to frigate.db: Argus, Jing, the analysis log). Collapsed by default, button top right (G1). Placement provisional.
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiGetFrigateModules, apiPutFrigateUserPipe, type FrigateModuleRow } from "@/services/api";

type Value = "auto" | "on" | "off";
// DT04 R0.6b: m4–m6 act now and ship off — they use the main model (the one Kåre answers with on a stock install).
const HEAVY = [4, 5, 6];

export default function ModulesCard() {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.frigate_modules.${key}`, o);
  const [rows, setRows] = useState<FrigateModuleRow[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const load = useCallback(() => {
    apiGetFrigateModules().then(d => { setRows(d.modules); setWarnings(d.warnings); setError(false); })
      .catch(() => setError(true));
  }, []);
  useEffect(() => { load(); }, [load]);
  const save = async (n: number, v: Value) => {
    setBusy(true);
    try { await apiPutFrigateUserPipe({ modules: { [`m${n}`]: v } }); } catch { setError(true); }
    finally { setBusy(false); load(); }
  };
  const choose = (n: number, v: Value) => { void save(n, v); };
  const btn = { background: "none", border: "1px solid #333", borderRadius: 6, color: "#aaa", fontSize: 12,
    padding: "2px 8px", cursor: "pointer" } as const;
  const state = (r: FrigateModuleRow) => {
    if (r.replaced) return k("m1_replaced");
    if (r.setting === "always") return k("always_on");
    if (!r.switchable) return r.on ? k("follows_on") : k("follows_off");
    const why = r.why === "needs" ? k("why_needs", { needs: r.needs_missing.join(", ") }) : k(`why_${r.why}`);
    return `${r.on ? k("on") : k("off")} — ${why}`;
  };
  return (
    <div style={{ background: "#111", border: "1px solid #1e1e1e", borderRadius: 8, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ color: "#ccc", fontSize: 14, fontWeight: 500 }}>{k("title")}</div>
        <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open} style={btn}>{open ? "▾" : "▸"}</button>
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          <div style={{ color: "#777", fontSize: 12, lineHeight: 1.5, marginBottom: 8 }}>{k("intro")}</div>
          {error && <div style={{ color: "#f87171", fontSize: 12 }}>{k("error")}</div>}
          {warnings.includes("double_work") && (
            <div role="status" style={{ color: "#e0b040", fontSize: 12, marginBottom: 8 }}>{k("warn_double_work")}</div>
          )}
          {rows.map(r => (
            <div key={r.n} style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", padding: "6px 0",
              borderTop: "1px solid #1a1a1a", opacity: r.on ? 1 : 0.7 }}>
              <div style={{ flex: "1 1 220px", minWidth: 0 }}>
                <div style={{ color: "#ccc", fontSize: 13 }}>{r.n}. {k(`m${r.n}`)}</div>
                <div style={{ color: "#888", fontSize: 11 }}>{state(r)}</div>
                {HEAVY.includes(r.n) && (  /* DT04 R0.6b: these queue on the model Kåre answers with */
                  <div style={{ color: "#e0b040", fontSize: 11 }}>{k("heavy_hint")}</div>
                )}
              </div>
              {r.switchable && (
                <div style={{ display: "flex", gap: 6 }}>
                  {(["auto", "on", "off"] as Value[]).map(v => (
                    <button key={v} type="button" disabled={busy} onClick={() => choose(r.n, v)}
                      style={{ ...btn, color: r.setting === v ? "#ddd" : "#888", borderColor: r.setting === v ? "#555" : "#333" }}>
                      {k(`v_${v}`)}</button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
