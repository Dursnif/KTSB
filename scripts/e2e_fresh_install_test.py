#!/usr/bin/env python3
"""
scripts/e2e_fresh_install_test.py — One-off: validate the onboarding wizard
against a genuinely fresh, unonboarded install.

Not part of the routine GUI sweep (e2e_gui_test.py) — that one deliberately
never resets state. This script is for when you've stood up a disposable
fresh instance (e.g. via Docker, empty configs/state/data) and want to
confirm the wizard's click-path actually works, since that branch of
walk_onboarding() can't be exercised against an already-onboarded target.

Assumes the frontend is served on one host:port and the API on a different
one (a throwaway instance often needs a remapped API port to avoid clashing
with a real install on the same machine) — rewrites the frontend's hardcoded
`{hostname}:8000` API calls to the real API port via Playwright request
interception, so no app code needs to change for the test.

Usage:
  PYTHONPATH=/kaare /kaare/venv/bin/python scripts/e2e_fresh_install_test.py \\
      --frontend-url http://127.0.0.1:15173 --api-port 18000 \\
      --username admin --pin 1234 --new-pin <ny PIN, min 6 siffer>
"""

import argparse
import re
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/kaare/scripts")
from e2e_gui_test import do_login, walk_onboarding, GREEN, RED, YELLOW, CYAN, BOLD, RESET, ok, err, warn, head  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--frontend-url", required=True)
    p.add_argument("--api-port", type=int, required=True,
                    help="Real host port the API container is published on (frontend code always calls :8000).")
    p.add_argument("--username", default="admin")
    p.add_argument("--pin", required=True)
    p.add_argument("--new-pin", default=None, help="Only needed on the very first login (forced PIN change).")
    p.add_argument("--headed", action="store_true")
    args = p.parse_args()

    from urllib.parse import urlparse
    fe_host = urlparse(args.frontend_url).hostname

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page()

        # Rewrite http://<fe_host>:8000/* -> http://<fe_host>:<api-port>/*
        pattern = re.compile(rf"^http://{re.escape(fe_host)}:8000/")

        def rewrite(route):
            url = route.request.url
            if pattern.match(url):
                new_url = url.replace(f"://{fe_host}:8000/", f"://{fe_host}:{args.api_port}/")
                route.continue_(url=new_url)
            else:
                route.continue_()

        page.route("**/*", rewrite)

        print(head(f"Logging in as {args.username} @ {args.frontend_url} (API rewritten to :{args.api_port})"))
        state = do_login(page, args.frontend_url, args.username, args.pin, args.new_pin)
        print(f"Login result: {state}")
        if state not in ("admin", "onboarding"):
            shot = "/tmp/fresh_install_login_fail.png"
            page.screenshot(path=shot)
            print(err(f"Could not get past login. Screenshot: {shot}"))
            browser.close()
            sys.exit(1)
        print(ok(f"Logged in, landed on: {state}"))

        print(head("Navigating to /onboarding explicitly (app never auto-redirects there)"))
        page.goto(f"{args.frontend_url}/onboarding", wait_until="networkidle")
        if "/onboarding" not in page.url:
            print(err(f"Could not reach /onboarding, landed on {page.url}"))
            browser.close()
            sys.exit(1)

        print(head("Walking onboarding wizard"))
        result = walk_onboarding(page, args.frontend_url)
        for s in result["steps"]:
            print(ok(s["step"]) if s["ok"] else err(f"{s['step']}: {s.get('reason')}"))
        if result.get("created_test_user"):
            u = result["created_test_user"]
            print(warn(f"Created test user '{u['username']}' (PIN {u['pin']}) inside the fresh instance."))

        browser.close()
        print(head("Result: PASS" if result["ok"] else "Result: FAIL"))
        sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
