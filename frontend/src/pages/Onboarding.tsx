import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, Circle, Loader2, ChevronRight, ChevronLeft, Rocket } from "lucide-react";
import axios from "axios";
import {
  apiGetCapabilities, apiPutCapabilities,
  apiGetServices, apiPutFrigate, apiPutHa, apiPutMqtt, apiPutPlex,
  type CapabilitiesConfig,
} from "@/services/api";

const BASE = `http://${window.location.hostname}:8000`;
const authHeader = () => ({ Authorization: `Bearer ${sessionStorage.getItem("kaare_token")}` });

// ── Step 1: Profil ────────────────────────────────────────────────────────────

type Location = { city: string; postal_code: string; country: string; lat: number; lon: number; timezone: string };

function StepProfil({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [lok, setLok] = useState<Location>({ city: "", postal_code: "", country: "", lat: 0, lon: 0, timezone: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    axios.get(`${BASE}/api/settings/location`, { headers: authHeader() })
      .then(r => { if (r.data.location) setLok(r.data.location); })
      .catch(() => {});
  }, []);

  const set = (k: keyof Location, v: string) =>
    setLok(p => ({ ...p, [k]: ["lat", "lon"].includes(k) ? Number(v) : v }));

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await axios.put(`${BASE}/api/settings/location`, lok, { headers: authHeader() });
      onDone();
    } catch { setError(t("onboarding.profil.error_save")); }
    finally { setSaving(false); }
  };

  const fields: [keyof Location, string, string, string][] = [
    ["city",        t("settings.generelt.location.fields.city.label"),        "text",   t("settings.generelt.location.fields.city.hint")],
    ["postal_code", t("settings.generelt.location.fields.postal_code.label"), "text",   t("settings.generelt.location.fields.postal_code.hint")],
    ["country",     t("settings.generelt.location.fields.country.label"),     "text",   t("settings.generelt.location.fields.country.hint")],
    ["lat",         t("settings.generelt.location.fields.lat.label"),         "number", t("settings.generelt.location.fields.lat.hint")],
    ["lon",         t("settings.generelt.location.fields.lon.label"),         "number", t("settings.generelt.location.fields.lon.hint")],
    ["timezone",    t("settings.generelt.location.fields.timezone.label"),    "text",   t("settings.generelt.location.fields.timezone.hint")],
  ];

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">{t("settings.generelt.location.title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">{t("settings.generelt.location.description")}</p>
      </div>
      <div className="space-y-3">
        {fields.map(([key, label, type, hint]) => (
          <div key={key}>
            <Label className="text-xs text-muted-foreground mb-1 block">{label}</Label>
            <Input
              type={type}
              value={String(lok[key] ?? "")}
              onChange={e => set(key, e.target.value)}
              step={type === "number" ? "any" : undefined}
              placeholder={hint}
            />
          </div>
        ))}
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button onClick={save} disabled={saving} className="w-full">
        {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
        {t("onboarding.profil.save_continue")} <ChevronRight className="ml-2 h-4 w-4" />
      </Button>
    </div>
  );
}

// ── Step 2: Bruker ────────────────────────────────────────────────────────────

