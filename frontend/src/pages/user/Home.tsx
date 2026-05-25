import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation, Trans } from "react-i18next";
import { apiGenerate, apiMissKareComment, apiChatHistory, apiTranscribe, apiTtsFile, apiGetServices } from "../../services/api";
import type { TraceStep } from "../../services/api";
import { useTheme } from "../../theme";
import { useUserPrefs } from "../../hooks/useUserPrefs";

type Message = { role: "user" | "kare"; text: string; trace?: TraceStep[]; images?: string[] };

const IMAGE_URL_RE = /\/api\/image\/([a-zA-Z0-9_-]+)/g;

function setMood(mood: string) {
  document.documentElement.dataset.mood = mood;
}
function getNightMood(): string | null {
  const h = new Date().getHours();
  return (h >= 23 || h < 7) ? "sleeping" : null;
}
const API_BASE = `http://${window.location.hostname}:8000`;

function renderKareText(text: string, onImageClick: (src: string) => void) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let match;
  IMAGE_URL_RE.lastIndex = 0;
  while ((match = IMAGE_URL_RE.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const fullSrc = `${API_BASE}${match[0]}`;
    parts.push(
      <img
        key={match.index}
        src={fullSrc}
        alt=""
        onClick={() => onImageClick(fullSrc)}
        style={{
          display: "block", maxWidth: "100%", borderRadius: 10,
          marginTop: 8, marginBottom: 4, cursor: "pointer",
        }}
      />
    );
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length > 1 ? <>{parts}</> : text;
}

async function downloadImageBlob(src: string, filename: string) {
  const token = sessionStorage.getItem("kaare_token");
  const res = await fetch(src, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  const { t } = useTranslation();
  const filename = src.split("/").pop()?.replace(/[?#].*/, "") + ".png" || "kare-bilde.png";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 2000,
        background: "rgba(0,0,0,0.88)",
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center", gap: 16,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{ position: "relative", maxWidth: "90vw", maxHeight: "82vh" }}
      >
        <img
          src={src}
          alt={t("home.img_alt")}
          style={{
            display: "block", maxWidth: "90vw", maxHeight: "82vh",
            borderRadius: 10, objectFit: "contain",
            boxShadow: "0 8px 48px #000a",
          }}
        />
        <button
          onClick={onClose}
          style={{
            position: "absolute", top: -14, right: -14,
            width: 30, height: 30, borderRadius: "50%",
            background: "#333", color: "#fff", border: "1px solid #555",
            fontSize: 18, cursor: "pointer", lineHeight: 1, padding: 0,
          }}
          title={t("home.close")}
        >×</button>
      </div>
      <div onClick={e => e.stopPropagation()} style={{ display: "flex", gap: 10 }}>
        <button
          onClick={() => downloadImageBlob(src, filename)}
          style={{
            padding: "10px 22px", borderRadius: 8, border: "none",
            background: "#646cff", color: "#fff", fontSize: 14,
            fontWeight: 600, cursor: "pointer",
          }}
        >{t("home.download")}</button>
        <button
          onClick={onClose}
          style={{
            padding: "10px 22px", borderRadius: 8,
            border: "1px solid #444", background: "#222",
            color: "#aaa", fontSize: 14, cursor: "pointer",
          }}
        >{t("home.close")}</button>
      </div>
    </div>
  );
}

function TraceView({ steps, defaultOpen }: { steps: TraceStep[]; defaultOpen?: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(defaultOpen ?? false);
  if (!steps.length) return null;
  return (
    <div style={{ marginTop: 4, fontSize: 12 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{ background: "none", border: "none", color: "#555", cursor: "pointer", padding: 0, fontSize: 12 }}
      >
        {open ? "▼" : "▶"} {t("home.trace_steps", { count: steps.length })}
      </button>
      {open && (
        <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
          {steps.map((s, i) => (
            <div key={i} style={{ fontFamily: "monospace", color: "#888", lineHeight: 1.4 }}>
              <span style={{ color: "#555" }}>R{s.round}</span>{" "}
              <span style={{ color: "#9cdcfe" }}>{s.tool}</span>
              {s.args && <span style={{ color: "#666" }}>({s.args})</span>}
              <span style={{ color: "#555" }}> → </span>
              <span style={{ color: "#6a9955" }}>{s.result}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const MK_COLOR = "#c084fc";   // lys lilla – rolig, varm
const MK_BG    = "#1a1228";   // mørk lilla bakgrunn

function MissKarePanel({ comment, onDismiss }: { comment: string; onDismiss: () => void }) {
  const { t } = useTranslation();
  return (
    <div style={{
      position: "fixed",
      bottom: 90,
      right: 24,
      width: 280,
      background: MK_BG,
      border: `1px solid ${MK_COLOR}44`,
      borderRadius: 14,
      padding: "14px 16px",
      boxShadow: `0 4px 24px #0009, 0 0 0 1px ${MK_COLOR}22`,
      zIndex: 100,
      animation: "mkFadeIn 0.3s ease",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{ color: MK_COLOR, fontSize: 13, fontWeight: 600, letterSpacing: "0.02em" }}>
          Miss Kåre
        </span>
        <button
          onClick={onDismiss}
          style={{ background: "none", border: "none", color: "#555", cursor: "pointer", fontSize: 16, padding: 0, lineHeight: 1 }}
          title={t("home.close")}
        >
          ×
        </button>
      </div>
      <p style={{ margin: 0, color: "#d4b8f0", fontSize: 14, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
        {comment}
      </p>
    </div>
  );
}

const S = {
  page: { display: "flex", flexDirection: "column" as const, flex: 1, overflow: "hidden", padding: "0 24px", width: "100%", boxSizing: "border-box" as const },
  messages: {
    flex: 1, overflowY: "auto" as const, padding: "24px 0", display: "flex",
    flexDirection: "column" as const, gap: 16, width: "100%",
  },
  bubble: (role: "user" | "kare", th: ReturnType<typeof useTheme>) => ({
    maxWidth: "70%",
    alignSelf: role === "user" ? "flex-end" as const : "flex-start" as const,
    background: role === "user" ? th.bubbleUserBg : "#1e1e1e",
    boxShadow: role === "kare" && th.bubbleKareShadow !== "none" ? th.bubbleKareShadow : undefined,
    border: role === "kare" && th.bubbleKareBorder !== "none" ? th.bubbleKareBorder : undefined,
    color: "#fff",
    padding: "12px 16px",
    borderRadius: role === "user" ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
    fontSize: th.chatFontSize,
    lineHeight: 1.5,
    whiteSpace: "pre-wrap" as const,
    animation: th.msgAnimation,
  }),
  thinking: { alignSelf: "flex-start" as const, color: "#555", fontSize: 14, padding: "8px 4px" },
  form: { display: "flex", flexDirection: "column" as const, gap: 8, padding: "16px 0 0", borderTop: "1px solid #222", position: "relative" as const },
  inputRow: { display: "flex", gap: 10, alignItems: "flex-end" },
  textarea: (fontSize: string) => ({
    flex: 1, padding: "12px 16px", borderRadius: 10, border: "1px solid #333",
    background: "#1a1a1a", color: "#fff", fontSize, outline: "none",
    resize: "none" as const, lineHeight: 1.5, minHeight: 46, maxHeight: 140,
    overflowY: "hidden" as const, fontFamily: "inherit",
  }),
  btn: {
    padding: "12px 24px", borderRadius: 10, border: "none",
    background: "#646cff", color: "#fff", fontSize: 15, fontWeight: 600, cursor: "pointer",
  },
  attachBtn: {
    padding: "12px 14px", borderRadius: 10, border: "1px solid #333",
    background: "#1e1e1e", color: "#888", fontSize: 18, cursor: "pointer", lineHeight: 1,
  },
};

type VoiceState = "idle" | "recording" | "transcribing";

function VoiceButton({
  onSend,
  disabled,
  sttEnabled = true,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
  sttEnabled?: boolean;
}) {
  const { t } = useTranslation();
  const [vs, setVs] = useState<VoiceState>("idle");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef   = useRef<Blob[]>([]);

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = e => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        setVs("transcribing");
        setMood("thinking");
        try {
          const blob = new Blob(chunksRef.current, { type: "audio/webm" });
          const { text } = await apiTranscribe(blob);
          if (text.trim()) onSend(text.trim());
          else setMood(getNightMood() ?? "idle");
        } catch {
          setMood(getNightMood() ?? "idle");
        }
        finally { setVs("idle"); }
      };
      mr.start();
      recorderRef.current = mr;
      setVs("recording");
      setMood("listening");
    } catch {
      setVs("idle");
    }
  };

  const stop = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
  };

  const icon = !sttEnabled ? "🎤" : vs === "idle" ? "🎤" : vs === "recording" ? "⏹" : "⏳";

  return (
    <button
      type="button"
      onClick={vs === "idle" && sttEnabled ? start : vs === "recording" ? stop : undefined}
      disabled={disabled || vs === "transcribing" || !sttEnabled}
      title={
        !sttEnabled ? t("home.voice_disabled") :
        vs === "idle" ? t("home.voice_start") :
        vs === "recording" ? t("home.voice_stop") :
        t("home.voice_transcribing")
      }
      style={{
        ...S.attachBtn,
        background: vs === "recording" ? "#7f1d1d" : "#1e1e1e",
        color:      !sttEnabled ? "#444" : vs === "recording" ? "#fca5a5" : "#888",
        border:     vs === "recording" ? "1px solid #c0392b" : "1px solid #333",
        animation:  vs === "recording" ? "voicePulse 1s ease-in-out infinite" : "none",
        opacity:    !sttEnabled ? 0.4 : 1,
        cursor:     !sttEnabled ? "not-allowed" : "pointer",
      }}
    >
      {icon}
    </button>
  );
}

function getUserId(): string {
  const raw = sessionStorage.getItem("kaare_user");
  return raw ? (JSON.parse(raw)?.username ?? "global") : "global";
}

export default function Home() {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<Message[]>([
    { role: "kare", text: t("home.greeting") },
  ]);
  const [input, setInput]               = useState("");
  const [loading, setLoading]           = useState(false);
  const [mkComment, setMkComment]       = useState<string | null>(null);
  const [pendingImages, setPendingImages] = useState<string[]>([]);
  const [lightboxSrc, setLightboxSrc]   = useState<string | null>(null);
  const theme                            = useTheme();
  const { prefs }                        = useUserPrefs();
  const [isDragging, setIsDragging]     = useState(false);
  const [inputFocused, setInputFocused] = useState(false);
  const [sttEnabled, setSttEnabled]     = useState(true);
  const bottomRef                        = useRef<HTMLDivElement>(null);
  const pollRef                          = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInputRef                     = useRef<HTMLInputElement>(null);
  const textareaRef                      = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    apiGetServices().then(svc => {
      setSttEnabled((svc as any).voice?.stt_enabled ?? true);
    }).catch(() => {});
  }, []);

  // Night-hours sleeping mood — checked on mount and every minute
  useEffect(() => {
    const update = () => {
      const night = getNightMood();
      if (night) setMood("sleeping");
      else if (document.documentElement.dataset.mood === "sleeping") setMood("idle");
    };
    update();
    const id = setInterval(update, 60_000);
    return () => clearInterval(id);
  }, []);

  // Restore chat history from STM on mount
  useEffect(() => {
    const uid = getUserId();
    apiChatHistory(uid, 60).then(({ turns }) => {
      if (!turns.length) return;
      const restored: Message[] = turns.map(t => ({
        role: t.role === "assistant" ? "kare" : "user",
        text: t.text,
      }));
      setMessages([...restored]);
    }).catch(() => { /* non-critical */ });
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // Grow textarea up to ~5 lines, then show scrollbar
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const maxHeight = 140;
    el.style.height = Math.min(el.scrollHeight, maxHeight) + "px";
    el.style.overflowY = el.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [input]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    const uid   = getUserId();
    let attempts = 0;
    const MAX    = 10; // 10 × 8s = 80s maks (dekker 9B warm-up)

    pollRef.current = setInterval(async () => {
      attempts++;
      try {
        const { comment } = await apiMissKareComment(uid);
        if (comment) {
          setMkComment(comment);
          stopPolling();
          return;
        }
      } catch { /* ikke kritisk */ }
      if (attempts >= MAX) stopPolling();
    }, 8000);
  }, [stopPolling]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    files.forEach(file => {
      const reader = new FileReader();
      reader.onload = () => {
        if (typeof reader.result === "string") {
          setPendingImages(prev => [...prev, reader.result as string]);
        }
      };
      reader.readAsDataURL(file);
    });
    e.target.value = "";
  };

  const doSend = useCallback(async (opts?: { text?: string; playTts?: boolean }) => {
    const text = (opts?.text ?? input).trim();
    if ((!text && pendingImages.length === 0) || loading) return;

    const imagesToSend = [...pendingImages];
    setInput("");
    setPendingImages([]);

    const base64Images = imagesToSend.map(url => url.split(",")[1]);

    setMessages(m => [...m, { role: "user", text, images: imagesToSend }]);
    setLoading(true);
    setMkComment(null);
    stopPolling();
    setMood("thinking");
    try {
      const res = await apiGenerate(text, base64Images);
      setMessages(m => [...m, { role: "kare", text: res.text, trace: res.trace }]);
      setMood("happy");
      setTimeout(() => setMood(getNightMood() ?? "idle"), 1500);
      if ((opts?.playTts || prefs.ttsAutoplay) && res.text) {
        apiTtsFile(res.text)
          .then(({ url }) => { const a = new Audio(url); a.play().catch(() => {}); })
          .catch(() => {});
      }
      if (prefs.mkPanelEnabled) startPolling();
    } catch {
      setMessages(m => [...m, { role: "kare", text: t("home.error") }]);
      setMood("alert");
      setTimeout(() => setMood(getNightMood() ?? "idle"), 2500);
    } finally {
      setLoading(false);
    }
  }, [input, pendingImages, loading, stopPolling, startPolling, prefs]);

  const send = (e: React.FormEvent) => {
    e.preventDefault();
    doSend();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const imageItems = Array.from(e.clipboardData.items).filter(it => it.type.startsWith("image/"));
    if (!imageItems.length) return;
    e.preventDefault();
    imageItems.forEach(item => {
      const file = item.getAsFile();
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        if (typeof reader.result === "string") setPendingImages(p => [...p, reader.result as string]);
      };
      reader.readAsDataURL(file);
    });
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (!isDragging) setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    Array.from(e.dataTransfer.files)
      .filter(f => f.type.startsWith("image/"))
      .forEach(file => {
        const reader = new FileReader();
        reader.onload = () => {
          if (typeof reader.result === "string") setPendingImages(p => [...p, reader.result as string]);
        };
        reader.readAsDataURL(file);
      });
  };

  return (
    <>
      <style>{`
        ${theme.keyframes}
        @keyframes mkFadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes voicePulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.45; }
        }
      `}</style>

      <div style={S.page}>
        <div style={S.messages}>
          <div style={{
            alignSelf: "center",
            color: "#7c4faa",
            fontSize: 12,
            padding: "6px 14px",
            borderRadius: 20,
            border: "1px solid #3a2050",
            background: "#130d1e",
            marginBottom: 4,
            letterSpacing: "0.01em",
          }}>
            <Trans i18nKey="home.miss_kare_hint" components={[<span style={{ color: MK_COLOR, fontWeight: 600 }} />]} />
          </div>
          {messages.map((m, i) => (
            <div key={i} style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start", maxWidth: "70%" }}>
              {m.images && m.images.length > 0 && (
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" as const, marginBottom: 6, justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
                  {m.images.map((img, ii) => (
                    <img key={ii} src={img} alt="" style={{ maxWidth: 200, maxHeight: 200, borderRadius: 10, objectFit: "cover" as const }} />
                  ))}
                </div>
              )}
              {(m.text || m.role === "kare") && (
                <div style={S.bubble(m.role, theme)}>
                  {m.role === "kare" ? renderKareText(m.text, setLightboxSrc) : m.text}
                </div>
              )}
              {m.role === "kare" && m.trace && m.trace.length > 0 && (
                <TraceView steps={m.trace} defaultOpen={prefs.showTrace} />
              )}
            </div>
          ))}
          {loading && (
            <div style={{ alignSelf: "flex-start", display: "flex", gap: 5, padding: "13px 16px", background: "#1e1e1e", borderRadius: "16px 16px 16px 4px" }}>
              {[0, 1, 2].map(i => (
                <span key={i} style={{
                  width: 8, height: 8, borderRadius: "50%", display: "inline-block",
                  background: theme.typingColor,
                  animation: `bounceDot 1.2s ease-in-out ${i * 0.2}s infinite`,
                }} />
              ))}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <form
          style={S.form}
          onSubmit={send}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {isDragging && (
            <div style={{
              position: "absolute", inset: 0, zIndex: 10,
              background: "#646cff1a", border: "2px dashed #646cff",
              borderRadius: 10, display: "flex", alignItems: "center",
              justifyContent: "center", color: "#646cff",
              fontSize: 15, fontWeight: 600, pointerEvents: "none",
            }}>
              {t("home.drop_hint")}
            </div>
          )}
          {pendingImages.length > 0 && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" as const }}>
              {pendingImages.map((img, i) => (
                <div key={i} style={{ position: "relative" as const }}>
                  <img src={img} alt="" style={{ width: 60, height: 60, borderRadius: 8, objectFit: "cover" as const }} />
                  <button
                    type="button"
                    onClick={() => setPendingImages(p => p.filter((_, j) => j !== i))}
                    style={{
                      position: "absolute" as const, top: -5, right: -5,
                      width: 18, height: 18, borderRadius: "50%",
                      border: "none", background: "#444", color: "#fff",
                      fontSize: 11, cursor: "pointer", padding: 0, lineHeight: 1,
                    }}
                  >×</button>
                </div>
              ))}
            </div>
          )}
          <div style={S.inputRow}>
            <textarea
              ref={textareaRef}
              style={{
                ...S.textarea(theme.chatFontSize),
                ...(inputFocused ? {
                  boxShadow: theme.inputFocusShadow !== "none" ? theme.inputFocusShadow : undefined,
                  borderColor: theme.inputFocusBorder,
                } : {}),
              }}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              onFocus={() => setInputFocused(true)}
              onBlur={() => setInputFocused(false)}
              placeholder={t("home.placeholder")}
              autoFocus
              disabled={loading}
              rows={1}
            />
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: "none" }}
              onChange={handleFileChange}
            />
            <button
              type="button"
              style={S.attachBtn}
              onClick={() => fileInputRef.current?.click()}
              disabled={loading}
              title={t("home.attach_title")}
            >🖼</button>
            <VoiceButton
              onSend={text => doSend({ text, playTts: true })}
              disabled={loading}
              sttEnabled={sttEnabled}
            />
            <button style={{ ...S.btn, background: theme.btnBg }} type="submit" disabled={loading || (!input.trim() && pendingImages.length === 0)}>
              {t("home.send")}
            </button>
          </div>
        </form>
      </div>

      {mkComment && (
        <MissKarePanel
          comment={mkComment}
          onDismiss={() => { setMkComment(null); stopPolling(); }}
        />
      )}

      {lightboxSrc && (
        <Lightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />
      )}
    </>
  );
}
