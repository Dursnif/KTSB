import { useEffect, useState } from "react";
import axios from "axios";

const BASE = `http://${window.location.hostname}:8000`;

type AgentMessage = {
  id: number;
  ts: string;
  rid: string;
  user_id: string;
  from_agent: string;
  to_agent: string;
  query: string;
  response: string;
};

const AGENT_COLORS: Record<string, string> = {
  kare:         "#646cff",
  miss_kare:    "#c084fc",
  miss_library: "#5ba8a0",
  pettersmart:  "#82b366",
  jing:         "#a78bfa",
  jang:         "#f472b6",
};

const AGENT_LABELS: Record<string, string> = {
  kare:         "Kåre",
  miss_kare:    "Miss Kåre",
  miss_library: "Frøken Library",
  pettersmart:  "Pettersmart",
  jing:         "Jing",
  jang:         "Jang",
};

function AgentBadge({ name }: { name: string }) {
  return (
    <span style={{
      background: AGENT_COLORS[name] ?? "#444",
      color: "#fff", fontSize: 11, fontWeight: 700,
      padding: "2px 8px", borderRadius: 10,
      letterSpacing: 0.5,
    }}>
      {AGENT_LABELS[name] ?? name}
    </span>
  );
}

export default function AgentMessages() {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const token = sessionStorage.getItem("kaare_token");
      const { data } = await axios.get(`${BASE}/api/agent_messages?limit=100`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setMessages(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const fmt = (ts: string) => {
    try { return new Date(ts).toLocaleString("no-NO", { dateStyle: "short", timeStyle: "medium" }); }
    catch { return ts; }
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 28 }}>
        <div>
          <div style={{ color: "#fff", fontSize: 22, fontWeight: 700 }}>Intern kommunikasjon</div>
          <div style={{ color: "#555", fontSize: 13, marginTop: 4 }}>Meldinger mellom agenter</div>
        </div>
        <button onClick={load} style={{
          padding: "8px 16px", borderRadius: 8, border: "1px solid #333",
          background: "transparent", color: "#888", fontSize: 13, cursor: "pointer",
        }}>
          Oppdater
        </button>
      </div>

      {loading && <div style={{ color: "#555" }}>Laster…</div>}

      {!loading && messages.length === 0 && (
        <div style={{ color: "#555", fontSize: 14 }}>
          Ingen agent-kommunikasjon logget ennå. Skjer første gang Kåre spør en agent om noe.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {messages.map(m => (
          <div key={m.id} style={{
            background: "#111", border: "1px solid #1e1e1e",
            borderRadius: 12, padding: "16px 20px",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <AgentBadge name={m.from_agent} />
              <span style={{ color: "#444", fontSize: 12 }}>→</span>
              <AgentBadge name={m.to_agent} />
              <span style={{ color: "#444", fontSize: 12, marginLeft: "auto" }}>{fmt(m.ts)}</span>
              {m.user_id !== "global" && (
                <span style={{ color: "#555", fontSize: 11 }}>({m.user_id})</span>
              )}
            </div>

            <div style={{ marginBottom: 10 }}>
              <div style={{ color: "#666", fontSize: 11, marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.5 }}>Spørsmål</div>
              <div style={{ color: "#ccc", fontSize: 14 }}>{m.query}</div>
            </div>

            <div style={{ borderTop: "1px solid #1a1a1a", paddingTop: 10 }}>
              <div style={{ color: "#666", fontSize: 11, marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.5 }}>Svar</div>
              <div style={{ color: "#aaa", fontSize: 13, whiteSpace: "pre-wrap" }}>{m.response}</div>
            </div>

            {m.rid && (
              <div style={{ marginTop: 8, color: "#333", fontSize: 11 }}>rid: {m.rid}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
