// Usage bar for a storage limit (used / limit MB, colour by fill) — shared by every storage card (P150; moved out of
// Cameras.tsx so the Frigate «Lagringsgrenser» card and Settings → Logs/Storage show the same bar).
export default function StorageBar({ label, usedMb, limitMb }: { label: string; usedMb: number; limitMb: number }) {
  const pct = limitMb > 0 ? Math.min(100, Math.round((usedMb / limitMb) * 100)) : 0;
  const color = pct >= 90 ? "#f87171" : pct >= 70 ? "#fb923c" : "#6366f1";
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ color: "#888", fontSize: 12 }}>{label}</span>
        <span style={{ color: "#666", fontSize: 12 }}>
          {usedMb} / {limitMb > 0 ? limitMb : "∞"} MB
          {limitMb > 0 && <span style={{ marginLeft: 6, color: pct >= 70 ? color : "#555" }}>({pct}%)</span>}
        </span>
      </div>
      <div style={{ height: 6, background: "#1e1e1e", borderRadius: 4, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 4, transition: "width 0.3s" }} />
      </div>
    </div>
  );
}
