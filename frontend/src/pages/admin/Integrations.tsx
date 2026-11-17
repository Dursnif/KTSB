// Integrations (P144-s01, first step of I21): Home Assistant, MQTT, Frigate and Plex moved out of Settings into
// their own page. The Frigate tab is where P144's Frigate blocks land.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SettingsLevelProvider } from "@/contexts/SettingsLevelContext";
import { SettingsLevelToggle } from "./settings/shared";
import TabHomeAssistant from "./integrations/TabHomeAssistant";
import TabMqtt from "./integrations/TabMqtt";
import TabFrigate from "./integrations/TabFrigate";
import PlexCard from "./integrations/PlexCard";

// Tab dot colours — the same colours colour the card top borders (index.css, [data-tab=…]).
const TABS = [
  { key: "ha",      color: "#4db67a" },
  { key: "mqtt",    color: "#ff9f43" },
  { key: "frigate", color: "#55efc4" },
  { key: "plex",    color: "#e5a00d" },
] as const;

function IntegrationsInner() {
  const { t } = useTranslation();
  const [tab, setTab] = useState("ha");
  const dot = (color: string) => (
    <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, display: "inline-block", marginRight: 5, flexShrink: 0 }} />
  );

  return (
    <div>
      <h1>{t("integrations.title")}</h1>
      <p style={{ color: "#999", fontSize: 13, marginBottom: 24, marginTop: -16 }}>{t("integrations.description")}</p>
      <Tabs value={tab} onValueChange={(v) => { if (v) setTab(v); }} className="w-full">
        {/* Tabs left, Basic/Expert right — wraps on narrow screens; the h1 rule above stays full width */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div className="overflow-x-auto pb-1" style={{ maxWidth: "100%" }}>
            <TabsList className="flex-nowrap h-auto gap-1">
              {TABS.map(({ key, color }) => (
                <TabsTrigger key={key} value={key} className="gap-1 whitespace-nowrap">{dot(color)}{t(`integrations.tabs.${key}`)}</TabsTrigger>
              ))}
            </TabsList>
          </div>
          <SettingsLevelToggle />
        </div>

        <TabsContent value="ha">      <div data-tab="ha"><TabHomeAssistant /></div></TabsContent>
        <TabsContent value="mqtt">    <div data-tab="mqtt"><TabMqtt /></div></TabsContent>
        <TabsContent value="frigate"> <div data-tab="frigate"><TabFrigate /></div></TabsContent>
        <TabsContent value="plex">    <div data-tab="plex"><PlexCard /></div></TabsContent>
      </Tabs>
    </div>
  );
}

export default function Integrations() {
  return (
    <SettingsLevelProvider>
      <IntegrationsInner />
    </SettingsLevelProvider>
  );
}
