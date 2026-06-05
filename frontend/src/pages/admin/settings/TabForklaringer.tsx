import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight } from "lucide-react";

const TAB_COLORS = {
  generelt:      "#9c8d5e",
  ha:            "#4db67a",
  mqtt:          "#ff9f43",
  llm:           "#a29bfe",
  refleksjon:    "#fd79a8",
  nettsok:       "#00cec9",
  bilder:        "#e17055",
  kare:          "#b8c6db",
  integrasjoner: "#55efc4",
  distribusjon:  "#fdcb6e",
  agenter:       "#74b9ff",
};

const GROUP_COLORS = {
  overview: TAB_COLORS.kare,
  admin:    TAB_COLORS.distribusjon,
  internal: TAB_COLORS.refleksjon,
  settings: TAB_COLORS.generelt,
  user:     TAB_COLORS.integrasjoner,
};

function Dot({ color }: { color: string }) {
  return (
    <span style={{
      width: 8, height: 8, borderRadius: "50%", background: color,
      display: "inline-block", flexShrink: 0,
    }} />
  );
}

function GroupHeader({ label, color }: { label: string; color: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, paddingLeft: 2 }}>
      <Dot color={color} />
      <h3 style={{
        fontSize: 11, fontWeight: 700, letterSpacing: "0.08em",
        textTransform: "uppercase", color, margin: 0,
      }}>
        {label}
      </h3>
    </div>
  );
}

function Section({
  sectionKey, title, summary, color, children, open, onToggle,
}: {
  sectionKey: string;
  title: string;
  summary: string;
  color: string;
  children: React.ReactNode;
  open: boolean;
  onToggle: (key: string) => void;
}) {
  return (
    <div style={{
      borderLeft: `3px solid ${color}50`,
      borderRadius: "0 6px 6px 0",
      marginBottom: 6,
      background: "rgba(255,255,255,0.02)",
      overflow: "hidden",
    }}>
      <button
        onClick={() => onToggle(sectionKey)}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 10,
          padding: "11px 14px", background: "transparent",
          border: "none", cursor: "pointer", textAlign: "left",
        }}
      >
        <span style={{ color, flexShrink: 0, display: "flex" }}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <span style={{ fontWeight: 600, fontSize: 13, color: "#e0e0e0", flexShrink: 0 }}>{title}</span>
        {!open && (
          <span style={{ fontSize: 12, color: "#666", marginLeft: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {summary}
          </span>
        )}
      </button>
      {open && (
        <div style={{ padding: "0 14px 14px 14px", borderTop: "1px solid rgba(255,255,255,0.05)" }}>
          {children}
        </div>
      )}
    </div>
  );
}

function Body({ text }: { text: string }) {
  return (
    <p style={{ fontSize: 13, color: "#aaa", marginBottom: 12, marginTop: 12, lineHeight: 1.65 }}>{text}</p>
  );
}

function Items({ items }: { items: string[] }) {
  return (
    <ul style={{ margin: 0, paddingLeft: 18, listStyle: "disc", color: "#bbb" }}>
      {items.map((item, i) => (
        <li key={i} style={{
          fontSize: 13, lineHeight: 1.65, marginBottom: 3,
          color: item.startsWith("⚠") ? "#ff9f43" : "#bbb",
        }}>
          {item}
        </li>
      ))}
    </ul>
  );
}

function SubHeading({ text }: { text: string }) {
  return (
    <p style={{ fontSize: 12, fontWeight: 600, color: "#ccc", margin: "14px 0 6px" }}>{text}</p>
  );
}

function SubSection({
  sectionKey, title, color, children, open, onToggle,
}: {
  sectionKey: string;
  title: string;
  color: string;
  children: React.ReactNode;
  open: boolean;
  onToggle: (key: string) => void;
}) {
  return (
    <div style={{
      borderLeft: `2px solid ${color}40`,
      borderRadius: "0 4px 4px 0",
      marginTop: 8,
      background: "rgba(255,255,255,0.015)",
      overflow: "hidden",
    }}>
      <button
        onClick={() => onToggle(sectionKey)}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 8,
          padding: "8px 12px", background: "transparent",
          border: "none", cursor: "pointer", textAlign: "left",
        }}
      >
        <span style={{ color, opacity: 0.75, flexShrink: 0, display: "flex" }}>
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
        <span style={{ fontWeight: 600, fontSize: 12, color: "#c0c0c0" }}>{title}</span>
      </button>
      {open && (
        <div style={{ padding: "4px 12px 12px 12px", borderTop: "1px solid rgba(255,255,255,0.04)" }}>
          {children}
        </div>
      )}
    </div>
  );
}

