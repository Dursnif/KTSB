import { useTranslation } from "react-i18next";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import TabGenerelt from "./settings/TabGenerelt";
import TabHomeAssistant from "./settings/TabHomeAssistant";
import TabMqtt from "./settings/TabMqtt";
import { TabLlm } from "./settings/llm/TabLlm";
import TabNettsokOgVaer from "./settings/TabNettsokOgVaer";
import TabRefleksjon from "./settings/TabRefleksjon";
import TabBilder from "./settings/TabBilder";
import TabKareInnstillinger from "./settings/TabKareInnstillinger";
import TabIntegrasjoner from "./settings/TabIntegrasjoner";
import TabDistribusjon from "./settings/TabDistribusjon";
import TabAgenter from "./settings/TabAgenter";
import TabForklaringer from "./settings/TabForklaringer";

export default function Settings() {
  const { t } = useTranslation();
  const dot = (color: string) => (
    <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, display: "inline-block", marginRight: 5, flexShrink: 0 }} />
  );

  return (
    <div>
      <h1 style={{ color: "#fff", fontSize: 22, fontWeight: 700, margin: "0 0 28px" }}>{t("settings.title")}</h1>
      <Tabs defaultValue="generelt" className="w-full">
        {/* Scrollable on mobile so tabs stay on one row instead of wrapping over content */}
        <div className="w-full overflow-x-auto mb-6 pb-1">
          <TabsList className="flex-nowrap h-auto gap-1">
            <TabsTrigger value="generelt"    className="gap-1 whitespace-nowrap">{dot("#9c8d5e")}{t("settings.tabs.generelt")}</TabsTrigger>
            <TabsTrigger value="ha"          className="gap-1 whitespace-nowrap">{dot("#4db67a")}{t("settings.tabs.ha")}</TabsTrigger>
            <TabsTrigger value="mqtt"        className="gap-1 whitespace-nowrap">{dot("#ff9f43")}{t("settings.tabs.mqtt")}</TabsTrigger>
            <TabsTrigger value="llm"         className="gap-1 whitespace-nowrap">{dot("#a29bfe")}{t("settings.tabs.llm")}</TabsTrigger>
            <TabsTrigger value="refleksjon"  className="gap-1 whitespace-nowrap">{dot("#fd79a8")}{t("settings.tabs.refleksjon")}</TabsTrigger>
            <TabsTrigger value="nettsok"     className="gap-1 whitespace-nowrap">{dot("#00cec9")}{t("settings.tabs.nettsok")}</TabsTrigger>
            <TabsTrigger value="bilder"      className="gap-1 whitespace-nowrap">{dot("#e17055")}{t("settings.tabs.bilder")}</TabsTrigger>
            <TabsTrigger value="kare"           className="gap-1 whitespace-nowrap">{dot("#b8c6db")}{t("settings.tabs.kare")}</TabsTrigger>
            <TabsTrigger value="integrasjoner" className="gap-1 whitespace-nowrap">{dot("#55efc4")}{t("settings.tabs.integrasjoner")}</TabsTrigger>
            <TabsTrigger value="distribusjon"  className="gap-1 whitespace-nowrap">{dot("#fdcb6e")}{t("settings.tabs.distribusjon")}</TabsTrigger>
            <TabsTrigger value="agenter"       className="gap-1 whitespace-nowrap">{dot("#74b9ff")}{t("settings.tabs.agenter")}</TabsTrigger>
            <TabsTrigger value="forklaringer"  className="gap-1 whitespace-nowrap">{dot("#c0c0c0")}{t("settings.tabs.forklaringer")}</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="generelt">      <div data-tab="generelt"><TabGenerelt /></div></TabsContent>
        <TabsContent value="ha">            <div data-tab="ha"><TabHomeAssistant /></div></TabsContent>
        <TabsContent value="mqtt">          <div data-tab="mqtt"><TabMqtt /></div></TabsContent>
        <TabsContent value="llm">           <div data-tab="llm"><TabLlm /></div></TabsContent>
        <TabsContent value="refleksjon">    <div data-tab="refleksjon"><TabRefleksjon /></div></TabsContent>
        <TabsContent value="nettsok">       <div data-tab="nettsok"><TabNettsokOgVaer /></div></TabsContent>
        <TabsContent value="bilder">        <div data-tab="bilder"><TabBilder /></div></TabsContent>
        <TabsContent value="kare">          <div data-tab="kare"><TabKareInnstillinger /></div></TabsContent>
        <TabsContent value="integrasjoner"> <div data-tab="integrasjoner"><TabIntegrasjoner /></div></TabsContent>
        <TabsContent value="distribusjon">  <div data-tab="distribusjon"><TabDistribusjon /></div></TabsContent>
        <TabsContent value="agenter">       <div data-tab="agenter"><TabAgenter /></div></TabsContent>
        <TabsContent value="forklaringer">  <div data-tab="forklaringer"><TabForklaringer /></div></TabsContent>
      </Tabs>
    </div>
  );
}
