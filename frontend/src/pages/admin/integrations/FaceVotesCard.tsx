// Extra face recognition (P144-s19, P130 port): a second, independent face sensor — Double Take over MQTT first — whose
// vote joins the Frigate user's claim check (never a fact). Basic: on/off + status line; Expert: provider, topic prefix,
// optional REST address for the test button, lowest confidence that counts. How much each detector counts lives in its
// own card, «Ansiktssensorer» (FaceSensorsCard, P144-s29b). Self-contained, dropped into
// Integrations → Frigate with one line (I21). KTSB only subscribes; it never publishes to the provider.
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Loader2 } from "lucide-react";
import {
  apiGetFaceVotesStatus, apiGetFrigateUserPipe, apiPutFrigateUserPipe, apiTestFaceVotes,
  type FaceVotesSettings, type FaceVotesStatus,
} from "@/services/api";
import { ExpertOnly, FieldRow, SaveFeedback } from "../settings/shared";
import { useSaveState } from "../settings/useSaveState";

const DEFAULTS: FaceVotesSettings = {
  enabled: false, provider: "double_take", topic_prefix: "double-take", rest_url: "", min_confidence: 0,
};
const STATUS_POLL_MS = 15000;

function clock(ts: number | null): string {
  return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

export default function FaceVotesCard() {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`integrations.face_votes.${key}`, o);
  const [cfg, setCfg] = useState<FaceVotesSettings | null>(null);
  const [pipeOn, setPipeOn] = useState(false);
  const [status, setStatus] = useState<FaceVotesStatus | null>(null);
  const [prefix, setPrefix] = useState("");
  const [restUrl, setRestUrl] = useState("");
  const [minConf, setMinConf] = useState("0");
  const [test, setTest] = useState<{ ok: boolean; error: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const ssSwitch = useSaveState();
  const ssForm = useSaveState();

  const fill = (fv: FaceVotesSettings) => {
    setCfg(fv);
    setPrefix(fv.topic_prefix);
    setRestUrl(fv.rest_url);
    setMinConf(String(fv.min_confidence));
  };

  useEffect(() => {
    apiGetFrigateUserPipe().then(p => { setPipeOn(p.enabled); fill(p.face_votes ?? DEFAULTS); }).catch(() => {});
  }, []);

  const refresh = useCallback(() => { apiGetFaceVotesStatus().then(setStatus).catch(() => {}); }, []);
  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, STATUS_POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  if (!cfg) return null;

  const save = async (changes: Partial<FaceVotesSettings>, ss: ReturnType<typeof useSaveState>) => {
    ss.saving();
    try {
      const p = await apiPutFrigateUserPipe({ face_votes: changes });
      setPipeOn(p.enabled);
      fill(p.face_votes ?? DEFAULTS);
      ss.saved();
      refresh();
    } catch { ss.error(); }
  };
  const saveForm = () => {
    const n = Number(minConf);
    if (!prefix.trim() || !Number.isFinite(n) || n < 0 || n > 100) { ssForm.error(); return; }
    save({ topic_prefix: prefix.trim(), rest_url: restUrl.trim(), min_confidence: n }, ssForm);
  };
  const runTest = async () => {
    setTesting(true);
    try {
      const r = await apiTestFaceVotes(restUrl.trim());
      setTest({ ok: r.ok, error: r.error || String(r.status ?? "") });
    } catch { setTest({ ok: false, error: "?" }); }
    setTesting(false);
  };

  const state = !status?.enabled ? "off" : status.available;
  const colour = state === "online" ? "#16a34a" : state === "offline" ? "#dc2626" : "#888";

  return (
    <Card>
      <CardHeader>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <CardTitle>{k("title")}</CardTitle>
            <CardDescription>{k("description")}</CardDescription>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <SaveFeedback state={ssSwitch.state} />
            <Switch checked={cfg.enabled} onCheckedChange={on => save({ enabled: on }, ssSwitch)}
              aria-label={k("switch_label")} />
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div style={{ fontSize: 13, color: "#ccc", display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <span>{k("status")}:</span>
          <span style={{ color: colour }}>{k(`status_${state}`)}</span>
          {status?.enabled && status.votes_seen > 0 && (
            <span style={{ color: "#888" }}>
              · {k("votes_seen", { n: status.votes_seen })}
              {status.last_vote_ts ? ` · ${k("last_vote", { time: clock(status.last_vote_ts) })}` : ""}
            </span>
          )}
        </div>
        {cfg.enabled && !pipeOn && (
          <div style={{ fontSize: 12, color: "#f59e0b", marginTop: 6 }}>{k("needs_frigate_user")}</div>
        )}
        {status?.rest_read?.ok === false && (
          <div style={{ fontSize: 12, color: "#f59e0b", marginTop: 6 }}>
            {k("rest_read_fail", { error: status.rest_read.error })}
          </div>
        )}
        <div style={{ fontSize: 12, color: "#777", marginTop: 4 }}>{k("restart_hint")}</div>
        <ExpertOnly><>
          <div className="divide-y divide-border" style={{ marginTop: 12 }}>
            <FieldRow label={k("provider_label")} hint={k("provider_hint")}>
              <span style={{ fontSize: 13 }}>{k("provider_double_take")}</span>
            </FieldRow>
            <FieldRow label={k("topic_label")} hint={k("topic_hint")}>
              <Input value={prefix} className="w-48" placeholder="double-take"
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPrefix(e.target.value)} />
            </FieldRow>
            <FieldRow label={k("rest_label")} hint={k("rest_hint")}>
              <div className="flex gap-2" style={{ flexWrap: "wrap", alignItems: "center" }}>
                <Input value={restUrl} placeholder="http://192.168.0.x:3000"
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setRestUrl(e.target.value)} />
                <Button variant="outline" onClick={runTest} disabled={testing || !restUrl.trim()}>
                  {testing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {k("test")}
                </Button>
                {test && (
                  <span style={{ fontSize: 12, color: test.ok ? "#16a34a" : "#dc2626" }}>
                    {test.ok ? k("test_ok") : k("test_fail", { error: test.error })}
                  </span>
                )}
              </div>
            </FieldRow>
            <FieldRow label={k("min_label")} hint={k("min_hint")}>
              <Input type="number" min={0} max={100} step={1} value={minConf} className="w-28"
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setMinConf(e.target.value)} />
            </FieldRow>
          </div>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={saveForm} disabled={ssForm.state === "saving"}>
              {ssForm.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssForm.state} />
          </div>
        </></ExpertOnly>
      </CardContent>
    </Card>
  );
}
