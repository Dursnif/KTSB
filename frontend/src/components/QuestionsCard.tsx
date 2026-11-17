// Run-queue J20 (2026-11-06): one card «Hvem / Hva / Hvor?» for every question KTSB asks about the camera world —
// «Hvem» = «Var det deg?» (only your own questions, never about another person), «Hva» = «Hva er dette?» + «Er dette det
// samme?» (still things), «Hvor» = «Hva er her?» (places). Hva and Hvor are admin only for now («det kan eventuelt dras ut
// i bruker-GUI senere»); P150 6b adds the vehicle part (admin). Always shown; open questions on top in each part, the answers so far below, pictures without
// people. Collapsed by default with the open/close button top right (G1); the count is every open question in it.
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import CameraQuestionsCard from "./CameraQuestionsCard";
import PlaceQuestionsCard from "@/pages/admin/integrations/PlaceQuestionsCard";
import ThingQuestionsCard from "@/pages/admin/integrations/ThingQuestionsCard";
import WhatQuestionsCard from "@/pages/admin/integrations/WhatQuestionsCard";
import WhoForOthersCard from "@/pages/admin/integrations/WhoForOthersCard";
import VehicleQuestionsCard from "@/pages/admin/integrations/VehicleQuestionsCard";

type Part = "who" | "others" | "what" | "same" | "where" | "vehicles";

export default function QuestionsCard({ admin }: { admin: boolean }) {
  const { t } = useTranslation();
  const k = (key: string) => t(`questions_card.${key}`);
  const [open, setOpen] = useState(false);
  const [counts, setCounts] = useState<Record<Part, number>>({ who: 0, others: 0, what: 0, same: 0, where: 0, vehicles: 0 });
  const set = (p: Part, n: number) => setCounts(c => (c[p] === n ? c : { ...c, [p]: n }));
  const onWho = useCallback((n: number) => set("who", n), []);
  const onWhat = useCallback((n: number) => set("what", n), []);
  const onOthers = useCallback((n: number) => set("others", n), []);
  const onSame = useCallback((n: number) => set("same", n), []);
  const onWhere = useCallback((n: number) => set("where", n), []);
  const onVehicles = useCallback((n: number) => set("vehicles", n), []);
  const total = counts.who + (admin ? counts.others + counts.what + counts.same + counts.where + counts.vehicles : 0);
  const head = (key: string) => (
    <div style={{ color: "#ddd", fontSize: 13, fontWeight: 500, marginTop: 16, paddingTop: 10, borderTop: "1px solid #222" }}>
      {k(key)}
    </div>
  );
  return (
    <div style={{ background: "#111", border: "1px solid #1e1e1e", borderRadius: 8, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ color: "#ccc", fontSize: 14, fontWeight: 500 }}>
          {k(admin ? "title" : "title_who")} <span style={{ color: "#888", fontWeight: 400 }}>({total})</span>
        </div>
        <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
          style={{ background: "none", border: "1px solid #333", borderRadius: 6, color: "#aaa", fontSize: 12,
            padding: "2px 8px", cursor: "pointer" }}>{open ? "▾" : "▸"}</button>
      </div>
      {/* the parts stay mounted while collapsed so the count is right; only their display is hidden */}
      <div style={{ display: open ? "block" : "none" }}>
        {head("who")}
        <CameraQuestionsCard bare onCount={onWho} />
        {admin && (
          <>
            {head("others")}
            <WhoForOthersCard onCount={onOthers} />
            {head("what")}
            <WhatQuestionsCard onCount={onWhat} />
            {head("same")}
            <ThingQuestionsCard bare onCount={onSame} />
            {head("where")}
            <PlaceQuestionsCard bare onCount={onWhere} />
            {head("vehicles")}
            <VehicleQuestionsCard onCount={onVehicles} />
          </>
        )}
      </div>
    </div>
  );
}