function StepBruker({ onDone, onSkip }: { onDone: () => void; onSkip: () => void }) {
  const { t } = useTranslation();
  const [displayName, setDisplayName] = useState("");
  const [username, setUsername]       = useState("");
  const [pin, setPin]                 = useState("");
  const [role, setRole]               = useState("adult");
  const [saving, setSaving] = useState(false);
  const [error, setError]   = useState("");

  const create = async () => {
    setSaving(true);
    setError("");
    try {
      await axios.post(`${BASE}/api/admin/users`, { display_name: displayName, username, pin, role }, { headers: authHeader() });
      onDone();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? t("onboarding.bruker.error_create");
      setError(msg);
    }
    finally { setSaving(false); }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">{t("onboarding.bruker.title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">{t("onboarding.bruker.description")}</p>
      </div>
      <div className="space-y-3">
        <div>
          <Label className="text-xs text-muted-foreground mb-1 block">{t("onboarding.bruker.display_name")}</Label>
          <Input value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder={t("onboarding.bruker.display_name_ph")} />
        </div>
        <div>
          <Label className="text-xs text-muted-foreground mb-1 block">{t("onboarding.bruker.username")}</Label>
          <Input value={username} onChange={e => setUsername(e.target.value)} placeholder={t("onboarding.bruker.username_ph")} />
        </div>
        <div>
          <Label className="text-xs text-muted-foreground mb-1 block">{t("onboarding.bruker.pin")}</Label>
          <Input type="password" value={pin} onChange={e => setPin(e.target.value)} placeholder="••••••" maxLength={8} />
        </div>
        <div>
          <Label className="text-xs text-muted-foreground mb-1 block">{t("onboarding.bruker.role")}</Label>
          <select value={role} onChange={e => setRole(e.target.value)} className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm">
            <option value="adult">{t("onboarding.bruker.roles.adult")}</option>
            <option value="young_adult">{t("onboarding.bruker.roles.young_adult")}</option>
            <option value="teen">{t("onboarding.bruker.roles.teen")}</option>
            <option value="child">{t("onboarding.bruker.roles.child")}</option>
          </select>
        </div>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="flex gap-2">
        <Button onClick={create} disabled={saving || !displayName || !username || pin.length < 6} className="flex-1">
          {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          {t("onboarding.bruker.create")} <ChevronRight className="ml-2 h-4 w-4" />
        </Button>
        <Button variant="outline" onClick={onSkip}>{t("onboarding.bruker.skip")}</Button>
      </div>
    </div>
  );
}

// ── Step 3: Distribusjon ──────────────────────────────────────────────────────

const PRESETS = [
  { id: "full",    emoji: "🖥" },
  { id: "medium",  emoji: "⚡" },
  { id: "letvekt", emoji: "🪶" },
];

function StepDistribusjon({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [caps, setCaps]   = useState<CapabilitiesConfig | null>(null);
  const [profile, setProfile] = useState("");
  const [saving, setSaving]   = useState(false);
  const [error, setError]     = useState("");

  useEffect(() => {
    apiGetCapabilities().then(d => { setCaps(d); setProfile(d.distribution_profile ?? ""); }).catch(() => {});
  }, []);

  const applyPreset = (id: string) => {
    setProfile(id);
    setCaps(prev => prev ? { ...prev, distribution_profile: id } : prev);
  };

  const save = async () => {
    if (!caps) return;
    setSaving(true);
    setError("");
    try {
      await apiPutCapabilities({ domains: caps.domains, distribution_profile: profile });
      onDone();
    } catch { setError(t("onboarding.distribusjon.error_save")); }
    finally { setSaving(false); }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">{t("settings.distribusjon.profile.title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">{t("settings.distribusjon.profile.description")}</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {PRESETS.map(p => (
          <button
            key={p.id}
            onClick={() => applyPreset(p.id)}
            className={`text-left p-4 rounded-lg border transition-colors ${profile === p.id ? "border-primary bg-primary/10" : "border-border hover:border-muted-foreground"}`}
          >
            <div className="text-2xl mb-1">{p.emoji}</div>
            <div className="font-semibold text-sm">{t(`settings.distribusjon.presets.${p.id}.label`)}</div>
            <div className="text-xs text-muted-foreground mt-1">{t(`settings.distribusjon.presets.${p.id}.description`)}</div>
          </button>
        ))}
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button onClick={save} disabled={saving || !profile} className="w-full">
        {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
        {t("onboarding.distribusjon.save_continue")} <ChevronRight className="ml-2 h-4 w-4" />
      </Button>
    </div>
  );
}

// ── Step 4: LLM-kilde ─────────────────────────────────────────────────────────

