import { useCallback, useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, XCircle, Loader2, RotateCcw, ChevronDown, ChevronUp, Download } from "lucide-react";
import { apiGetPiperModels, apiPostPiperDownload, apiGetPiperDownloadStatus, apiPutPiperActivate, apiRestartService, type PiperPreset } from "@/services/api";

const LANG_LABELS: Record<string, string> = { nb: "Norsk (nb)", en: "English (en)", de: "Deutsch (de)" };
const LANG_ORDER = ["nb", "en", "de"];

type DownloadState = { jobId: string; modelName: string; done: boolean; error?: string };

export function PiperCard({ onSaved }: { onSaved: () => void }) {
  const [collapsed, setCollapsed]     = useState(true);
  const [models, setModels]           = useState<Record<string, PiperPreset[]>>({});
  const [activating, setActivating]   = useState<string | null>(null);
  const [downloads, setDownloads]     = useState<Record<string, DownloadState>>({});
  const [restarting, setRestarting]   = useState(false);
  const [restartOk, setRestartOk]     = useState<boolean | null>(null);
  const pollRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const load = useCallback(() => {
    apiGetPiperModels().then(setModels).catch(() => {});
  }, []);

  useEffect(() => {
    if (!collapsed) load();
  }, [collapsed, load]);

  useEffect(() => {
    const refs = pollRefs.current;
    return () => { Object.values(refs).forEach(clearInterval); };
  }, []);

  const startDownload = async (lang: string, preset: PiperPreset) => {
    const key = `${lang}:${preset.model}`;
    try {
      const { job_id } = await apiPostPiperDownload(lang, preset.model);
      setDownloads(d => ({ ...d, [key]: { jobId: job_id, modelName: preset.model, done: false } }));
      const iv = setInterval(async () => {
        try {
          const s = await apiGetPiperDownloadStatus(job_id);
          if (s.status === "done") {
            clearInterval(iv);
            delete pollRefs.current[key];
            setDownloads(d => ({ ...d, [key]: { ...d[key], done: true } }));
            load();
          } else if (s.status === "error") {
            clearInterval(iv);
            delete pollRefs.current[key];
            setDownloads(d => ({ ...d, [key]: { ...d[key], done: true, error: s.error } }));
          }
        } catch { /* keep polling */ }
      }, 3000);
      pollRefs.current[key] = iv;
    } catch (e) {
      setDownloads(d => ({ ...d, [key]: { jobId: "", modelName: preset.model, done: true, error: String(e) } }));
    }
  };

  const activate = async (lang: string, preset: PiperPreset) => {
    setActivating(`${lang}:${preset.model}`);
    try {
      await apiPutPiperActivate(lang, preset.local_path);
      load();
      onSaved();
    } catch { /* ignore */ }
    finally { setActivating(null); }
  };

  const restart = async () => {
    setRestarting(true);
    setRestartOk(null);
    try {
      const r = await apiRestartService("voice");
      setRestartOk(r.ok);
    } catch { setRestartOk(false); }
    finally { setRestarting(false); }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <div
          className="flex items-center gap-3 cursor-pointer select-none flex-1"
          onClick={() => setCollapsed(v => !v)}
        >
          <CardTitle className="text-base">Piper (tekst-til-tale)</CardTitle>
          <Badge variant="outline" className="text-xs font-mono">port 8011</Badge>
        </div>
        <div className="flex items-center gap-2">
          {collapsed
            ? <ChevronDown className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(false)} />
            : <ChevronUp   className="h-4 w-4 text-muted-foreground cursor-pointer" onClick={() => setCollapsed(true)} />
          }
        </div>
      </CardHeader>

      {!collapsed && (
        <CardContent className="space-y-5 pt-0">
          <p className="text-xs text-muted-foreground">
            Én stemmemodell per språk. Velg modellen som er lastet ned for å aktivere den.
            Nedlasting krever internettilgang og <code>huggingface-cli</code>.
          </p>

          {LANG_ORDER.map(lang => {
            const presets = models[lang] ?? [];
            return (
              <div key={lang} className="space-y-1">
                <p className="text-sm font-medium text-foreground">{LANG_LABELS[lang]}</p>
                <div className="rounded-md border border-white/10 divide-y divide-white/5 text-sm">
                  {presets.map(p => {
                    const key = `${lang}:${p.model}`;
                    const dl = downloads[key];
                    const isDownloading = dl && !dl.done;
                    const dlError = dl?.done && dl.error;
                    const isActivating = activating === key;

                    return (
                      <div
                        key={p.model}
                        className={`flex items-center justify-between px-3 py-2 gap-3 ${p.active ? "bg-white/10" : ""}`}
                      >
                        {/* Model info */}
                        <div className="flex-1 min-w-0">
                          <span className="font-mono text-xs text-foreground block truncate">{p.model}</span>
                          <span className="text-xs text-muted-foreground">
                            {p.tier_label} · {p.size_mb} MB · {p.gender}
                          </span>
                          {dlError && (
                            <span className="text-xs text-red-400 block truncate" title={dl.error}>
                              Feil: {dl.error?.slice(0, 80)}
                            </span>
                          )}
                        </div>

                        {/* State + action */}
                        <div className="flex items-center gap-2 shrink-0">
                          {p.active && (
                            <Badge className="bg-green-600/20 text-green-400 border-green-600/30 text-xs">aktiv</Badge>
                          )}
                          {!p.active && p.downloaded && (
                            <Badge variant="outline" className="text-xs text-muted-foreground">lastet ned</Badge>
                          )}

                          {!p.downloaded && !isDownloading && (
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-6 px-2 text-xs gap-1"
                              onClick={() => startDownload(lang, p)}
                            >
                              <Download className="h-3 w-3" />
                              Last ned
                            </Button>
                          )}

                          {isDownloading && (
                            <span className="text-xs text-muted-foreground flex items-center gap-1">
                              <Loader2 className="h-3 w-3 animate-spin" />
                              Laster ned…
                            </span>
                          )}

                          {p.downloaded && !p.active && (
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-6 px-2 text-xs"
                              disabled={isActivating}
                              onClick={() => activate(lang, p)}
                            >
                              {isActivating && <Loader2 className="h-3 w-3 animate-spin mr-1" />}
                              Aktiver
                            </Button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}

          {/* Footer */}
          <div className="flex items-center gap-3 flex-wrap">
            <Button onClick={restart} disabled={restarting} size="sm" variant="outline">
              {restarting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
              Restart tjeneste
            </Button>
            {restartOk === true  && <span className="text-xs text-green-400 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" /> Restartet</span>}
            {restartOk === false && <span className="text-xs text-red-400 flex items-center gap-1"><XCircle className="h-3 w-3" /> Feil ved restart</span>}
          </div>
          <p className="text-xs text-muted-foreground">
            Aktivering av ny modell krever restart for at Kåre tar den i bruk.
          </p>
        </CardContent>
      )}
    </Card>
  );
}
