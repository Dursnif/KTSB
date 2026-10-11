#!/usr/bin/env python3
"""
scripts/e2e_gui_test.py — Automated GUI smoke test (Playwright)

Drives a real headless browser against a running KTSB frontend — local or a
remote Docker test rig over the LAN — to replace manual click-testing:

  1. Login (handles the forced first-login PIN change screen)
  2. Onboarding wizard walkthrough — only runs if the target is unonboarded
  3. Admin page sweep — every /admin/* route, checked for:
       - JS exceptions (React error boundaries / uncaught errors)
       - browser console errors
       - failed network responses (HTTP >= 400) during page load

Never navigates away from what it's told to test and never resets/wipes
anything — read-only against whatever state the target is already in.

One-time setup — Playwright is a dev dependency (requirements-dev.txt), not part of
KTSB's runtime requirements.txt, so give it its own Python environment:
  python3 -m venv <e2e-venv>
  <e2e-venv>/bin/pip install -r requirements-dev.txt
  <e2e-venv>/bin/python -m playwright install chromium

Usage (<e2e-venv>/bin/python below):
  python scripts/e2e_gui_test.py \\
      --base-url http://192.168.0.x:5173 --username admin --pin ...

  # First login on a brand new, never-onboarded install (forces a PIN change):
  python scripts/e2e_gui_test.py \\
      --base-url http://192.168.0.x:5173 --username admin --pin 1234 --new-pin ...

  # Credentials can also come from the environment instead of argv:
  KAARE_TEST_PIN=... python scripts/e2e_gui_test.py --base-url ... --username admin

⚠️  The onboarding-wizard walkthrough (--skip-onboarding not set) is built by
    reading the React source (Onboarding.tsx), not live-verified end-to-end —
    every target tested so far was already onboarded, so that code path has
    only run against the live DOM structure, never a full live walkthrough.
    Validate it once against a genuinely fresh install before trusting it
    blindly for a release decision.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

GREEN, RED, YELLOW, CYAN, BOLD, RESET = (
    "\033[92m",
    "\033[91m",
    "\033[93m",
    "\033[96m",
    "\033[1m",
    "\033[0m",
)


def ok(msg: str) -> str:
    return f"{GREEN}✓{RESET} {msg}"


def err(msg: str) -> str:
    return f"{RED}✗{RESET} {msg}"


def warn(msg: str) -> str:
    return f"{YELLOW}~{RESET} {msg}"


def head(msg: str) -> str:
    return f"\n{BOLD}{CYAN}{msg}{RESET}"


ADMIN_PAGES = {
    "dashboard": "/admin",
    "users": "/admin/users",
    "system": "/admin/system",
    "agent-messages": "/admin/agent-messages",
    "reflections": "/admin/reflections",
    "tools": "/admin/tools",
    "aliases": "/admin/aliases",
    "nodes": "/admin/nodes",
    "security": "/admin/security",
    "settings": "/admin/settings",
}


# ── Login (incl. forced first-login PIN change) ─────────────────────────────


def do_login(page, base_url: str, username: str, pin: str, new_pin: str | None) -> str:
    """Returns one of: 'onboarding', 'admin', 'must_change_pin_no_new_pin',
    'login_failed', 'unexpected'."""
    page.goto(f"{base_url}/login", wait_until="networkidle")
    inputs = page.locator("form input")
    inputs.nth(0).fill(username)
    inputs.nth(1).fill(pin)
    page.locator("form button[type=submit]").first.click()
    page.wait_for_timeout(1000)

    if "/login" not in page.url:
        return "onboarding" if "/onboarding" in page.url else "admin"

    pw_count = page.locator('input[type="password"]').count()
    if pw_count >= 3:
        # Forced-change overlay: original pin field (1) + newPin + newPin2 (2 more).
        if not new_pin:
            return "must_change_pin_no_new_pin"
        page.locator('input[type="password"]').nth(1).fill(new_pin)
        page.locator('input[type="password"]').nth(2).fill(new_pin)
        page.locator("button[type=submit]").last.click()
        page.wait_for_timeout(1000)
        if "/login" not in page.url:
            return "onboarding" if "/onboarding" in page.url else "admin"
        return "unexpected"

    if pw_count == 1:
        return "login_failed"

    return "unexpected"


# ── Onboarding wizard (unverified live — see module docstring) ──────────────


def walk_onboarding(page, base_url: str) -> dict:
    result = {"ok": True, "steps": [], "created_test_user": None}

    def step_ok(name):
        result["steps"].append({"step": name, "ok": True})

    def step_fail(name, reason):
        result["ok"] = False
        result["steps"].append({"step": name, "ok": False, "reason": reason})

    # The wizard shell always renders 6 progress-indicator buttons (one per
    # STEP_KEYS entry) above the step content, so page-wide button/input
    # indices are contaminated by them. `.border-border.rounded-xl.p-6` is
    # the step content panel's own class combo (Tailwind utility classes,
    # stable, no bracket-syntax to escape) — scope everything to it instead.
    content = page.locator(".border-border.rounded-xl.p-6")

    def wait_for_signature(
        expected_buttons: int, expected_inputs: int, timeout_s: float = 6.0
    ) -> bool:
        """Poll until the content panel's button/input counts match the next
        step's expected shape, or timeout. Real verification instead of a
        fixed sleep-and-hope — a wrong click (e.g. re-clicking a preset
        instead of the submit button) leaves the counts unchanged, which
        this catches immediately instead of silently reporting 'ok'."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if (
                content.locator("button").count() == expected_buttons
                and content.locator("input").count() == expected_inputs
            ):
                return True
            page.wait_for_timeout(150)
        return False

    def describe_content() -> str:
        btns = content.locator("button")
        return "; ".join(f"btn[{i}]={btns.nth(i).inner_text()[:25]!r}" for i in range(btns.count()))

    try:
        # Step 0 — Profil (location): 6 inputs, 1 button (save).
        vals = ["Testby", "0000", "NO", "59.9", "10.7", "Europe/Oslo"]
        inputs = content.locator("input")
        for i, v in enumerate(vals):
            inputs.nth(i).fill(v)
        content.get_by_role("button").nth(0).click()
        if not wait_for_signature(expected_buttons=2, expected_inputs=3):
            step_fail("profil", f"didn't reach 'bruker' step. Now: {describe_content()}")
            return result
        step_ok("profil")

        # Step 1 — Bruker: 3 inputs (name/username/pin) + button pair (create, skip).
        username = f"e2e_test_{int(time.time())}"
        test_pin = "483920"
        content.locator("input").nth(0).fill("E2E Test")
        content.locator("input").nth(1).fill(username)
        content.locator('input[type="password"]').first.fill(test_pin)
        content.get_by_role("button").nth(0).click()  # "create" — first of the pair
        if not wait_for_signature(expected_buttons=4, expected_inputs=0):
            step_fail("bruker", f"didn't reach 'distribusjon' step. Now: {describe_content()}")
            return result
        result["created_test_user"] = {"username": username, "pin": test_pin}
        step_ok("bruker")

        # Step 2 — Distribusjon: 4 buttons = 3 presets + submit (index 3, NOT 0).
        content.locator(".grid > button").nth(1).click()  # "medium" preset
        content.get_by_role("button").nth(3).click()  # submit — after the 3 presets
        if not wait_for_signature(expected_buttons=4, expected_inputs=0):
            step_fail("distribusjon", f"didn't reach 'llm' step. Now: {describe_content()}")
            return result
        step_ok("distribusjon")

        # Step 3 — LLM source: 4 buttons = 2 choice + save/skip (default "builtin", 0 inputs).
        content.get_by_role("button").nth(2).click()  # save — after the 2 choice buttons
        if not wait_for_signature(expected_buttons=2, expected_inputs=4):
            step_fail("llm", f"didn't reach 'integrasjoner' step. Now: {describe_content()}")
            return result
        step_ok("llm")

        # Step 4 — Integrasjoner: 4 inputs (all optional, left empty), 2 buttons (save/skip).
        content.get_by_role("button").nth(0).click()
        if not wait_for_signature(expected_buttons=1, expected_inputs=0):
            step_fail("integrasjoner", f"didn't reach 'ferdig' step. Now: {describe_content()}")
            return result
        step_ok("integrasjoner")

        # Step 5 — Ferdig: 1 button, navigates to /admin.
        content.get_by_role("button").nth(0).click()
        page.wait_for_timeout(1000)
        if "/admin" in page.url:
            step_ok("ferdig")
        else:
            step_fail("ferdig", f"expected /admin, landed on {page.url}")

    except PWTimeout as e:
        step_fail("timeout", str(e))
    except Exception as e:
        step_fail("exception", str(e))

    return result


