import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { apiGetAgentTools, apiPutAgentTools, apiGetMeetingRoles, apiPutMeetingRoles, type AgentToolsConfig, type MeetingRolesConfig } from "@/services/api";
import { useSaveState, SaveFeedback, RadioOption } from "./shared";

const DEFAULT_AGENT_TOOLS: AgentToolsConfig = {
  mechanic: { utforsk: true, inspiser: true, "nettsøk": true, "søk_argus": true, shell: false, hukommelse: true },
  miss_kare:   { "spør_frøken_library": true },
  miss_library: { wiki: false },
};

const DEFAULT_MEETING_ROLES: MeetingRolesConfig = {
  mechanic: "undersøker", mechanic_custom: "", mechanic_default: "",
  miss_kare: "empatisk", miss_kare_custom: "", miss_kare_default: "",
};

export default function TabAgenter() {
  const { t } = useTranslation();
  const ss         = useSaveState();
  const ssPsRole   = useSaveState();
  const ssMkRole   = useSaveState();
  const [cfg, setCfg]       = useState<AgentToolsConfig>(DEFAULT_AGENT_TOOLS);
  const [roles, setRoles]   = useState<MeetingRolesConfig>(DEFAULT_MEETING_ROLES);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    mechanic: true, miss_kare: true, miss_library: true,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([apiGetAgentTools(), apiGetMeetingRoles()])
      .then(([tools, r]) => { setCfg({ ...DEFAULT_AGENT_TOOLS, ...tools }); setRoles(r); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const toggle = (agent: keyof AgentToolsConfig, tool: string, val: boolean) =>
    setCfg(prev => ({ ...prev, [agent]: { ...prev[agent], [tool]: val } }));
  const toggleExpand = (key: string) =>
    setExpanded(prev => ({ ...prev, [key]: !prev[key] }));

  const saveTools = async () => {
    ss.saving();
    try { await apiPutAgentTools(cfg); ss.saved(); }
    catch { ss.error(); }
  };
  const savePsRole = async () => {
    ssPsRole.saving();
    try {
      await apiPutMeetingRoles({
        mechanic: roles.mechanic,
        ...(roles.mechanic === "egendefinert" ? { mechanic_custom: roles.mechanic_custom } : {}),
      });
      ssPsRole.saved();
    } catch { ssPsRole.error(); }
  };
  const saveMkRole = async () => {
    ssMkRole.saving();
    try {
      await apiPutMeetingRoles({
        miss_kare: roles.miss_kare,
        ...(roles.miss_kare === "egendefinert" ? { miss_kare_custom: roles.miss_kare_custom } : {}),
      });
      ssMkRole.saved();
    } catch { ssMkRole.error(); }
  };

  if (loading) return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground mt-6" />;

  const ColorSwitch = ({ agent, tool, defaultOff }: { agent: keyof AgentToolsConfig; tool: string; defaultOff?: boolean }) => (
    <Switch
      checked={cfg[agent]?.[tool] ?? !defaultOff}
      onCheckedChange={v => toggle(agent, tool, v)}
      className="data-checked:bg-green-600 data-unchecked:bg-red-600"
    />
  );

  type ToolDef = { key: string; label: string; description: string; defaultOff?: boolean };

  const psTools: ToolDef[] = [
    { key: "utforsk",        label: t("settings.agenter.tools.utforsk"),        description: t("settings.agenter.tools.utforsk_desc") },
    { key: "inspiser",       label: t("settings.agenter.tools.inspiser"),       description: t("settings.agenter.tools.inspiser_desc") },
    { key: "nettsøk",        label: t("settings.agenter.tools.nettsøk"),        description: t("settings.agenter.tools.nettsøk_desc") },
    { key: "søk_argus", label: t("settings.agenter.tools.søk_argus"), description: t("settings.agenter.tools.søk_argus_desc") },
    { key: "shell",          label: t("settings.agenter.tools.shell"),          description: t("settings.agenter.tools.shell_desc"), defaultOff: true },
    { key: "hukommelse",     label: t("settings.agenter.tools.hukommelse"),     description: t("settings.agenter.tools.hukommelse_desc") },
  ];
  const mkTools: ToolDef[] = [
    { key: "spør_frøken_library", label: t("settings.agenter.tools.spør_frøken_library"), description: t("settings.agenter.tools.spør_frøken_library_desc") },
  ];
  const mlTools: ToolDef[] = [
    { key: "wiki", label: t("settings.agenter.tools.wiki"), description: t("settings.agenter.tools.wiki_desc"), defaultOff: true },
  ];

  const SectionLabel = ({ text }: { text: string }) => (
    <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground mb-3">{text}</p>
  );

  const ToolList = ({ agent, tools }: { agent: keyof AgentToolsConfig; tools: ToolDef[] }) => (
    <div className="space-y-3">
      {tools.map(tool => (
        <div key={tool.key} className="flex items-center justify-between py-1">
          <div>
            <p className="text-sm font-mono text-white/90">{tool.label}</p>
            <p className="text-xs text-muted-foreground">{tool.description}</p>
          </div>
          <ColorSwitch agent={agent} tool={tool.key} defaultOff={tool.defaultOff} />
        </div>
      ))}
    </div>
  );

  return (
    <div className="space-y-4">

      {/* ── Mechanic ── */}
      <Card className="bg-[#1a1a1a] border-[#333]">
        <CardHeader className="cursor-pointer select-none pb-3" onClick={() => toggleExpand("mechanic")}>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-sm font-semibold text-white">{t("settings.agenter.mechanic.label")}</CardTitle>
              <CardDescription className="text-xs text-muted-foreground">{t("settings.agenter.mechanic.description")}</CardDescription>
            </div>
            {expanded.mechanic ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
          </div>
        </CardHeader>
        {expanded.mechanic && (
          <CardContent className="space-y-6 pt-0">
            <div>
              <SectionLabel text={t("settings.agenter.section_tools")} />
              <ToolList agent="mechanic" tools={psTools} />
            </div>
            <Separator className="bg-[#333]" />
            <div>
              <SectionLabel text={t("settings.agenter.section_role_dev")} />
              <div className="divide-y divide-[#2a2a2a] mb-4">
                {([ ["undersøker", "undersøker"], ["kritiker", "kritiker"], ["analytiker", "analytiker"], ["egendefinert", "egendefinert"] ] as [string, string][]).map(([value, key]) => (
                  <RadioOption
                    key={value}
                    value={value}
                    current={roles.mechanic}
                    onChange={v => setRoles(r => ({ ...r, mechanic: v }))}
                    label={t(`settings.agenter.mechanic.roles.${key}_label`)}
                    description={t(`settings.agenter.mechanic.roles.${key}_desc`)}
                  />
                ))}
              </div>
              {roles.mechanic === "egendefinert" && (
                <div className="space-y-2 mt-2">
                  {roles.mechanic_custom === "" && roles.mechanic_default && (
                    <div className="flex justify-end">
                      <Button variant="outline" size="sm"
                        onClick={() => setRoles(r => ({ ...r, mechanic_custom: r.mechanic_default }))}>
                        {t("settings.agenter.role_load_default")}
                      </Button>
                    </div>
                  )}
                  <textarea
                    value={roles.mechanic_custom}
                    onChange={e => setRoles(r => ({ ...r, mechanic_custom: e.target.value }))}
                    placeholder={t("settings.agenter.role_custom_placeholder")}
                    rows={14}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <p className="text-xs text-muted-foreground">{t("settings.agenter.role_custom_hint")}</p>
                </div>
              )}
              <p className="text-xs text-muted-foreground mt-3">{t("settings.agenter.role_hot_reload_hint")}</p>
              <div className="flex items-center gap-3 mt-4">
                <Button size="sm" onClick={savePsRole} disabled={ssPsRole.state === "saving"}>
                  {ssPsRole.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {t("settings.agenter.role_save")}
                </Button>
                <SaveFeedback state={ssPsRole.state} />
              </div>
            </div>
          </CardContent>
        )}
      </Card>

      {/* ── Miss Kåre ── */}
      <Card className="bg-[#1a1a1a] border-[#333]">
        <CardHeader className="cursor-pointer select-none pb-3" onClick={() => toggleExpand("miss_kare")}>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-sm font-semibold text-white">{t("settings.agenter.miss_kare.label")}</CardTitle>
              <CardDescription className="text-xs text-muted-foreground">{t("settings.agenter.miss_kare.description")}</CardDescription>
            </div>
            {expanded.miss_kare ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
          </div>
        </CardHeader>
        {expanded.miss_kare && (
          <CardContent className="space-y-6 pt-0">
            <div>
              <SectionLabel text={t("settings.agenter.section_tools")} />
              <ToolList agent="miss_kare" tools={mkTools} />
            </div>
            <Separator className="bg-[#333]" />
            <div>
              <SectionLabel text={t("settings.agenter.section_role_reflection")} />
              <div className="divide-y divide-[#2a2a2a] mb-4">
                {([ ["empatisk", "empatisk"], ["analytiker", "analytiker"], ["utfordrende", "utfordrende"], ["egendefinert", "egendefinert"] ] as [string, string][]).map(([value, key]) => (
                  <RadioOption
                    key={value}
                    value={value}
                    current={roles.miss_kare}
                    onChange={v => setRoles(r => ({ ...r, miss_kare: v }))}
                    label={t(`settings.agenter.miss_kare.roles.${key}_label`)}
                    description={t(`settings.agenter.miss_kare.roles.${key}_desc`)}
                  />
                ))}
              </div>
              {roles.miss_kare === "egendefinert" && (
                <div className="space-y-2 mt-2">
                  {roles.miss_kare_custom === "" && roles.miss_kare_default && (
                    <div className="flex justify-end">
                      <Button variant="outline" size="sm"
                        onClick={() => setRoles(r => ({ ...r, miss_kare_custom: r.miss_kare_default }))}>
                        {t("settings.agenter.role_load_default")}
                      </Button>
                    </div>
                  )}
                  <textarea
                    value={roles.miss_kare_custom}
                    onChange={e => setRoles(r => ({ ...r, miss_kare_custom: e.target.value }))}
                    placeholder={t("settings.agenter.role_custom_placeholder")}
                    rows={14}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <p className="text-xs text-muted-foreground">{t("settings.agenter.role_custom_hint")}</p>
                </div>
              )}
              <p className="text-xs text-muted-foreground mt-3">{t("settings.agenter.role_hot_reload_hint")}</p>
              <div className="flex items-center gap-3 mt-4">
                <Button size="sm" onClick={saveMkRole} disabled={ssMkRole.state === "saving"}>
                  {ssMkRole.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {t("settings.agenter.role_save")}
                </Button>
                <SaveFeedback state={ssMkRole.state} />
              </div>
            </div>
          </CardContent>
        )}
      </Card>

      {/* ── Frøken Library ── */}
      <Card className="bg-[#1a1a1a] border-[#333]">
        <CardHeader className="cursor-pointer select-none pb-3" onClick={() => toggleExpand("miss_library")}>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-sm font-semibold text-white">{t("settings.agenter.miss_library.label")}</CardTitle>
              <CardDescription className="text-xs text-muted-foreground">{t("settings.agenter.miss_library.description")}</CardDescription>
            </div>
            {expanded.miss_library ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
          </div>
        </CardHeader>
        {expanded.miss_library && (
          <CardContent className="pt-0">
            <ToolList agent="miss_library" tools={mlTools} />
          </CardContent>
        )}
      </Card>

      <div className="flex items-center gap-3">
        <Button size="sm" onClick={saveTools} disabled={ss.state === "saving"}>
          {ss.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {t("settings.agenter.save_tools")}
        </Button>
        <SaveFeedback state={ss.state} />
      </div>
    </div>
  );
}