function StepLLM({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [choice, setChoice] = useState<"builtin" | "external">("builtin");
  const [url, setUrl] = useState("http://localhost:11434");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    axios.get(`${BASE}/api/settings/ollama_source`, { headers: authHeader() })
      .then(r => {
        if (!r.data.builtin && r.data.url) {
          setChoice("external");
          setUrl(r.data.url);
        }
      }).catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const targetUrl = choice === "builtin" ? "http://ollama:11434" : url.trim();
      await axios.put(`${BASE}/api/settings/ollama_source`, { url: targetUrl }, { headers: authHeader() });
      onDone();
    } catch { setError(t("onboarding.llm.error_save")); }
    finally { setSaving(false); }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">{t("onboarding.llm.title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">{t("onboarding.llm.description")}</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <button
          onClick={() => setChoice("builtin")}
          className={`text-left p-4 rounded-lg border transition-colors ${choice === "builtin" ? "border-primary bg-primary/10" : "border-border hover:border-muted-foreground"}`}
        >
          <div className="text-2xl mb-2">📦</div>
          <div className="font-semibold text-sm">{t("onboarding.llm.builtin_label")}</div>
          <div className="text-xs text-muted-foreground mt-1">{t("onboarding.llm.builtin_hint")}</div>
          <div className="mt-3 text-xs text-amber-400/80 flex items-start gap-1">
            <span className="shrink-0 mt-0.5">⚠</span>
            <span>{t("onboarding.llm.builtin_warning")}</span>
          </div>
        </button>

        <button
          onClick={() => setChoice("external")}
          className={`text-left p-4 rounded-lg border transition-colors ${choice === "external" ? "border-primary bg-primary/10" : "border-border hover:border-muted-foreground"}`}
        >
          <div className="text-2xl mb-2">🌐</div>
          <div className="font-semibold text-sm">{t("onboarding.llm.external_label")}</div>
          <div className="text-xs text-muted-foreground mt-1">{t("onboarding.llm.external_hint")}</div>
        </button>
      </div>

      {choice === "builtin" && (
        <div className="bg-amber-950/20 border border-amber-800/30 rounded-lg p-4 text-sm text-amber-300/80 space-y-1">
          <p className="font-medium">{t("onboarding.llm.builtin_note_title")}</p>
          <p className="text-xs text-amber-400/60">{t("onboarding.llm.builtin_note_body")}</p>
        </div>
      )}

      {choice === "external" && (
        <div className="space-y-2">
          <Label className="text-xs text-muted-foreground">{t("onboarding.llm.external_url_label")}</Label>
          <Input
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="http://192.168.0.x:11434"
          />
          <p className="text-xs text-muted-foreground">{t("onboarding.llm.external_url_hint")}</p>
        </div>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex gap-2">
        <Button onClick={save} disabled={saving || (choice === "external" && !url.trim())} className="flex-1">
          {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          {t("onboarding.llm.save_continue")} <ChevronRight className="ml-2 h-4 w-4" />
        </Button>
        <Button variant="outline" onClick={onDone}>{t("onboarding.llm.skip")}</Button>
      </div>
    </div>
  );
}

// ── Step 5: Integrasjoner (valgfri) ──────────────────────────────────────────

type IntegrationState = {
  haUrl: string; mqttHost: string; frigateUrl: string; plexUrl: string;
};

function StepIntegrasjoner({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [state, setState] = useState<IntegrationState>({ haUrl: "", mqttHost: "", frigateUrl: "", plexUrl: "" });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiGetServices().then(d => {
      setState({
        haUrl: d.home_assistant?.url ?? "",
        mqttHost: d.mqtt?.host ?? "",
        frigateUrl: d.frigate?.url ?? "",
        plexUrl: d.plex?.url ?? "",
      });
    }).catch(() => {});
  }, []);

  const set = (k: keyof IntegrationState, v: string) => setState(p => ({ ...p, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      await Promise.allSettled([
        state.haUrl ? apiPutHa({ url: state.haUrl }) : Promise.resolve(),
        state.mqttHost ? apiPutMqtt({ host: state.mqttHost }) : Promise.resolve(),
        state.frigateUrl ? apiPutFrigate({ url: state.frigateUrl }) : Promise.resolve(),
        state.plexUrl ? apiPutPlex({ url: state.plexUrl }) : Promise.resolve(),
      ]);
      onDone();
    } finally { setSaving(false); }
  };

  const integrations = [
    { key: "haUrl" as keyof IntegrationState,      label: "Home Assistant URL",  placeholder: "http://192.168.0.100:8123" },
    { key: "mqttHost" as keyof IntegrationState,   label: "MQTT Broker host",    placeholder: "192.168.0.100" },
    { key: "frigateUrl" as keyof IntegrationState, label: "Frigate URL",         placeholder: "http://192.168.0.100:5000" },
    { key: "plexUrl" as keyof IntegrationState,    label: "Plex Server URL",     placeholder: "http://192.168.0.156:32400" },
  ];

  return (
    <div className="space-y-5">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <h2 className="text-lg font-semibold">{t("onboarding.integrasjoner.title")}</h2>
          <Badge variant="outline" className="text-muted-foreground text-xs">{t("onboarding.integrasjoner.optional_badge")}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          {t("onboarding.integrasjoner.description")}
        </p>
      </div>
      <div className="space-y-3">
        {integrations.map(({ key, label, placeholder }) => (
          <div key={key}>
            <Label className="text-xs text-muted-foreground mb-1 block">{label}</Label>
            <Input value={state[key]} onChange={e => set(key, e.target.value)} placeholder={placeholder} />
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <Button onClick={save} disabled={saving} className="flex-1">
          {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          {t("onboarding.integrasjoner.save_finish")} <ChevronRight className="ml-2 h-4 w-4" />
        </Button>
        <Button variant="outline" onClick={onDone}>{t("onboarding.integrasjoner.skip")}</Button>
      </div>
    </div>
  );
}

// ── Step 5: Ferdig ────────────────────────────────────────────────────────────

function StepFerdig({ onDashboard }: { onDashboard: () => void }) {
  const { t } = useTranslation();
  const hints = t("onboarding.ferdig.hints", { returnObjects: true }) as string[];

  return (
    <div className="space-y-5 text-center">
      <div className="flex justify-center">
        <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center">
          <Rocket className="h-8 w-8 text-primary" />
        </div>
      </div>
      <div>
        <h2 className="text-xl font-semibold">{t("onboarding.ferdig.title")}</h2>
        <p className="text-sm text-muted-foreground mt-2">
          {t("onboarding.ferdig.description")}
        </p>
      </div>
      <div className="text-left space-y-2 bg-muted/20 rounded-lg p-4">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">{t("onboarding.ferdig.next_steps_label")}</p>
        {hints.map(hint => (
          <div key={hint} className="flex gap-2 text-xs text-muted-foreground">
            <span className="text-primary mt-0.5">→</span>
            <span>{hint}</span>
          </div>
        ))}
      </div>
      <Button onClick={onDashboard} className="w-full" size="lg">
        {t("onboarding.ferdig.go_dashboard")}
      </Button>
    </div>
  );
}

// ── Wizard ────────────────────────────────────────────────────────────────────

const STEP_KEYS = ["profil", "bruker", "distribusjon", "llm", "integrasjoner", "ferdig"] as const;

export default function Onboarding() {
  const { t } = useTranslation();
  const [step, setStep] = useState(0);
  const navigate = useNavigate();

  const next = () => setStep(s => Math.min(s + 1, STEP_KEYS.length - 1));
  const back = () => setStep(s => Math.max(s - 1, 0));

  return (
    <div className="min-h-screen bg-[#13151f] flex items-center justify-center p-4">
      <div className="w-full max-w-xl">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="text-3xl font-bold text-white mb-1">Kåre</div>
          <p className="text-sm text-muted-foreground">{t("onboarding.step_label", { step: step + 1, total: STEP_KEYS.length })}</p>
        </div>

        {/* Progress */}
        <div className="flex items-center gap-2 mb-8">
          {STEP_KEYS.map((key, i) => (
            <div key={key} className="flex items-center gap-2 flex-1 min-w-0">
              <button
                onClick={() => i < step && setStep(i)}
                className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 transition-colors ${
                  i < step ? "bg-primary cursor-pointer" : i === step ? "bg-primary/30 border border-primary" : "bg-muted/30 border border-border"
                }`}
              >
                {i < step ? <CheckCircle2 className="h-4 w-4 text-primary-foreground" /> : <Circle className="h-3 w-3 text-muted-foreground" />}
              </button>
              <span className={`text-xs truncate ${i === step ? "text-foreground font-medium" : "text-muted-foreground"}`}>{t(`onboarding.steps.${key}`)}</span>
              {i < STEP_KEYS.length - 1 && <div className="h-px flex-1 bg-border" />}
            </div>
          ))}
        </div>

        {/* Content */}
        <div className="bg-[#1a1d2e] border border-border rounded-xl p-6">
          {step === 0 && <StepProfil onDone={next} />}
          {step === 1 && <StepBruker onDone={next} onSkip={next} />}
          {step === 2 && <StepDistribusjon onDone={next} />}
          {step === 3 && <StepLLM onDone={next} />}
          {step === 4 && <StepIntegrasjoner onDone={next} />}
          {step === 5 && <StepFerdig onDashboard={() => navigate("/admin")} />}
        </div>

        {/* Back button */}
        {step > 0 && step < STEP_KEYS.length - 1 && (
          <button onClick={back} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mt-4 mx-auto">
            <ChevronLeft className="h-3 w-3" /> {t("onboarding.back")}
          </button>
        )}
      </div>
    </div>
  );
}