# ── Admin page sweep ─────────────────────────────────────────────────────────


def sweep_admin_pages(page, base_url: str, shots_dir: Path) -> list[dict]:
    results = []
    for key, path in ADMIN_PAGES.items():
        console_errors: list[str] = []
        page_errors: list[str] = []
        bad_responses: list[str] = []

        def on_console(msg, _bucket=console_errors):
            if msg.type == "error":
                _bucket.append(msg.text[:300])

        def on_pageerror(exc, _bucket=page_errors):
            _bucket.append(str(exc)[:300])

        def on_response(resp, _bucket=bad_responses):
            if resp.status >= 400:
                _bucket.append(f"{resp.status} {resp.url}")

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("response", on_response)
        try:
            page.goto(f"{base_url}{path}", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(500)
            landed_elsewhere = path not in page.url
            passed = not page_errors and not bad_responses and not landed_elsewhere
            entry = {
                "page": key,
                "path": path,
                "ok": passed,
                "console_errors": console_errors,
                "page_errors": page_errors,
                "bad_responses": bad_responses,
                "landed_on": page.url if landed_elsewhere else None,
            }
            if not passed:
                shot = shots_dir / f"{key}.png"
                page.screenshot(path=str(shot))
                entry["screenshot"] = str(shot)
            results.append(entry)
        except PWTimeout as e:
            shot = shots_dir / f"{key}_timeout.png"
            try:
                page.screenshot(path=str(shot))
            except Exception:
                shot = None
            results.append(
                {
                    "page": key,
                    "path": path,
                    "ok": False,
                    "console_errors": console_errors,
                    "page_errors": page_errors,
                    "bad_responses": bad_responses,
                    "error": f"timeout: {e}",
                    "screenshot": str(shot) if shot else None,
                }
            )
        finally:
            page.remove_listener("console", on_console)
            page.remove_listener("pageerror", on_pageerror)
            page.remove_listener("response", on_response)
    return results


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base-url", default="http://localhost:5173")
    p.add_argument("--username", default="admin")
    p.add_argument("--pin", default=os.environ.get("KAARE_TEST_PIN"))
    p.add_argument("--new-pin", default=os.environ.get("KAARE_TEST_NEW_PIN"))
    p.add_argument(
        "--skip-onboarding",
        action="store_true",
        help="Never attempt the onboarding wizard, even if the target lands there.",
    )
    p.add_argument(
        "--headed", action="store_true", help="Show the browser window (default: headless)."
    )
    args = p.parse_args()

    if not args.pin:
        print(err("No PIN given (--pin or $KAARE_TEST_PIN)."))
        sys.exit(1)

    run_dir = Path("/kaare/logs/e2e_gui_test") / datetime.now().strftime("%Y%m%d_%H%M%S")
    shots_dir = run_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    report = {"base_url": args.base_url, "started": datetime.now().isoformat()}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page()

        print(head(f"Logging in as {args.username} @ {args.base_url}"))
        state = do_login(page, args.base_url, args.username, args.pin, args.new_pin)
        report["login_result"] = state

        if state == "login_failed":
            print(err("Login failed — wrong username/PIN."))
            browser.close()
            _write_report(run_dir, report)
            sys.exit(1)
        if state == "must_change_pin_no_new_pin":
            print(
                err(
                    "Target is on the forced first-login PIN-change screen. Re-run with --new-pin to set one."
                )
            )
            browser.close()
            _write_report(run_dir, report)
            sys.exit(1)
        if state == "unexpected":
            shot = run_dir / "unexpected_login_state.png"
            page.screenshot(path=str(shot))
            print(err(f"Unexpected state after login (url={page.url}) — screenshot: {shot}"))
            browser.close()
            _write_report(run_dir, report)
            sys.exit(1)
        print(ok(f"Logged in, landed on: {state}"))

        if state == "onboarding":
            if args.skip_onboarding:
                print(
                    warn(
                        "Target is unonboarded and --skip-onboarding is set. Nothing more to test."
                    )
                )
                browser.close()
                _write_report(run_dir, report)
                sys.exit(0)
            print(head("Walking onboarding wizard (unverified live — see script docstring)"))
            onb = walk_onboarding(page, args.base_url)
            report["onboarding"] = onb
            for s in onb["steps"]:
                print(ok(s["step"]) if s["ok"] else err(f"{s['step']}: {s.get('reason')}"))
            if onb.get("created_test_user"):
                u = onb["created_test_user"]
                print(
                    warn(
                        f"Created test user '{u['username']}' (PIN {u['pin']}) — delete it via Admin → Users if not wanted."
                    )
                )
            if not onb["ok"]:
                print(err("Onboarding wizard did not complete — stopping before the admin sweep."))
                browser.close()
                _write_report(run_dir, report)
                sys.exit(1)

        print(head("Sweeping admin pages"))
        pages = sweep_admin_pages(page, args.base_url, shots_dir)
        report["admin_pages"] = pages
        passed = sum(1 for r in pages if r["ok"])
        for r in pages:
            if r["ok"]:
                print(ok(r["page"]))
            else:
                detail = (
                    r.get("error")
                    or "; ".join(r["console_errors"] + r["page_errors"] + r["bad_responses"])
                    or f"landed on {r.get('landed_on')}"
                )
                print(err(f"{r['page']}: {detail}"))

        browser.close()
        report["passed"] = passed
        report["total"] = len(pages)
        _write_report(run_dir, report)

        print(head(f"Result: {passed}/{len(pages)} admin pages clean"))
        print(f"Full report: {run_dir / 'report.json'}")
        sys.exit(0 if passed == len(pages) else 1)


def _write_report(run_dir: Path, report: dict) -> None:
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
