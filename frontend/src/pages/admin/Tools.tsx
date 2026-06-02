import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import {
  apiGetTimerSettings, apiPutTimerSettings,
  apiGetSshNodes, apiPutSshNodes, apiTestSshNode,
  type SshNodeConfig, type SshNodesData,
} from "../../services/api";

interface Timer {
  id: string;
  prompt: string;
  fires_at: string;
  remaining_seconds: number;
  notify: boolean;
  repeat?: string | null;
  at_time?: string | null;
}

interface ToolCall {
  ts: string;
  source: string;
  tool?: string;
  event?: string;
  args?: Record<string, unknown>;
  result_preview?: string;
  duration_ms?: number;
  timer_id?: string;
  prompt_preview?: string;
  error?: string;
}

const SOURCE_COLOR: Record<string, string> = {
  kare:         "#6c8ebf",
  timer:        "#d79b00",
  miss_kare:    "#c084fc",
  miss_library: "#5ba8a0",
  mechanic:     "#82b366",
  jing:         "#82b366",
  jang:         "#9673a6",
};

function sourceColor(s: string) { return SOURCE_COLOR[s] ?? "#888"; }

function formatRemaining(secs: number, firingSoonLabel: string): string {
  if (secs <= 0) return firingSoonLabel;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}t ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatTime(ts: string): string {
  try {
    const locale = i18n.language === "nb" ? "nb-NO" : i18n.language;
    return new Date(ts).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return ts; }
}

function eventLabel(call: ToolCall, unknownLabel: string): string {
  if (call.tool) return call.tool;
  if (call.event) return call.event;
  return unknownLabel;
}

function eventColor(call: ToolCall): string {
  const label = call.tool ?? call.event ?? "";
  if (label.includes("timer"))   return SOURCE_COLOR.timer;
  if (label.includes("styr"))    return "#e07070";
  if (label.includes("library")) return SOURCE_COLOR.miss_library;
  if (label.includes("søk"))     return SOURCE_COLOR.jing;
  return "#555";
}

// ── SSH Nodes ─────────────────────────────────────────────────────────────────

const BOX = {
  background: "#141420",
  border: "1px solid #2a2a4a",
  borderRadius: 10,
  padding: "16px 20px",
  marginBottom: 28,
} as const;

const INPUT_STYLE = {
  width: "100%", padding: "6px 10px", borderRadius: 6,
  border: "1px solid #333", background: "#111", color: "#fff",
  fontSize: 13, boxSizing: "border-box" as const,
};

const SECTION_HEADER = {
  color: "#aaa", fontSize: 13, textTransform: "uppercase" as const,
  letterSpacing: 1, marginBottom: 14,
};

const EMPTY_NODE: SshNodeConfig = {
  label: "", host: "", user: "", port: 22,
  ssh_key: "~/.ssh/id_ed25519",
  node_type: "linux", sudo_enabled: false, sudo_commands: [],
};