function RoleList({ rows }: { rows: { label: string; desc: string; color?: string }[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      {rows.map(({ label, desc, color }) => (
        <div key={label} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
          <span style={{
            fontSize: 11, fontWeight: 700, color: color ?? "#a29bfe",
            minWidth: 110, flexShrink: 0, paddingTop: 2, fontFamily: "monospace",
          }}>{label}</span>
          <span style={{ fontSize: 13, color: "#bbb", lineHeight: 1.5 }}>{desc}</span>
        </div>
      ))}
    </div>
  );
}

function ToolList({ tools }: { tools: { key: string; desc: string }[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {tools.map(({ key, desc }) => (
        <div key={key} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
          <span style={{
            fontSize: 11, color: TAB_COLORS.agenter, minWidth: 160, flexShrink: 0,
            fontFamily: "monospace", paddingTop: 2,
          }}>{key}</span>
          <span style={{ fontSize: 13, color: "#bbb", lineHeight: 1.5 }}>{desc}</span>
        </div>
      ))}
    </div>
  );
}

export default function TabForklaringer() {
  const { t } = useTranslation();
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (key: string) =>
    setOpen(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const isOpen = (key: string) => open.has(key);

  const sec = (key: string, color: string, children: React.ReactNode) => (
    <Section
      key={key}
      sectionKey={key}
      title={t(`forklaringer.${key}.title`)}
      summary={t(`forklaringer.${key}.summary`)}
      color={color}
      open={isOpen(key)}
      onToggle={toggle}
    >
      {children}
    </Section>
  );

  const sub = (key: string, color: string, children: React.ReactNode) => (
    <SubSection
      key={key}
      sectionKey={key}
      title={t(`forklaringer.${key}.title`)}
      color={color}
      open={isOpen(key)}
      onToggle={toggle}
    >
      {children}
    </SubSection>
  );

  const items = (key: string, count: number) =>
    Array.from({ length: count }, (_, i) => t(`forklaringer.${key}.item_${i + 1}`));

  const TOOL_KEYS = [
    "ha_control", "ha_read", "timer", "get_weather", "library", "web_search",
    "kare_image", "view_images", "media", "note", "announce", "reason_freely",
    "memory", "camera", "search_argus", "read_meeting", "mechanic",
    "explore_code", "inspect_system", "ssh_command", "local_command", "restart_docker_container",
  ];

  return (
    <div>
      <div style={{ marginBottom: 28 }}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: "#fff", marginBottom: 6 }}>
          {t("forklaringer.title")}
        </h2>
        <p style={{ fontSize: 13, color: "#777" }}>{t("forklaringer.subtitle")}</p>
      </div>

      {/* ── Group 1: Oversikt og brukere ─────────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <GroupHeader label={t("forklaringer.group_overview")} color={GROUP_COLORS.overview} />

        {sec("dashboard", GROUP_COLORS.overview, <>
          <Body text={t("forklaringer.dashboard.body")} />
          <Items items={items("dashboard", 4)} />
        </>)}

        {sec("users", GROUP_COLORS.overview, <>
          <Body text={t("forklaringer.users.body")} />
          <SubHeading text={t("forklaringer.users.roles_intro")} />
          <RoleList rows={[
            { label: "child",       desc: t("forklaringer.users.roles.child") },
            { label: "teen",        desc: t("forklaringer.users.roles.teen") },
            { label: "young_adult", desc: t("forklaringer.users.roles.young_adult") },
            { label: "adult",       desc: t("forklaringer.users.roles.adult") },
            { label: "admin",       desc: t("forklaringer.users.roles.admin") },
          ]} />
          <SubHeading text={t("forklaringer.users.vpn_intro")} />
          <RoleList rows={[
            { label: "local_only",  desc: t("forklaringer.users.vpn.local_only"),  color: "#ff9f43" },
            { label: "ai_only",     desc: t("forklaringer.users.vpn.ai_only"),     color: "#fdcb6e" },
            { label: "full_access", desc: t("forklaringer.users.vpn.full_access"), color: "#55efc4" },
          ]} />
          <SubHeading text={t("forklaringer.users.tools_intro")} />
          <ToolList tools={TOOL_KEYS.map(k => ({ key: k, desc: t(`forklaringer.users.tools.${k}`) }))} />
          <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 5 }}>
            <p style={{ fontSize: 13, color: "#bbb", lineHeight: 1.6 }}>{t("forklaringer.users.pin")}</p>
            <p style={{ fontSize: 13, color: "#bbb", lineHeight: 1.6 }}>{t("forklaringer.users.voice_reg")}</p>
            <p style={{ fontSize: 13, color: "#bbb", lineHeight: 1.6 }}>{t("forklaringer.users.vpn_qr")}</p>
            <p style={{ fontSize: 13, color: "#ff9f43", lineHeight: 1.6 }}>{t("forklaringer.users.warning_delete")}</p>
            <p style={{ fontSize: 13, color: "#ff9f43", lineHeight: 1.6 }}>{t("forklaringer.users.warning_vpn")}</p>
          </div>
        </>)}
      </div>

      {/* ── Group 2: Administrasjon og drift ─────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <GroupHeader label={t("forklaringer.group_admin")} color={GROUP_COLORS.admin} />

        {sec("vedlikehold", GROUP_COLORS.admin, <>
          <Body text={t("forklaringer.vedlikehold.body")} />
          <Items items={items("vedlikehold", 5)} />
        </>)}

        {sec("verktoy", GROUP_COLORS.admin, <>
          <Body text={t("forklaringer.verktoy.body")} />

          {sub("verktoy_ssh", TAB_COLORS.nettsok, <>
            <Body text={t("forklaringer.verktoy_ssh.body")} />
            <Items items={items("verktoy_ssh", 3)} />
          </>)}

          {sub("verktoy_timer", TAB_COLORS.mqtt, <>
            <Items items={[
              t("forklaringer.verktoy.item_1"),
              t("forklaringer.verktoy_timer.item_1"),
            ]} />
          </>)}

          {sub("verktoy_log", GROUP_COLORS.admin, <>
            <Items items={[
              t("forklaringer.verktoy.item_2"),
              t("forklaringer.verktoy.item_3"),
            ]} />
          </>)}
        </>)}

        {sec("aliaser", GROUP_COLORS.admin, <>
          <Body text={t("forklaringer.aliaser.body")} />
          <Items items={items("aliaser", 4)} />
        </>)}

        {sec("noder", GROUP_COLORS.admin, <>
          <Body text={t("forklaringer.noder.body")} />

          {sub("noder_kategorier", TAB_COLORS.kare, <>
            <Items items={items("noder_kategorier", 3)} />
          </>)}

          {sub("noder_lyd_typer", TAB_COLORS.mqtt, <>
            <RoleList rows={[
              { label: "ha_media_player", desc: t("forklaringer.noder.types.ha_media_player"), color: TAB_COLORS.ha },
              { label: "esp32",           desc: t("forklaringer.noder.types.esp32"),           color: TAB_COLORS.nettsok },
              { label: "wyoming",         desc: t("forklaringer.noder.types.wyoming"),         color: TAB_COLORS.kare },
              { label: "chromecast",      desc: t("forklaringer.noder.types.chromecast"),      color: TAB_COLORS.mqtt },
              { label: "snapcast",        desc: t("forklaringer.noder.types.snapcast"),        color: TAB_COLORS.llm },
              { label: "airplay",         desc: t("forklaringer.noder.types.airplay"),         color: TAB_COLORS.refleksjon },
              { label: "dlna",            desc: t("forklaringer.noder.types.dlna"),            color: TAB_COLORS.bilder },
            ]} />
          </>)}

          {sub("noder_skjerm_typer", TAB_COLORS.bilder, <>
            <RoleList rows={[
              { label: "apple_tv",   desc: t("forklaringer.noder.types.apple_tv"),   color: TAB_COLORS.integrasjoner },
              { label: "samsung_tv", desc: t("forklaringer.noder.types.samsung_tv"), color: TAB_COLORS.ha },
              { label: "android_tv", desc: t("forklaringer.noder.types.android_tv"), color: TAB_COLORS.llm },
              { label: "google_tv",  desc: t("forklaringer.noder.types.google_tv"),  color: TAB_COLORS.nettsok },
              { label: "fire_tv",    desc: t("forklaringer.noder.types.fire_tv"),    color: TAB_COLORS.mqtt },
              { label: "lg_tv",      desc: t("forklaringer.noder.types.lg_tv"),      color: TAB_COLORS.refleksjon },
              { label: "projector",  desc: t("forklaringer.noder.types.projector"),  color: TAB_COLORS.generelt },
            ]} />
          </>)}
        </>)}

        {sec("kameraer", GROUP_COLORS.admin, <>
          <Items items={items("kameraer", 5)} />
        </>)}
      </div>

      {/* ── Group 3: Kåres indre liv ──────────────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <GroupHeader label={t("forklaringer.group_internal")} color={GROUP_COLORS.internal} />

        {sec("agenter", GROUP_COLORS.internal, <>
          <Body text={t("forklaringer.agenter.body")} />
          <RoleList rows={[
            { label: "Miss Kåre",   desc: t("forklaringer.agenter.miss_kare"),   color: TAB_COLORS.refleksjon },
            { label: "Miss Library",desc: t("forklaringer.agenter.miss_library"),color: TAB_COLORS.nettsok },
            { label: "Mechanic",    desc: t("forklaringer.agenter.mechanic"),     color: TAB_COLORS.agenter },
            { label: "Jing",        desc: t("forklaringer.agenter.jing"),         color: TAB_COLORS.kare },
            { label: "Jang",        desc: t("forklaringer.agenter.jang"),         color: TAB_COLORS.distribusjon },
          ]} />
        </>)}

        {sec("refleksjoner", GROUP_COLORS.internal, <>
          <Body text={t("forklaringer.refleksjoner.body")} />
          <Items items={items("refleksjoner", 5)} />
        </>)}
      </div>

      {/* ── Group 4: Innstillinger ────────────────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <GroupHeader label={t("forklaringer.group_settings")} color={GROUP_COLORS.settings} />

        {sec("innst_generelt", TAB_COLORS.generelt, <>
          <Items items={items("innst_generelt", 3)} />
        </>)}

        {sec("innst_ha", TAB_COLORS.ha, <>
          <Body text={t("forklaringer.innst_ha.body")} />
          <Items items={items("innst_ha", 3)} />
        </>)}

        {sec("innst_mqtt", TAB_COLORS.mqtt, <>
          <Items items={items("innst_mqtt", 4)} />
        </>)}

        {sec("innst_llm", TAB_COLORS.llm, <>
          <Body text={t("forklaringer.innst_llm.body")} />
          <SubHeading text="Modellroller:" />
          <RoleList rows={[
            { label: "Kåre",        desc: t("forklaringer.innst_llm.role_kare"),      color: TAB_COLORS.llm },
            { label: "Miss Kåre",   desc: t("forklaringer.innst_llm.role_miss_kare"), color: TAB_COLORS.refleksjon },
            { label: "Miss Library",desc: t("forklaringer.innst_llm.role_library"),   color: TAB_COLORS.nettsok },
            { label: "Mechanic",    desc: t("forklaringer.innst_llm.role_mechanic"),  color: TAB_COLORS.agenter },
            { label: "Fallback",    desc: t("forklaringer.innst_llm.role_fallback"),  color: TAB_COLORS.mqtt },
            { label: "Sky",         desc: t("forklaringer.innst_llm.role_sky"),       color: TAB_COLORS.kare },
          ]} />
          <SubHeading text="Andre begreper:" />
          <Items items={items("innst_llm", 5)} />
        </>)}

        {sec("innst_refleksjon", TAB_COLORS.refleksjon, <>
          <Items items={items("innst_refleksjon", 4)} />
        </>)}

        {sec("innst_nett", TAB_COLORS.nettsok, <>
          <Items items={items("innst_nett", 5)} />
        </>)}

        {sec("innst_bilder", TAB_COLORS.bilder, <>
          <Items items={items("innst_bilder", 4)} />
        </>)}

        {sec("innst_kare", TAB_COLORS.kare, <>
          <Items items={items("innst_kare", 9)} />
        </>)}

        {sec("innst_integrasjoner", TAB_COLORS.integrasjoner, <>
          <Items items={items("innst_integrasjoner", 3)} />
        </>)}

        {sec("innst_distribusjon", TAB_COLORS.distribusjon, <>
          <Items items={items("innst_distribusjon", 4)} />
        </>)}

        {sec("innst_agenter", TAB_COLORS.agenter, <>
          <Items items={items("innst_agenter", 3)} />
        </>)}
      </div>

      {/* ── Group 5: Som bruker ───────────────────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <GroupHeader label={t("forklaringer.group_user")} color={GROUP_COLORS.user} />

        {sec("bruker_chat", GROUP_COLORS.user, <>
          <Items items={items("bruker_chat", 6)} />
        </>)}

        {sec("bruker_refleksjoner", GROUP_COLORS.user, <>
          <Items items={items("bruker_refleksjoner", 4)} />
        </>)}

        {sec("bruker_stemmeopplaasing", GROUP_COLORS.user, <>
          <Items items={items("bruker_stemmeopplaasing", 5)} />
        </>)}
      </div>
    </div>
  );
}
