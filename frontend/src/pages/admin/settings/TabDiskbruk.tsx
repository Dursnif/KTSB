// Settings → «Logg/Lagring» (P150, 2026-11-13; was «Logg/Lagring», and the Images tab's cards moved here): size limits per data class — nothing is deleted for being old;
// when a limit is full the oldest goes first, after a short summary is kept where the class has one.
// Basic: the disks («KTSB er satt opp til å bruke x GB av y GB») + five large groups; a group limit is shared
// proportionally («forholdsmessig»); a limit with its own home (Frigate tab, Images tab) is only shown here,
// with the place to set it. Expert: every data class, Argus' detail, and the «Logs and data» card
// (moved here from General). A class without a size control yet says so — never pretend.
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import StorageBar from "@/components/StorageBar";
import { apiGetDiskUsage, apiPutDiskUsage, type DiskUsage } from "@/services/api";
import { apiErrorDetail } from "@/lib/apiError";
import { ExpertOnly, FieldRow, SaveFeedback } from "./shared";
import { useSaveState } from "./useSaveState";
import LogsAndDataCard from "./LogsAndDataCard";
import TabBilder from "./TabBilder";

const KNOWN_STATUS = ["under_limit", "trimmed", "off", "only_today_left", "max_days_reached", "error",
  "empty_or_unmeasured", "unmeasured"];

type Put = Parameters<typeof apiPutDiskUsage>[0];

