// Face classification (P144-s05, N19 point 6): each face in Frigate's face library is household / safe guest /
// not welcome, plus an attribute profile the Frigate user checks Frigate's claims against. Self-contained so it
// drops into Integrations → Frigate with one line and can move whole (I21). Admin-only API; saves on each change.
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { AlertTriangle, ExternalLink, UserRound } from "lucide-react";
import {
  apiGetFrigateFaceList, apiGetFrigateFaceThumb, apiPutFrigateFace, apiPutFrigateFaceSettings,
  type FaceAttributes, type FaceClass, type FrigateFaceList, type FrigateFaceRow,
} from "@/services/api";
import { ExpertOnly, SaveFeedback } from "../settings/shared";
import { useSaveState } from "../settings/useSaveState";
import { WARN, badge, chip, sectionLabel } from "../rooms/shared";

const CLASS_COLOR: Record<FaceClass, string> = {
  household: "#4db67a",
  safe_guest: "#5dade2",
  not_welcome: "#e17055",
  unclassified: "#666666",
};
const CLASS_ORDER: FaceClass[] = ["household", "safe_guest", "not_welcome", "unclassified"];
const ATTR_FIELDS: (keyof FaceAttributes)[] = ["beard", "glasses", "age_band", "headwear_often"];

const grid: CSSProperties = { display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" };
const select: CSSProperties = {
  width: "100%", background: "#1a1a1a", color: "#ddd", border: "1px solid #2a2a2a", borderRadius: 6,
  padding: "4px 6px", fontSize: 12,
};

function Thumb({ name, images }: { name: string; images: number }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!images) return;
    let alive = true;
    let made = "";
    apiGetFrigateFaceThumb(name).then(u => {
      made = u;
      if (alive) setUrl(u); else URL.revokeObjectURL(u);
    }).catch(() => {});
    return () => { alive = false; if (made) URL.revokeObjectURL(made); };
  }, [name, images]);
  const box: CSSProperties = {
    width: 44, height: 44, borderRadius: 8, flexShrink: 0, background: "#1e1e1e", objectFit: "cover",
    display: "flex", alignItems: "center", justifyContent: "center",
  };
  return url ? <img src={url} alt="" style={box} /> : <div style={box}><UserRound size={20} color="#555" /></div>;
}

function FaceItem({ face, values, onSaved }: {
  face: FrigateFaceRow;
  values: FrigateFaceList["attribute_values"];
  onSaved: (row: FrigateFaceRow) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const ss = useSaveState();
  const color = CLASS_COLOR[face.class];

  const save = async (cls: FaceClass, attributes: FaceAttributes) => {
    ss.saving();
    try { onSaved(await apiPutFrigateFace(face.name, cls, attributes)); ss.saved(); } catch { ss.error(); }
  };

  return (
    <div style={{ background: "#141414", borderRadius: 10, borderTop: `2px solid ${color}`, padding: 12, minWidth: 0 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", minWidth: 0 }}>
        <Thumb name={face.name} images={face.images} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ color: "#ddd", fontSize: 14, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {face.name}
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 3 }}>
            <span style={badge(color)}>{t(`integrations.frigate_faces.class.${face.class}`)}</span>
            {!face.in_library && <span style={chip}>{t("integrations.frigate_faces.not_in_library")}</span>}
          </div>
        </div>
        <SaveFeedback state={ss.state} />
      </div>

      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 10 }}>
        {CLASS_ORDER.map(c => {
          const on = face.class === c;
          return (
            <button
              key={c}
              onClick={() => !on && save(c, face.attributes)}
              style={{
                padding: "3px 9px", borderRadius: 12, fontSize: 12, cursor: on ? "default" : "pointer",
                border: `1px solid ${on ? CLASS_COLOR[c] : "#2a2a2a"}`,
                background: on ? CLASS_COLOR[c] + "25" : "#1a1a1a", color: on ? CLASS_COLOR[c] : "#999",
              }}
            >
              {t(`integrations.frigate_faces.class.${c}`)}
            </button>
          );
        })}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 10 }}>
        {ATTR_FIELDS.map(field => (
          <label key={field} style={{ display: "block", minWidth: 0 }}>
            <span style={sectionLabel}>{t(`integrations.frigate_faces.attr.${field}`)}</span>
            <select
              style={select}
              value={face.attributes[field]}
              onChange={e => save(face.class, { ...face.attributes, [field]: e.target.value })}
            >
              {(values[field] ?? []).map(v => (
                <option key={v} value={v}>{t(`integrations.frigate_faces.value.${v}`)}</option>
              ))}
            </select>
          </label>
        ))}
      </div>

      <div style={{ marginTop: 10, fontSize: 12, color: "#888", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <span style={sectionLabel}>{t("integrations.frigate_faces.user_label")}</span>
        {face.linked_user
          ? <span style={{ color: "#ccc" }}>{face.linked_user.display_name}</span>
          : <span>{t("integrations.frigate_faces.no_user")}</span>}
        <button
          onClick={() => navigate("/admin/users")}
          style={{ background: "none", border: "none", color: "#5dade2", cursor: "pointer", fontSize: 12, padding: 0, display: "inline-flex", alignItems: "center", gap: 3 }}
        >
          {t("integrations.frigate_faces.users_link")} <ExternalLink size={11} />
        </button>
      </div>
    </div>
  );
}

