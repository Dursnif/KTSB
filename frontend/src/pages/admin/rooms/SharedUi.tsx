// Small shared components for Rom og aliaser (P137).
import { useTranslation } from "react-i18next";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";
import { ACCENT, HA_COLOR, badge, domainColor, type SaveState } from "./shared";

export function DomainBadge({ domain }: { domain: string }) {
  const { t } = useTranslation();
  return <span style={badge(domainColor(domain))}>{t(`aliases.domain.${domain}`, { defaultValue: domain || "?" })}</span>;
}

// Red badge for a reference HA does not have (gone) or only keeps as a leftover (orphan).
export function MissingBadge({ missing }: { missing?: string }) {
  const { t } = useTranslation();
  if (!missing) return null;
  return <span style={badge("#ff6b6b")} title={t(`rooms.missing_hint_${missing}`)}>{t(`rooms.missing_${missing}`)}</span>;
}

export function SourceBadge({ source }: { source: string }) {
  const { t } = useTranslation();
  const color = source === "ha" ? HA_COLOR : source === "manual" ? ACCENT : "#5b9cff";
  return <span style={badge(color)}>{t(`rooms.source.${source}`, { defaultValue: source })}</span>;
}

export function SaveFeedback({ state }: { state: SaveState }) {
  const { t } = useTranslation();
  if (state === "saving") return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />;
  if (state === "saved")  return <span className="flex items-center gap-1 text-sm text-green-500"><CheckCircle2 className="h-4 w-4" /> {t("common.saved")}</span>;
  if (state === "error")  return <span className="flex items-center gap-1 text-sm text-destructive"><XCircle className="h-4 w-4" /> {t("common.error")}</span>;
  return null;
}