/** One number + save button; reports errors under itself. Callers key it by the saved value so it resets after a save. */
function LimitField({ value, onSave, step = 100 }: { value: number; onSave: (mb: number) => Promise<void>; step?: number }) {
  const { t } = useTranslation();
  const ss = useSaveState();
  const [v, setV] = useState(String(value));
  const [error, setError] = useState("");
  const save = () => {
    setError("");
    ss.saving();
    onSave(Number(v)).then(() => ss.saved()).catch(err => { setError(apiErrorDetail(err) ?? t("common.error")); ss.error(); });
  };
  return (
    <div className="flex items-center gap-2 mt-2">
      <Input type="number" min={0} step={step} value={v} className="w-36"
        onChange={(e: React.ChangeEvent<HTMLInputElement>) => setV(e.target.value)} />
      <span className="text-xs text-muted-foreground">MB</span>
      <Button size="sm" onClick={save} disabled={ss.state === "saving" || v === ""}>
        {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        {t("common.save")}
      </Button>
      <SaveFeedback state={ss.state} />
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}

function DisksAndGroupsCard({ info, put }: { info: DiskUsage; put: (p: Put) => Promise<void> }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.diskbruk.overview_title")}</CardTitle>
        <CardDescription>{t("settings.diskbruk.overview_description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {info.disks.map(d => (
          <div key={d.mount} className="text-sm">
            <p>{t("settings.diskbruk.disk_line", { limits: d.limits_gb, total: d.total_gb, mount: d.mount, free: d.free_gb })}</p>
            {d.unlimited.length > 0 && (
              <p className="text-xs mt-1 text-muted-foreground">{t("settings.diskbruk.disk_unlimited", { n: d.unlimited.length })}</p>
            )}
            {d.over_commit && <p className="text-xs mt-1" style={{ color: "#f87171" }}>⚠ {t("settings.diskbruk.over_commit")}</p>}
          </div>
        ))}
        <div className="divide-y divide-border">
          {info.groups.map(g => (
            <div key={g.key} className="py-3">
              <StorageBar label={t(`settings.diskbruk.groups.${g.key}`)} usedMb={Math.round(g.used_mb)}
                limitMb={g.per_user ? 0 : Math.round(g.limit_mb ?? 0)} />
              {g.home ? (
                <p className="text-xs mt-1 text-muted-foreground">{t(`settings.diskbruk.home.${g.home}`)}</p>
              ) : g.controlled && g.limit_mb != null ? (
                <>
                  {g.per_user && <p className="text-xs mt-1 text-muted-foreground">{t("settings.diskbruk.per_user_limit", { mb: g.limit_mb })}</p>}
                  <LimitField key={`${g.key}-${g.limit_mb}`} value={Math.round(g.limit_mb)} onSave={mb => put({ group: g.key, limit_mb: mb })} />
                  {g.uncontrolled.length > 0 && (
                    <p className="text-xs mt-1 text-muted-foreground">{t("settings.diskbruk.group_partly", { n: g.uncontrolled.length })}</p>
                  )}
                </>
              ) : (
                <p className="text-xs mt-1 text-muted-foreground">{t("settings.diskbruk.no_control")}</p>
              )}
            </div>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">{t("settings.diskbruk.group_hint")}</p>
      </CardContent>
    </Card>
  );
}

function ClassesCard({ info, put }: { info: DiskUsage; put: (p: Put) => Promise<void> }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.diskbruk.classes_title")}</CardTitle>
        <CardDescription>{t("settings.diskbruk.classes_description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="divide-y divide-border">
          {info.classes.map(c => (
            <div key={c.key} className="py-3">
              <StorageBar label={`${t(`settings.diskbruk.classes.${c.key}`)} · ${t(`settings.diskbruk.groups.${c.group}`)}`}
                usedMb={Math.round(c.used_mb * 10) / 10} limitMb={c.per_user ? 0 : Math.round(c.limit_mb ?? 0)} />
              {c.home ? (
                <p className="text-xs mt-1 text-muted-foreground">{t(`settings.diskbruk.home.${c.home}`)}</p>
              ) : c.controlled && c.limit_mb != null ? (
                <>
                  {c.per_user && <p className="text-xs mt-1 text-muted-foreground">{t("settings.diskbruk.per_user_limit", { mb: c.limit_mb })}</p>}
                  <LimitField key={`${c.key}-${c.limit_mb}`} value={Math.round(c.limit_mb)} onSave={mb => put({ class: c.key, limit_mb: mb })} />
                </>
              ) : (
                <p className="text-xs mt-1 text-muted-foreground">{t("settings.diskbruk.no_control")}</p>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ArgusStorageCard({ info, put }: { info: DiskUsage; put: (p: Put) => Promise<void> }) {
  const { t, i18n } = useTranslation();
  const a = info.argus;
  const unmeasured = a.measured_by === "unavailable";
  const last = a.last_run;
  const statusText = (s?: string) =>
    s && KNOWN_STATUS.includes(s) ? t(`settings.diskbruk.status.${s}`) : (s ?? "");
  const warning =
    a.limit_mb === 0 ? t("settings.diskbruk.argus_off")
    : !unmeasured && a.limit_mb < a.used_mb ? t("settings.diskbruk.argus_warn_below_usage")
    : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.diskbruk.argus_title")}</CardTitle>
        <CardDescription>{t("settings.diskbruk.argus_description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {!unmeasured ? (
          <div className="mb-2">
            <StorageBar label={t("settings.diskbruk.argus_bar", { n: a.points.toLocaleString(i18n.language) })}
              usedMb={a.used_mb} limitMb={a.limit_mb} />
            {a.measured_by === "estimate" && (
              <p className="text-xs mt-1.5 text-muted-foreground">{t("settings.diskbruk.measured_estimate")}</p>
            )}
          </div>
        ) : <p className="text-sm mb-2 text-muted-foreground">{t("settings.diskbruk.unavailable")}</p>}
        <FieldRow label={t("settings.diskbruk.limit_label")} hint={t("settings.diskbruk.limit_hint", { def: a.default_mb })}>
          <LimitField key={`argus-${a.limit_mb}`} value={a.limit_mb} step={512} onSave={mb => put({ argus_events_mb: mb })} />
          {warning && <p className="text-xs mt-1.5" style={{ color: "#f5a623" }}>⚠ {warning}</p>}
          {a.floor_mb != null && !a.confirmed && (
            <p className="text-xs mt-1.5" style={{ color: "#f5a623" }}>⚠ {t("settings.diskbruk.argus_floor", { mb: Math.round(a.floor_mb) })}</p>
          )}
        </FieldRow>
        {last && (
          <div className="text-xs mt-3 text-muted-foreground space-y-1">
            {last.ts && (
              <p>{t("settings.diskbruk.last_run", {
                when: new Date(last.ts).toLocaleString(i18n.language), status: statusText(last.status),
                deleted: (last.deleted ?? 0).toLocaleString(i18n.language),
              })}</p>
            )}
            {!!last.rolled_days && (
              <p>{t("settings.diskbruk.rolled_days", { n: last.rolled_days, first: last.first_day, last: last.last_day })}</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function TabDiskbruk() {
  const { t } = useTranslation();
  const [info, setInfo] = useState<DiskUsage | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    // Defaults keep the tab usable against an API that predates the storage budget (until kaare.service restarts).
    apiGetDiskUsage().then(d => { setInfo({ ...d, groups: d.groups ?? [], classes: d.classes ?? [], disks: d.disks ?? [] }); setError(""); }).catch(() => setError(t("settings.diskbruk.load_error")));
  }, [t]);
  useEffect(() => { load(); }, [load]);

  const put = async (p: Put) => {
    const d = await apiPutDiskUsage(p);
    setInfo({ ...d, groups: d.groups ?? [], classes: d.classes ?? [], disks: d.disks ?? [] });
  };

  return (
    <div className="space-y-6">
      {error && <p className="text-sm text-destructive">{error}</p>}
      {!info && !error && <Loader2 className="h-5 w-5 animate-spin" />}
      {info && <DisksAndGroupsCard info={info} put={put} />}
      {/* 2026-11-13: «Skift navn på fanen til Logg/Lagring, så kan innholdet til bilde fanen flyttes dit» */}
      <TabBilder />
      <ExpertOnly>
        {info && <ClassesCard info={info} put={put} />}
        {info && <ArgusStorageCard info={info} put={put} />}
        <LogsAndDataCard />
      </ExpertOnly>
    </div>
  );
}
