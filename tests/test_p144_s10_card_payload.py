"""
P144-s10 — the Frigate model cards send the right `share_with` (GUI, mocked API — no real login, no writes).

- Basic: «Borrow the model from» offers Main agent / Sub-agents only; a choice saves at once and sends
  exactly {share_with: …} (no own fields → the lender's Ollama never reloads). The model line follows the choice.
- Expert: a third choice «Own model» shows the full package; Save sends share_with null + the own fields.
- No horizontal scroll at 1900 px and 390 px.

Needs Playwright (the envs/e2e venv) and the built frontend on 127.0.0.1:5173 — skipped otherwise.
Run with: cd /kaare && <e2e venv>/bin/python -m pytest tests/test_p144_s10_card_payload.py -v
"""

import json
import os
import re
import socket

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

_BASE = os.environ.get("KTSB_GUI_URL", "http://127.0.0.1:5173")
_SHOTS = os.environ.get("P144_S10_SHOTS")  # optional folder for evidence screenshots

_LLM = {
    "default": {"provider": "vllm", "base_url": "http://127.0.0.1:11440", "model_role": "kare", "model": "big-27b"},
    "miss_kare": {
        "provider": "ollama", "base_url": "http://127.0.0.1:11447", "model_role": "miss_kare",
        "model": "agent-9b", "options": {"num_ctx": 32768}, "enabled": True,
    },
}
for _r in ("frigate", "frigate_conclude"):
    _LLM[_r] = {**_LLM["default"], "share_with": "default", "enabled": True}

_USER = {
    "id": 1, "username": "admin", "display_name": "Bruker X", "role": "admin", "avatar": "",
    "is_active": True, "is_online": True, "last_seen": None,
}


def _gui_up() -> bool:
    host, port = _BASE.split("//")[1].split(":")
    try:
        socket.create_connection((host, int(port)), timeout=1).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _gui_up(), reason="frontend not served")


@pytest.fixture
def gui():
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


def _open(browser, level: str, width: int):
    puts: list = []
    ctx = browser.new_context(viewport={"width": width, "height": 1000})
    ctx.add_init_script(
        f"sessionStorage.setItem('kaare_token','mock');"
        f"sessionStorage.setItem('kaare_user',{json.dumps(json.dumps(_USER))});"
        f"localStorage.setItem('kaare_settings_level','{level}');"
    )
    page = ctx.new_page()

    def handle(route):
        req = route.request
        path = req.url.split("/api/", 1)[1].split("?")[0]
        if req.method == "PUT" and path.startswith("settings/llm/"):
            puts.append((path.rsplit("/", 1)[1], json.loads(req.post_data or "{}")))
            return route.fulfill(json={"ok": True, "warmup_started": False})
        if path == "settings/llm":
            return route.fulfill(json=_LLM)
        return route.fulfill(status=503, json={})  # every other card shows its own error state

    page.route("**/api/**", handle)
    page.goto(f"{_BASE}/admin/integrations")
    page.get_by_role("tab", name="Frigate").click()
    page.locator('[data-borrow="default"]').first.wait_for(timeout=10000)
    return page, puts


def _card(page, index: int):
    return page.locator('[role="radiogroup"]').nth(index)


def _no_hscroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("width", [1900, 390])
def test_basic_borrow_choice_sends_only_share_with(gui, width):
    page, puts = _open(gui, "basic", width)
    see = _card(page, 0)
    assert see.locator('[data-borrow="own"]').count() == 0
    # Borrowing: no Show/Hide button on either Frigate card (nothing to unfold).
    assert page.locator("[data-collapse-toggle]").count() == 0
    assert page.get_by_role("button", name=re.compile(r"^(Vis|Skjul)$")).count() == 0  # by name, not only the marker
    assert see.locator('[data-borrow="default"]').get_attribute("aria-checked") == "true"
    if _SHOTS:
        page.screenshot(path=f"{_SHOTS}/basic_{width}.png", full_page=True)
    see.locator('[data-borrow="miss_kare"]').click()
    page.wait_for_timeout(300)
    assert puts == [("frigate", {"share_with": "miss_kare"})]
    assert page.get_by_text("agent-9b").count() >= 1
    see.locator('[data-borrow="default"]').click()
    page.wait_for_timeout(300)
    assert puts[-1] == ("frigate", {"share_with": "default"})
    # The conclude card has its own choice.
    _card(page, 1).locator('[data-borrow="miss_kare"]').click()
    page.wait_for_timeout(300)
    assert puts[-1] == ("frigate_conclude", {"share_with": "miss_kare"})
    _no_hscroll(page)


@pytest.mark.parametrize("width", [1900, 390])
def test_expert_own_model_sends_own_fields(gui, width):
    page, puts = _open(gui, "expert", width)
    see = _card(page, 0)
    assert page.locator("[data-collapse-toggle]").count() == 0  # both cards borrow
    see.locator('[data-borrow="own"]').click()
    page.wait_for_timeout(300)
    assert page.locator("[data-collapse-toggle]").count() == 1  # only the Own model card can fold
    assert page.get_by_role("button", name=re.compile(r"^(Vis|Skjul)$")).count() == 1  # the name check does find it
    assert puts == []  # Own model is saved with Save, after the fields are filled
    if _SHOTS:
        page.screenshot(path=f"{_SHOTS}/expert_own_{width}.png", full_page=True)
    # The Save inside the model card (the Frigate connection card above has its own Save).
    page.locator("div").filter(has=see).filter(has=page.get_by_role("button", name="Lagre")).last \
        .get_by_role("button", name="Lagre").click()
    page.wait_for_timeout(300)
    role, body = puts[-1]
    assert role == "frigate" and body["share_with"] is None
    assert body["provider"] == "vllm" and body["base_url"] == "http://127.0.0.1:11440" and body["model"] == "big-27b"
    _no_hscroll(page)
