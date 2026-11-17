// J24 (2026-11-07: «når jeg holdt musepekeren over teksten skulle det blitt en finger, så kunne det klikkes og
// bildet vises»): the picture of one camera event, loaded only when the question row is opened. Header auth through the
// shared axios instance (never a token in the URL); the existing snapshot route decides access.
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";

export default function EventSnapshot({ eventId }: { eventId: string }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let u: string | null = null;
    api.get(`/api/frigate_snapshot/${encodeURIComponent(eventId)}`, { responseType: "blob" })
      .then(r => { u = URL.createObjectURL(r.data); setUrl(u); })
      .catch(() => setFailed(true));
    return () => { if (u) URL.revokeObjectURL(u); };
  }, [eventId]);
  if (failed) return <div style={{ color: "#888", fontSize: 12, padding: "4px 0" }}>{t("questions_card.no_event_image")}</div>;
  return url
    ? <img src={url} alt="" style={{ width: "100%", maxWidth: 480, borderRadius: 6, display: "block", margin: "4px 0" }} />
    : <div style={{ width: "100%", maxWidth: 480, paddingTop: "28%", background: "#1a1a1a", borderRadius: 6, margin: "4px 0" }} />;
}
