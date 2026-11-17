"""DT04-S05s / S05t — the Maintenance update card tells the truth after a rollback and never spins forever.

S05s: «KTSB starter på nytt …» spun without end while the API never came back (CORS after a rollback). Now, after a
fixed limit, the card says KTSB does not answer, offers «Reload page» and, when the browser can tell, why: the server
answers but the browser blocks the answer (CORS) vs. no answer at all.
S05t: an admin's own rollback was shown as «the update failed … cause: requested by an admin» (raw English). Now the
card tells «you went back» apart from an automatic rollback, and the cause comes from codes with nb/en/de text.

The logic lives in frontend/src/lib/updateReport.ts (no imports) and is run here with Node's type stripping.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "frontend" / "src" / "lib" / "updateReport.ts"
LOCALES = ROOT / "frontend" / "src" / "locales"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(tmp_path, body: str):
    script = tmp_path / "t.mts"
    script.write_text(f"import * as r from {json.dumps(str(LIB))};\nconsole.log(JSON.stringify({body}));\n")
    out = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", str(script)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_admin_rollback_is_not_called_a_failure(tmp_path):
    st = {"result": "rolled_back", "rollback_kind": "admin", "reason_code": "admin_request",
          "target": "v1.10.3", "left_version": "v1.11.1", "from_version": "v1.10.3", "error": "requested by an admin"}
    got = _run(tmp_path, f"[r.rollbackHeadline({json.dumps(st)}), r.rollbackCause({json.dumps(st)})]")
    assert got[0] == {"key": "system.update.rolled_back_admin", "vars": {"version": "v1.10.3", "from": "v1.11.1"}}
    assert got[1] is None  # no «cause» line for the admin's own choice


def test_automatic_rollback_names_the_failed_step_by_code(tmp_path):
    st = {"result": "rolled_back", "rollback_kind": "auto", "reason_code": "step_failed", "failed_step": "health",
          "from_version": "v1.11.0", "target": "v1.11.1", "error": "API never answered"}
    got = _run(tmp_path, f"[r.rollbackHeadline({json.dumps(st)}), r.rollbackCause({json.dumps(st)})]")
    assert got[0] == {"key": "system.update.rolled_back", "vars": {"version": "v1.11.0"}}
    assert got[1] == {"key": "system.update.cause_step_failed", "vars": {"step": "system.update.phase.health"}}


def test_an_older_updater_without_codes_still_shows_its_text(tmp_path):
    st = {"result": "rolled_back", "from_version": "v1.10.3", "error": "health check failed"}
    got = _run(tmp_path, f"r.rollbackCause({json.dumps(st)})")
    assert got == {"raw": "health check failed"}


@pytest.mark.parametrize("value,key", [
    ("restored", "system.update.configs_restored"),
    ("restored_from_premigrate", "system.update.configs_restored"),
    ("missing", "system.update.configs_missing"),
    ("failed: disk full", "system.update.configs_failed"),
    ("", None),
])
def test_config_restore_is_said(tmp_path, value, key):
    got = _run(tmp_path, f"r.configsLine({json.dumps({'configs_restored': value})})")
    assert (got and got["key"]) == key


def test_waiting_ends_after_the_limit_with_a_reason(tmp_path):
    got = _run(tmp_path, "[r.LOST_LIMIT_MS, r.waitVerdict(0, 30000, null), r.waitVerdict(0, 61000, 'opaque'),"
                         " r.waitVerdict(0, 61000, 'error'), r.waitVerdict(0, 61000, null)]")
    assert got[0] == 60000
    assert got[1:] == ["waiting", "blocked", "down", "unknown"]


def test_every_new_text_exists_in_nb_en_de():
    keys = ["rolled_back_admin", "cause_step_failed", "cause_interrupted", "configs_restored", "configs_missing",
            "configs_failed", "no_answer", "no_answer_blocked", "no_answer_down", "no_answer_unknown"]
    for lang in ("nb", "en", "de"):
        upd = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))["system"]["update"]
        missing = [k for k in keys if not upd.get(k)]
        assert not missing, (lang, missing)


def test_the_card_uses_the_helpers_and_no_raw_error_for_admin():
    src = (ROOT / "frontend" / "src" / "pages" / "admin" / "System.tsx").read_text(encoding="utf-8")
    for name in ("rollbackHeadline", "rollbackCause", "configsLine", "ConnectionWatch", "pollLoop"):
        assert re.search(rf"\b{name}\b", src), name


def test_outside_docker_the_card_shows_only_the_not_docker_text(tmp_path):
    """The developer's house runs outside Docker: never an update/rollback button, never the updater's waiting view —
    also when the API was lost for a while."""
    got = _run(tmp_path, "[r.cardMode({docker: false, reachable: true}, false), r.cardMode({docker: false, reachable: false}, true),"
                         " r.cardMode({docker: false, reachable: true, problem: 'no_home'}, true),"
                         " r.cardMode({docker: true, reachable: true}, true), r.cardMode(null, false)]")
    assert got == ["not_docker", "not_docker", "not_docker", "main", "loading"]


def test_buttons_and_waiting_views_live_only_in_the_main_view():
    src = (ROOT / "frontend" / "src" / "pages" / "admin" / "System.tsx").read_text(encoding="utf-8")
    start = src.index("const mode = cardMode(status, lostContact);")
    main_from = src.index('} else if (mode === "problem")', start)
    not_docker_branch = src[start:src.index('} else if (mode === "loading")', start)]
    assert "system.update.not_docker" in not_docker_branch
    for key in ("system.update.update_button", "system.update.rollback_button", "system.update.no_answer",
                "system.update.restarting"):
        first = src.find(key, start)
        assert first > main_from, key  # only after the not_docker/loading/unreachable/problem branches


# ── DT04-S05s retest (images g, Docker Desktop): calls that HANG, not «refused» ─────────────────────────────────────

def test_a_call_that_hangs_forever_still_ends_the_wait(tmp_path):
    """Every status call hangs and never settles. The watch counts a call as failed after its own time limit, the
    clock starts at that FIRST failure, and the «does not answer» view comes LOST_LIMIT_MS later — no request needs
    to finish. With no per-call limit (what the card did) it would wait forever: shown red with callTimeoutMs=Infinity."""
    got = _run(tmp_path, """(() => {
      const w = new r.ConnectionWatch(8000);
      w.begin(0);                           // first status call, never settles
      const out = [];
      for (const t of [5000, 8000, 30000, 67000, 68001]) { w.tick(t); out.push([t, w.lostSince, w.verdict(t, null)]); }
      const noLimit = new r.ConnectionWatch(Infinity); noLimit.begin(0); noLimit.tick(600000);
      out.push(noLimit.verdict(600000, null));
      return out;
    })()""")
    assert got[:5] == [[5000, None, "waiting"], [8000, 8000, "waiting"], [30000, 8000, "waiting"],
                       [67000, 8000, "waiting"], [68001, 8000, "unknown"]]
    assert got[5] == "waiting"  # the old behaviour: red — never ends


def test_only_a_real_answer_resets_the_clock(tmp_path):
    got = _run(tmp_path, """(() => {
      const w = new r.ConnectionWatch(8000);
      const a = w.begin(0); w.settle(a, false, 1000);      // first failure at 1 s
      const b = w.begin(4000); w.settle(b, false, 9000);   // later failures do not move the clock
      const c = w.begin(12000); w.tick(30000);             // a hanging one times out — still the same clock
      const keep = w.lostSince;
      w.settle(c, true, 31000);                            // a late real answer for a call already counted failed: ignored
      const afterLate = w.lostSince;
      const d = w.begin(40000); w.settle(d, true, 40500);  // a real answer → reset
      return [keep, afterLate, w.lostSince, w.verdict(200000, null)];
    })()""")
    assert got == [1000, 1000, None, "waiting"]


def test_poll_loop_never_overlaps_and_waits_between_calls(tmp_path):
    got = _run(tmp_path, """await (async () => {
      let running = 0, maxRunning = 0, calls = 0;
      const stop = { v: false };
      const load = async () => { running++; calls++; maxRunning = Math.max(maxRunning, running);
                                 await new Promise(res => setTimeout(res, 30)); running--; if (calls >= 4) stop.v = true; };
      await r.pollLoop(load, () => new Promise(res => setTimeout(res, 5)), () => stop.v);
      return [calls, maxRunning];
    })()""")
    assert got == [4, 1]