export default function FrigateFacesCard() {
  const { t } = useTranslation();
  const [list, setList] = useState<FrigateFaceList | null>(null);
  const [days, setDays] = useState("7");
  const ssDays = useSaveState();

  const load = useCallback(() => {
    apiGetFrigateFaceList().then(d => { setList(d); setDays(String(d.unclassified_name_days)); }).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const onSaved = (row: FrigateFaceRow) => {
    setList(prev => {
      if (!prev) return prev;
      const faces = prev.faces.map(f => (f.name === row.name ? row : f));
      const unclassified_count = faces.filter(f => f.in_library && f.class === "unclassified").length;
      return { ...prev, faces, unclassified_count };
    });
  };

  const saveDays = async () => {
    const n = Number(days);
    if (!Number.isInteger(n) || n < 0 || n > 90) { ssDays.error(); return; }
    ssDays.saving();
    try { const r = await apiPutFrigateFaceSettings(n); setDays(String(r.unclassified_name_days)); ssDays.saved(); }
    catch { ssDays.error(); }
  };

  if (!list) return null;
  const empty = list.faces.length === 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("integrations.frigate_faces.title")}</CardTitle>
        <CardDescription>{t("integrations.frigate_faces.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {list.error && (
          <div style={{ fontSize: 13, color: WARN }}>{t(`integrations.frigate_faces.error_${list.error}`)}</div>
        )}
        {list.unclassified_count > 0 && (
          <div style={{
            display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderRadius: 8, fontSize: 13,
            background: WARN + "15", border: `1px solid ${WARN}40`, color: WARN,
          }}>
            <AlertTriangle size={15} style={{ flexShrink: 0 }} />
            <span>{t("integrations.frigate_faces.unclassified_banner", { count: list.unclassified_count, days: list.unclassified_name_days })}</span>
          </div>
        )}
        {empty && !list.error && (
          <div style={{ fontSize: 13, color: "#888" }}>{t("integrations.frigate_faces.empty")}</div>
        )}
        {!empty && (
          <div style={grid}>
            {list.faces.map(f => (
              <FaceItem key={f.name} face={f} values={list.attribute_values} onSaved={onSaved} />
            ))}
          </div>
        )}
        <div style={{ fontSize: 12, color: "#777" }}>{t("integrations.frigate_faces.claim_note")}</div>
        <ExpertOnly>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", paddingTop: 6, borderTop: "1px solid #222" }}>
            <span style={{ fontSize: 13, color: "#ccc" }}>{t("integrations.frigate_faces.days_label")}</span>
            <Input
              type="number" min={0} max={90} value={days} className="w-20"
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDays(e.target.value)}
              onBlur={saveDays}
            />
            <SaveFeedback state={ssDays.state} />
            <div style={{ fontSize: 12, color: "#777", flexBasis: "100%" }}>{t("integrations.frigate_faces.days_hint")}</div>
          </div>
        </ExpertOnly>
      </CardContent>
    </Card>
  );
}