function SshNodes() {
  const { t } = useTranslation();
  const [data, setData]           = useState<SshNodesData | null>(null);
  const [saving, setSaving]       = useState(false);
  const [saveMsg, setSaveMsg]     = useState("");
  const [modal, setModal]         = useState<{ id: string; node: SshNodeConfig } | null>(null);
  const [isNew, setIsNew]         = useState(false);
  const [newCmd, setNewCmd]       = useState("");
  const [testResult, setTestResult] = useState<{ ok: boolean; latency_ms: number; error?: string } | null>(null);
  const [testing, setTesting]     = useState(false);
  const [haHost, setHaHost]       = useState<string>("");

  useEffect(() => {
    apiGetSshNodes().then(setData).catch(() => {});
    // fetch HA host for pre-population
    fetch(`http://${window.location.hostname}:8000/api/settings/services`)
      .then(r => r.json())
      .then(d => {
        const url: string = d?.home_assistant?.url ?? "";
        if (url) {
          try { setHaHost(new URL(url).hostname); } catch { setHaHost(url); }
        }
      }).catch(() => {});
  }, []);

  async function saveAll(updated: SshNodesData) {
    setSaving(true); setSaveMsg("");
    try {
      await apiPutSshNodes(updated);
      setData(updated);
      setSaveMsg(t("tools.ssh_saved"));
    } catch { setSaveMsg("✗"); }
    finally { setSaving(false); }
    setTimeout(() => setSaveMsg(""), 2500);
  }

  function toggleLocalSudo() {
    if (!data) return;
    saveAll({ ...data, local: { sudo_enabled: !data.local.sudo_enabled } });
  }

  function openAdd() {
    setIsNew(true);
    setModal({ id: "", node: { ...EMPTY_NODE } });
    setTestResult(null);
    setNewCmd("");
  }

  function openEdit(id: string) {
    if (!data) return;
    setIsNew(false);
    setModal({ id, node: { ...data.nodes[id] } });
    setTestResult(null);
    setNewCmd("");
  }

  function deleteNode(id: string) {
    if (!data) return;
    if (!window.confirm(t("tools.ssh_confirm_delete", { id }))) return;
    const nodes = { ...data.nodes };
    delete nodes[id];
    saveAll({ ...data, nodes });
  }

  function saveModal() {
    if (!data || !modal) return;
    const nodes = { ...data.nodes, [modal.id]: modal.node };
    saveAll({ ...data, nodes });
    setModal(null);
  }

  function setField<K extends keyof SshNodeConfig>(k: K, v: SshNodeConfig[K]) {
    if (!modal) return;
    setModal({ ...modal, node: { ...modal.node, [k]: v } });
  }

  function setNodeType(t: "linux" | "ha_os") {
    if (!modal) return;
    const node = { ...modal.node, node_type: t };
    if (t === "ha_os") {
      node.port = 2222;
      node.user = "root";
      node.sudo_enabled = false;
      if (haHost && !node.host) node.host = haHost;
    } else {
      node.port = 22;
      if (node.user === "root") node.user = "";
    }
    setModal({ ...modal, node });
    setTestResult(null);
  }

  async function runTest() {
    if (!modal) return;
    setTesting(true); setTestResult(null);
    try {
      const r = await apiTestSshNode({
        host: modal.node.host,
        user: modal.node.user,
        port: modal.node.port,
        ssh_key: modal.node.ssh_key,
      });
      setTestResult(r);
    } catch { setTestResult({ ok: false, latency_ms: 0, error: "Request failed" }); }
    finally { setTesting(false); }
  }

  function addSudoCmd() {
    const cmd = newCmd.trim();
    if (!cmd || !modal) return;
    setField("sudo_commands", [...modal.node.sudo_commands, cmd]);
    setNewCmd("");
  }

  function removeSudoCmd(i: number) {
    if (!modal) return;
    setField("sudo_commands", modal.node.sudo_commands.filter((_, idx) => idx !== i));
  }

  if (!data) return null;

  const nodes = data.nodes;
  const isHaOs = modal?.node.node_type === "ha_os";

  return (
    <section style={BOX}>
      <h3 style={SECTION_HEADER}>{t("tools.ssh_title")}</h3>

      {/* Local sudo toggle */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10, marginBottom: 16,
        padding: "8px 12px", background: "#1a1a2e", borderRadius: 7,
        border: "1px solid #2a2a4a",
      }}>
        <label style={{ color: "#aaa", fontSize: 13, flex: 1 }}>
          {t("tools.ssh_local_sudo")}
          {data.local.sudo_enabled && (
            <span style={{ color: "#e07070", fontSize: 11, marginLeft: 8 }}>
              ⚠ {t("tools.ssh_local_sudo_warning")}
            </span>
          )}
        </label>
        <button onClick={toggleLocalSudo} style={{
          padding: "3px 14px", borderRadius: 5, border: "none", cursor: "pointer",
          fontSize: 12, fontWeight: 700,
          background: data.local.sudo_enabled ? "#4caf50" : "#333",
          color: data.local.sudo_enabled ? "#000" : "#aaa",
        }}>
          {data.local.sudo_enabled ? "ON" : "OFF"}
        </button>
      </div>

      {/* Node cards */}
      {Object.keys(nodes).length === 0 ? (
        <div style={{ color: "#555", fontSize: 13, marginBottom: 12 }}>{t("tools.ssh_no_nodes")}</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
          {Object.entries(nodes).map(([id, node]) => (
            <div key={id} style={{
              display: "flex", alignItems: "center", gap: 12,
              background: "#1a1a2e", border: "1px solid #2a2a4a",
              borderRadius: 8, padding: "10px 14px",
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <span style={{ color: "#fff", fontWeight: 600, fontSize: 13 }}>{id}</span>
                  {node.label && <span style={{ color: "#888", fontSize: 12 }}>{node.label}</span>}
                  {node.node_type === "ha_os" && (
                    <span style={{
                      background: "#1e3a5f", color: "#60a5fa",
                      borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 700,
                    }}>HA OS</span>
                  )}
                  {node.sudo_enabled && (
                    <span style={{
                      background: "#2a3a2a", color: "#4caf50",
                      borderRadius: 4, padding: "1px 6px", fontSize: 10,
                    }}>
                      sudo · {node.sudo_commands.length}
                    </span>
                  )}
                </div>
                <div style={{ color: "#555", fontSize: 11, marginTop: 2 }}>
                  {node.user}@{node.host}:{node.port}
                </div>
              </div>
              <button onClick={() => openEdit(id)} style={{
                padding: "4px 10px", borderRadius: 5, border: "1px solid #444",
                background: "transparent", color: "#aaa", fontSize: 12, cursor: "pointer",
              }}>{t("tools.ssh_edit")}</button>
              <button onClick={() => deleteNode(id)} style={{
                padding: "4px 10px", borderRadius: 5, border: "1px solid #5a2a2a",
                background: "transparent", color: "#e07070", fontSize: 12, cursor: "pointer",
              }}>{t("tools.ssh_delete")}</button>
            </div>
          ))}
        </div>
      )}

      <button onClick={openAdd} style={{
        padding: "6px 16px", borderRadius: 6, border: "1px solid #444",
        background: "transparent", color: "#aaa", fontSize: 13, cursor: "pointer",
      }}>
        + {t("tools.ssh_add")}
      </button>

      {saveMsg && <span style={{ color: "#4caf50", fontSize: 13, marginLeft: 12 }}>{saveMsg}</span>}
      {saving && <span style={{ color: "#888", fontSize: 13, marginLeft: 12 }}>…</span>}

      {/* Modal */}
      {modal && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,.7)",
          display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
        }}>
          <div style={{
            background: "#181828", border: "1px solid #333", borderRadius: 12,
            padding: 24, width: 480, maxWidth: "95vw", maxHeight: "90vh",
            overflowY: "auto", color: "#ddd", fontFamily: "monospace",
          }}>
            <h3 style={{ color: "#fff", marginBottom: 18, fontSize: 15 }}>
              {isNew ? `+ ${t("tools.ssh_add")}` : t("tools.ssh_edit")}
            </h3>

            {/* Node type selector */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ color: "#888", fontSize: 11, display: "block", marginBottom: 6 }}>
                {t("tools.ssh_node_type")}
              </label>
              <div style={{ display: "flex", gap: 8 }}>
                {(["linux", "ha_os"] as const).map(nt => (
                  <button key={nt} onClick={() => setNodeType(nt)} style={{
                    flex: 1, padding: "7px 0", borderRadius: 6, cursor: "pointer",
                    border: `1px solid ${modal.node.node_type === nt ? "#60a5fa" : "#333"}`,
                    background: modal.node.node_type === nt ? "#1e3a5f" : "#111",
                    color: modal.node.node_type === nt ? "#60a5fa" : "#888",
                    fontSize: 12, fontWeight: modal.node.node_type === nt ? 700 : 400,
                  }}>
                    {nt === "linux" ? "🖥 " : "🏠 "}
                    {t(nt === "linux" ? "tools.ssh_node_type_linux" : "tools.ssh_node_type_ha_os")}
                  </button>
                ))}
              </div>
              {isHaOs && (
                <div style={{ color: "#60a5fa", fontSize: 11, marginTop: 6 }}>
                  ℹ {t("tools.ssh_ha_os_hint")}
                </div>
              )}
            </div>

            {/* Node ID */}
            <div style={{ marginBottom: 12 }}>
              <label style={{ color: "#888", fontSize: 11, display: "block", marginBottom: 4 }}>
                {t("tools.ssh_node_id")}
              </label>
              <input
                value={modal.id}
                onChange={e => setModal({ ...modal, id: e.target.value.replace(/\s/g, "_") })}
                disabled={!isNew}
                placeholder="mynuc"
                style={{ ...INPUT_STYLE, opacity: isNew ? 1 : 0.5 }}
              />
            </div>

            {/* Label */}
            <div style={{ marginBottom: 12 }}>
              <label style={{ color: "#888", fontSize: 11, display: "block", marginBottom: 4 }}>
                {t("tools.ssh_label")}
              </label>
              <input
                value={modal.node.label ?? ""}
                onChange={e => setField("label", e.target.value)}
                placeholder="Intel NUC"
                style={INPUT_STYLE}
              />
            </div>

            {/* Host */}
            <div style={{ marginBottom: 12 }}>
              <label style={{ color: "#888", fontSize: 11, display: "block", marginBottom: 4 }}>
                {t("tools.ssh_host")}
              </label>
              <input
                value={modal.node.host}
                onChange={e => setField("host", e.target.value)}
                placeholder="192.168.0.233"
                style={INPUT_STYLE}
              />
              {isHaOs && haHost && modal.node.host === "" && (
                <div style={{ color: "#888", fontSize: 11, marginTop: 4 }}>
                  💡 {t("tools.ssh_ha_host_hint", { host: haHost })}
                  <button onClick={() => setField("host", haHost)} style={{
                    marginLeft: 8, padding: "1px 8px", borderRadius: 4, border: "none",
                    background: "#1e3a5f", color: "#60a5fa", fontSize: 11, cursor: "pointer",
                  }}>Bruk</button>
                </div>
              )}
            </div>

            {/* User + Port row */}
            <div style={{ display: "flex", gap: 10, marginBottom: 12 }}>
              <div style={{ flex: 2 }}>
                <label style={{ color: "#888", fontSize: 11, display: "block", marginBottom: 4 }}>
                  {t("tools.ssh_user")}
                </label>
                <input
                  value={modal.node.user}
                  onChange={e => setField("user", e.target.value)}
                  placeholder={isHaOs ? "root" : "user"}
                  disabled={isHaOs}
                  style={{ ...INPUT_STYLE, opacity: isHaOs ? 0.5 : 1 }}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ color: "#888", fontSize: 11, display: "block", marginBottom: 4 }}>
                  {t("tools.ssh_port")}
                </label>
                <input
                  type="number" min={1} max={65535}
                  value={modal.node.port}
                  onChange={e => setField("port", parseInt(e.target.value) || 22)}
                  disabled={isHaOs}
                  style={{ ...INPUT_STYLE, opacity: isHaOs ? 0.5 : 1 }}
                />
              </div>
            </div>

            {/* SSH key */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ color: "#888", fontSize: 11, display: "block", marginBottom: 4 }}>
                {t("tools.ssh_key")}
              </label>
              <input
                value={modal.node.ssh_key}
                onChange={e => setField("ssh_key", e.target.value)}
                placeholder="~/.ssh/id_ed25519"
                style={INPUT_STYLE}
              />
            </div>

            {/* Sudo section (Linux only) */}
            {!isHaOs && (
              <div style={{
                background: "#111", border: "1px solid #2a2a4a",
                borderRadius: 8, padding: "12px 14px", marginBottom: 16,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                  <span style={{ color: "#aaa", fontSize: 13, flex: 1 }}>{t("tools.ssh_sudo_enabled")}</span>
                  <button onClick={() => setField("sudo_enabled", !modal.node.sudo_enabled)} style={{
                    padding: "3px 12px", borderRadius: 5, border: "none", cursor: "pointer",
                    fontSize: 12, fontWeight: 700,
                    background: modal.node.sudo_enabled ? "#4caf50" : "#333",
                    color: modal.node.sudo_enabled ? "#000" : "#aaa",
                  }}>
                    {modal.node.sudo_enabled ? "ON" : "OFF"}
                  </button>
                </div>
                {modal.node.sudo_enabled && (
                  <>
                    <div style={{ color: "#888", fontSize: 11, marginBottom: 8 }}>
                      {t("tools.ssh_sudo_commands")}
                    </div>
                    {modal.node.sudo_commands.map((cmd, i) => (
                      <div key={i} style={{
                        display: "flex", alignItems: "center", gap: 8, marginBottom: 4,
                      }}>
                        <span style={{
                          flex: 1, color: "#ccc", fontSize: 12, background: "#1a1a2e",
                          borderRadius: 4, padding: "3px 8px", fontFamily: "monospace",
                        }}>{cmd}</span>
                        <button onClick={() => removeSudoCmd(i)} style={{
                          background: "none", border: "none", color: "#e07070",
                          cursor: "pointer", fontSize: 14, lineHeight: 1,
                        }}>×</button>
                      </div>
                    ))}
                    <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                      <input
                        value={newCmd}
                        onChange={e => setNewCmd(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && addSudoCmd()}
                        placeholder={t("tools.ssh_sudo_add_cmd")}
                        style={{ ...INPUT_STYLE, flex: 1 }}
                      />
                      <button onClick={addSudoCmd} style={{
                        padding: "6px 12px", borderRadius: 6, border: "none",
                        background: "#2a3a5a", color: "#60a5fa",
                        fontSize: 12, cursor: "pointer",
                      }}>{t("tools.ssh_sudo_add")}</button>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* Test connection */}
            <div style={{ marginBottom: 18 }}>
              <button onClick={runTest} disabled={testing || !modal.node.host} style={{
                padding: "6px 16px", borderRadius: 6, border: "1px solid #444",
                background: "transparent", color: "#aaa", fontSize: 13,
                cursor: testing || !modal.node.host ? "not-allowed" : "pointer",
                opacity: testing || !modal.node.host ? 0.5 : 1,
              }}>
                {testing ? "…" : `🔌 ${t("tools.ssh_test")}`}
              </button>
              {testResult && (
                <span style={{
                  marginLeft: 12, fontSize: 13,
                  color: testResult.ok ? "#4caf50" : "#e07070",
                }}>
                  {testResult.ok
                    ? t("tools.ssh_test_ok", { ms: testResult.latency_ms })
                    : t("tools.ssh_test_fail", { error: testResult.error })}
                </span>
              )}
            </div>

            {/* Actions */}
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button onClick={() => setModal(null)} style={{
                padding: "7px 18px", borderRadius: 6, border: "1px solid #444",
                background: "transparent", color: "#aaa", fontSize: 13, cursor: "pointer",
              }}>Avbryt</button>
              <button
                onClick={saveModal}
                disabled={!modal.id || !modal.node.host}
                style={{
                  padding: "7px 18px", borderRadius: 6, border: "none",
                  background: !modal.id || !modal.node.host ? "#222" : "#646cff",
                  color: !modal.id || !modal.node.host ? "#555" : "#fff",
                  fontSize: 13, fontWeight: 600, cursor: "pointer",
                }}>
                Lagre
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function Tools() {
  const { t } = useTranslation();
  const [timers, setTimers] = useState<Timer[]>([]);
  const [timerCounts, setTimerCounts] = useState<Record<string, number>>({});
  const [calls, setCalls] = useState<ToolCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [, setTick] = useState(0);

  const [maxPerUser, setMaxPerUser] = useState<number>(20);
  const [maxInput, setMaxInput] = useState<string>("20");
  const [savingMax, setSavingMax] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  async function fetchData() {
    try {
      const r = await fetch(`http://${window.location.hostname}:8000/api/tools/recent?n=80`);
      const data = await r.json();
      setTimers(data.timers ?? []);
      setTimerCounts(data.timer_counts ?? {});
      setCalls(data.calls ?? []);
    } catch {
      // network error — retry next round
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    apiGetTimerSettings().then(s => {
      setMaxPerUser(s.max_per_user);
      setMaxInput(String(s.max_per_user));
    }).catch(() => {});
  }, []);

  async function saveMax() {
    const val = parseInt(maxInput, 10);
    if (isNaN(val) || val < 1) return;
    setSavingMax(true);
    setSaveMsg("");
    try {
      await apiPutTimerSettings(val);
      setMaxPerUser(val);
      setSaveMsg(t("tools.timer_admin_saved"));
    } catch {
      setSaveMsg("✗");
    } finally {
      setSavingMax(false);
    }
  }

  useEffect(() => {
    fetchData();
    const id = setInterval(() => {
      fetchData();
      setTick(tk => tk + 1);
    }, 5000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const id = setInterval(() => setTick(tk => tk + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const now = Date.now() / 1000;
  const firingSoonLabel = t("tools.firing_soon");
  const unknownLabel = t("tools.unknown_event");

  return (
    <div style={{ color: "#ddd", fontFamily: "monospace" }}>
      <h2 style={{ color: "#fff", fontWeight: 700, fontSize: 22, marginBottom: 24 }}>
        {t("tools.title")}
      </h2>

      {/* SSH Nodes — top */}
      <SshNodes />

      {/* Timer admin */}
      <section style={BOX}>
        <h3 style={SECTION_HEADER}>{t("tools.timer_admin_title")}</h3>

        {Object.keys(timerCounts).length === 0 ? (
          <div style={{ color: "#555", fontSize: 12, marginBottom: 14 }}>{t("tools.timer_admin_no_active")}</div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
            {Object.entries(timerCounts).map(([uid, count]) => (
              <div key={uid} style={{
                background: "#1a1a2e", border: "1px solid #2a2a4a",
                borderRadius: 6, padding: "4px 12px",
                display: "flex", alignItems: "center", gap: 8,
              }}>
                <span style={{ color: "#888", fontSize: 12 }}>{uid}</span>
                <span style={{
                  background: SOURCE_COLOR.timer, color: "#000",
                  borderRadius: 4, padding: "1px 7px",
                  fontSize: 11, fontWeight: 700,
                }}>{count}</span>
                <span style={{ color: "#555", fontSize: 11 }}>/ {maxPerUser}</span>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <label style={{ color: "#aaa", fontSize: 13, flexShrink: 0 }}>
            {t("tools.timer_admin_max_label")}
          </label>
          <input
            type="number" min={1} max={1000}
            value={maxInput}
            onChange={e => { setMaxInput(e.target.value); setSaveMsg(""); }}
            style={{
              width: 70, padding: "5px 8px", borderRadius: 6,
              border: "1px solid #333", background: "#111", color: "#fff",
              fontSize: 13, textAlign: "center",
            }}
          />
          <button
            onClick={saveMax}
            disabled={savingMax || parseInt(maxInput, 10) === maxPerUser}
            style={{
              padding: "5px 14px", borderRadius: 6, border: "none",
              background: savingMax || parseInt(maxInput, 10) === maxPerUser ? "#222" : "#646cff",
              color: savingMax || parseInt(maxInput, 10) === maxPerUser ? "#555" : "#fff",
              fontSize: 13, fontWeight: 600, cursor: "pointer",
            }}
          >
            {savingMax ? "…" : t("tools.timer_admin_save")}
          </button>
          {saveMsg && <span style={{ color: "#4caf50", fontSize: 13 }}>{saveMsg}</span>}
        </div>
      </section>

      {/* Active timers */}
      <section style={{ marginBottom: 32 }}>
        <h3 style={SECTION_HEADER}>{t("tools.timers_title")}</h3>
        {timers.length === 0 ? (
          <div style={{ color: "#555", fontSize: 13 }}>{t("tools.timers_empty")}</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {timers.map(timer => {
              const firesAt = new Date(timer.fires_at).getTime() / 1000;
              const remaining = Math.max(0, Math.round(firesAt - now));
              const isRepeat = !!timer.repeat;
              const locale = i18n.language === "nb" ? "nb-NO" : i18n.language;
              return (
                <div key={timer.id} style={{
                  background: isRepeat ? "#1a2e1a" : "#1a1a2e",
                  border: `1px solid ${isRepeat ? "#2a4a2a" : "#2a2a4a"}`,
                  borderRadius: 8, padding: "12px 16px",
                  display: "flex", alignItems: "flex-start", gap: 12,
                }}>
                  <div style={{
                    background: isRepeat ? "#4a7a4a" : SOURCE_COLOR.timer,
                    color: "#000", borderRadius: 4, padding: "2px 8px",
                    fontSize: 11, fontWeight: 700, flexShrink: 0, marginTop: 2,
                  }}>
                    {formatRemaining(remaining, firingSoonLabel)}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 12, color: "#888", marginBottom: 4, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                      <span>[{timer.id}]</span>
                      <span>{t("tools.timer_fires")} {new Date(timer.fires_at).toLocaleString(locale, { dateStyle: "short", timeStyle: "short" })}</span>
                      {isRepeat && (
                        <span style={{
                          background: "#2a4a2a", color: "#7fc97f",
                          borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 700,
                        }}>
                          ↻ {t(`tools.repeat.${timer.repeat!}`, timer.repeat!)}
                        </span>
                      )}
                      {timer.notify && <span style={{ color: SOURCE_COLOR.timer }}>{t("tools.timer_notify")}</span>}
                    </div>
                    <div style={{ fontSize: 13, color: "#ccc", lineHeight: 1.4 }}>
                      «{timer.prompt}»
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Tool run log — scrollable box */}
      <section style={BOX}>
        <h3 style={SECTION_HEADER}>{t("tools.log_title")}</h3>
        {loading ? (
          <div style={{ color: "#555", fontSize: 13 }}>{t("tools.log_loading")}</div>
        ) : calls.length === 0 ? (
          <div style={{ color: "#555", fontSize: 13 }}>{t("tools.log_empty")}</div>
        ) : (
          <div style={{ maxHeight: 400, overflowY: "auto", paddingRight: 4 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {calls.map((c, i) => (
                <div key={i} style={{
                  display: "grid",
                  gridTemplateColumns: "60px 60px 140px 1fr auto",
                  gap: 10, alignItems: "start",
                  padding: "7px 10px", borderRadius: 6,
                  background: i % 2 === 0 ? "#111" : "#141414",
                  fontSize: 12,
                }}>
                  <div style={{ color: "#555" }}>{formatTime(c.ts)}</div>
                  <div style={{ color: sourceColor(c.source), fontWeight: 600 }}>{c.source}</div>
                  <div style={{
                    color: eventColor(c),
                    background: "#1a1a1a",
                    borderRadius: 4, padding: "1px 6px",
                    border: `1px solid ${eventColor(c)}44`,
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                  }}>
                    {eventLabel(c, unknownLabel)}
                  </div>
                  <div style={{ color: "#999", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {c.error
                      ? <span style={{ color: "#e07070" }}>{c.error}</span>
                      : c.result_preview ?? c.prompt_preview ?? ""}
                  </div>
                  <div style={{ color: "#444", textAlign: "right", whiteSpace: "nowrap" }}>
                    {c.duration_ms != null && c.duration_ms > 0 ? `${c.duration_ms}ms` : ""}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
