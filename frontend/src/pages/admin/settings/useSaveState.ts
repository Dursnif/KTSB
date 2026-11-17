import { useState } from "react";

export type SaveState = "idle" | "saving" | "saved" | "error";

/** `applies` = what the server says the saved setting needs before it is in use (DT04-S05d):
 *  "now" | "restart:<service key>" — shown by SaveFeedback; a restart note stays a little longer. */
export function useSaveState() {
  const [state, setState] = useState<SaveState>("idle");
  const [applies, setApplies] = useState<string | undefined>(undefined);
  const saved = (applies?: string) => {
    setApplies(applies);
    setState("saved");
    setTimeout(() => { setState("idle"); setApplies(undefined); }, applies?.startsWith("restart:") ? 8000 : 3000);
  };
  const error = () => { setState("error"); setTimeout(() => setState("idle"), 4000); };
  return { state, applies, saving: () => setState("saving"), saved, error };
}
