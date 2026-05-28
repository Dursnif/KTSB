import { useState } from "react";
import { CheckCircle2, XCircle, Loader2, Eye, EyeOff, Wifi, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiTestConnection } from "@/services/api";
import { useTranslation } from "react-i18next";

export type SaveState = "idle" | "saving" | "saved" | "error";

export function useSaveState() {
  const [state, setState] = useState<SaveState>("idle");
  const saved = () => { setState("saved"); setTimeout(() => setState("idle"), 3000); };
  const error = () => { setState("error"); setTimeout(() => setState("idle"), 4000); };
  return { state, saving: () => setState("saving"), saved, error };
}

export function SaveFeedback({ state }: { state: SaveState }) {
  const { t } = useTranslation();
  if (state === "saving") return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />;
  if (state === "saved")  return <span className="flex items-center gap-1 text-sm text-green-500"><CheckCircle2 className="h-4 w-4" /> {t("common.saved")}</span>;
  if (state === "error")  return <span className="flex items-center gap-1 text-sm text-destructive"><XCircle className="h-4 w-4" /> {t("common.error")}</span>;
  return null;
}

export function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[200px_1fr] items-start gap-4 py-3">
      <div>
        <Label className="text-sm font-medium">{label}</Label>
        {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
      </div>
      <div>{children}</div>
    </div>
  );
}

export function TestButton({ url, disabled }: { url: string; disabled?: boolean }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const test = async () => {
    if (!url) return;
    setStatus("testing");
    try {
      const r = await apiTestConnection(url);
      setStatus(r.ok ? "ok" : "fail");
    } catch {
      setStatus("fail");
    }
    setTimeout(() => setStatus("idle"), 5000);
  };
  return (
    <Button variant="outline" size="sm" onClick={test} disabled={disabled || !url || status === "testing"} className="gap-2">
      {status === "testing" ? <Loader2 className="h-3 w-3 animate-spin" /> :
       status === "ok"      ? <Wifi className="h-3 w-3 text-green-500" /> :
       status === "fail"    ? <WifiOff className="h-3 w-3 text-destructive" /> :
                              <Wifi className="h-3 w-3" />}
      {status === "testing" ? t("common.testing") : status === "ok" ? "OK" : status === "fail" ? t("common.error") : t("common.test")}
    </Button>
  );
}

export function MaskedInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative flex items-center">
      <Input
        type={show ? "text" : "password"}
        value={value}
        onChange={(e: React.ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        placeholder={placeholder ?? "••••••••"}
        className="pr-10 font-mono text-sm"
      />
      <button
        type="button"
        onClick={() => setShow(s => !s)}
        className="absolute right-3 text-muted-foreground hover:text-foreground"
      >
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

export function RadioOption({ value, current, onChange, label, description }: {
  value: string;
  current: string;
  onChange: (v: string) => void;
  label: string;
  description: string;
}) {
  const active = value === current;
  return (
    <button
      type="button"
      onClick={() => onChange(value)}
      className="flex items-start gap-3 w-full text-left py-2 group"
    >
      <span className={`mt-0.5 flex-shrink-0 w-4 h-4 rounded-full border-2 flex items-center justify-center transition-colors ${active ? "border-primary bg-primary" : "border-muted-foreground bg-transparent"}`}>
        {active && <span className="w-2 h-2 rounded-full bg-white" />}
      </span>
      <span>
        <span className="text-sm font-medium leading-tight block">{label}</span>
        <span className="text-xs text-muted-foreground">{description}</span>
      </span>
    </button>
  );
}
