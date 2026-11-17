// What the Maintenance update card says after an update or a rollback (DT04-S05s / S05t). No imports on purpose: the
// logic is tested with Node in tests/test_s05s_update_card.py.

export type ReportState = {
  result?: string;
  rollback_kind?: string; // "auto" = a failed update put back; "admin" = an admin chose to go back
  reason_code?: string; // step_failed | interrupted | admin_request
  failed_step?: string;
  target?: string;
  from_version?: string;
  left_version?: string;
  error?: string;
  configs_restored?: string; // restored | restored_from_premigrate | missing | failed: …
};

export type Line = { key: string; vars?: Record<string, string> };

// How long the card waits for the API before it stops spinning and says so (S05s).
export const LOST_LIMIT_MS = 60_000;

export function rollbackHeadline(st: ReportState): Line {
  if (st.rollback_kind === "admin") {
    return { key: "system.update.rolled_back_admin", vars: { version: st.target || "—", from: st.left_version || "—" } };
  }
  return { key: "system.update.rolled_back", vars: { version: st.from_version || "—" } };
}

// The cause as a translated line; an older updater (no codes) keeps its own text; the admin's own choice has none.
export function rollbackCause(st: ReportState): Line | { raw: string } | null {
  if (st.reason_code === "admin_request" || st.rollback_kind === "admin") return null;
  if (st.reason_code === "step_failed" && st.failed_step) {
    return { key: "system.update.cause_step_failed", vars: { step: `system.update.phase.${st.failed_step}` } };
  }
  if (st.reason_code === "interrupted") {
    return { key: "system.update.cause_interrupted", vars: { step: `system.update.phase.${st.failed_step || "rollback"}` } };
  }
  return st.error ? { raw: st.error } : null;
}

export function configsLine(st: ReportState): Line | null {
  const v = st.configs_restored || "";
  if (v === "restored" || v === "restored_from_premigrate") return { key: "system.update.configs_restored" };
  if (v === "missing") return { key: "system.update.configs_missing" };
  if (v.startsWith("failed")) return { key: "system.update.configs_failed", vars: { error: v.replace(/^failed:\s*/, "") } };
  return null;
}

// Which view the card shows. «not_docker» is decided first: an install outside Docker (the developer's own house runs
// on systemd) never gets the update or rollback buttons, nor the «KTSB does not answer» waiting view of the updater.
// Buttons, steps and waiting views live only in «main».
export type CardStatus = { docker: boolean; reachable: boolean; problem?: string } | null;
export function cardMode(status: CardStatus, lostContact: boolean): "loading" | "not_docker" | "unreachable" | "problem" | "main" {
  if (status && !status.docker) return "not_docker";
  if (!status && !lostContact) return "loading";
  if (status && !status.reachable && !lostContact) return "unreachable";
  if (status?.problem) return "problem";
  return "main";
}

// DT04-S05s retest (Docker Desktop, API stopped): calls do not fail at once — they hang (~20 s per call there, or
// for good). The watch counts an in-flight call as failed after its own limit, starts the clock at the FIRST failure
// and resets it only on a real answer; the «does not answer» view needs no request to finish.
export const CALL_TIMEOUT_MS = 8_000;

export class ConnectionWatch {
  lostSince: number | null = null;
  private seq = 0;
  private inflight = new Map<number, number>(); // id → started at
  private callTimeoutMs: number;

  constructor(callTimeoutMs: number = CALL_TIMEOUT_MS) {
    this.callTimeoutMs = callTimeoutMs;
  }

  begin(t: number): number {
    const id = ++this.seq;
    this.inflight.set(id, t);
    return id;
  }

  // ok = a real answer from the server. A call already counted as failed (timed out) is ignored when it settles late.
  settle(id: number, ok: boolean, t: number): void {
    if (!this.inflight.has(id)) return;
    this.inflight.delete(id);
    if (ok) this.lostSince = null;
    else if (this.lostSince === null) this.lostSince = t;
  }

  tick(t: number): void {
    for (const [id, started] of this.inflight) {
      if (t - started >= this.callTimeoutMs) {
        this.inflight.delete(id);
        if (this.lostSince === null) this.lostSince = started + this.callTimeoutMs;
      }
    }
  }

  get lost(): boolean {
    return this.lostSince !== null;
  }

  verdict(t: number, probe: "opaque" | "error" | null) {
    return this.lostSince === null ? "waiting" as const : waitVerdict(this.lostSince, t, probe);
  }
}

// One call at a time: the next starts only after the previous has finished (or timed out) and the pause has passed —
// a fixed interval stacked hanging calls until the browser's six connections per host were all taken.
export async function pollLoop(load: () => Promise<unknown>, pause: () => Promise<unknown>, stopped: () => boolean): Promise<void> {
  while (!stopped()) {
    try { await load(); } catch { /* the watch has already counted it */ }
    if (stopped()) return;
    await pause();
  }
}

// probe: "opaque" = the server answered but the browser hid it (CORS); "error" = no answer at all; null = not known.
export function waitVerdict(lostSince: number, now: number, probe: "opaque" | "error" | null): "waiting" | "blocked" | "down" | "unknown" {
  if (now - lostSince < LOST_LIMIT_MS) return "waiting";
  if (probe === "opaque") return "blocked";
  if (probe === "error") return "down";
  return "unknown";
}
