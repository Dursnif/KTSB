// J18b / J20: a place's (or a still thing's) picture for the question card — Frigate's recording frame by day with no
// active track on that camera (never a person; the server picks the moment), the spot framed in yellow. No such frame →
// the camera and the position as text. Admin route; the same access as the questions it belongs to.
import { useEffect, useState } from "react";
import { api } from "@/services/api";

type Spot = { camera: string; camera_name?: string; cell: string; box: [number, number, number, number] };

function where(cell: string, k: (key: string) => string): string {
  const [col, row] = cell.split(",").map(Number);
  const r = row <= 1 ? "row_top" : row >= 3 ? "row_bottom" : "row_mid";
  const c = col <= 1 ? "col_left" : col >= 3 ? "col_right" : "col_mid";
  return `${k(r)} ${k(c)}`;
}

export default function SpotImage({ s, width, k }: { s: Spot; width: number; k: (key: string, o?: Record<string, unknown>) => string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let u: string | null = null;
    api.get(`/api/frigate_user/questions/places/image/${encodeURIComponent(s.camera)}/${encodeURIComponent(s.cell)}`,
      { responseType: "blob" })
      .then(r => { u = URL.createObjectURL(r.data); setUrl(u); }).catch(() => { setUrl(null); setFailed(true); });
    return () => { if (u) URL.revokeObjectURL(u); };
  }, [s.camera, s.cell]);
  if (failed) {
    return <div style={{ color: "#888", fontSize: 12 }}>{k("no_image", { camera: s.camera_name || s.camera, where: where(s.cell, k) })}</div>;
  }
  const [x, y, w, h] = s.box;
  return (
    <div style={{ position: "relative", width: "100%", maxWidth: width }}>
      {url ? <img src={url} alt="" style={{ width: "100%", borderRadius: 6, display: "block" }} />
        : <div style={{ width: "100%", paddingTop: "56%", background: "#1a1a1a", borderRadius: 6 }} />}
      <div style={{ position: "absolute", left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`,
        height: `${h * 100}%`, border: "2px solid #e0b040", borderRadius: 4, background: "rgba(224,176,64,0.12)",
        pointerEvents: "none" }} />
    </div>
  );
}
