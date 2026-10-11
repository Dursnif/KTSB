// Copy text to the clipboard, robustly. navigator.clipboard is missing outside a secure context
// (plain-HTTP LAN access to the GUI) and can also reject (permission, focus loss) — fall back to
// a hidden textarea + execCommand before giving up. Resolves true when the text was copied.
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the textarea fallback
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
