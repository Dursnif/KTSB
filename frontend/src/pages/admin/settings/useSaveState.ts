import { useState } from "react";

export type SaveState = "idle" | "saving" | "saved" | "error";

export function useSaveState() {
  const [state, setState] = useState<SaveState>("idle");
  const saved = () => { setState("saved"); setTimeout(() => setState("idle"), 3000); };
  const error = () => { setState("error"); setTimeout(() => setState("idle"), 4000); };
  return { state, saving: () => setState("saving"), saved, error };
}
