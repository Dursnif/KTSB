"""DT03 R7 — restore from backup never worked (found live on Verksted-pc, 2026-10-06).

1. The restore endpoints read the user from `_u["username"]`, but the JWT carries it in `sub` →
   the PIN check always failed («Invalid PIN») for every admin, from the GUI and the API.
2. The restart after a restore was `sudo systemctl restart kaare.service`, which does not exist in
   the Docker image → the restored data never took effect there.
"""

import io
import signal

from fastapi import FastAPI
from fastapi.testclient import TestClient

import kaare_core.users.auth as auth
from kaare_core.routers import router_backup


def _client(monkeypatch, restored: list):
    monkeypatch.setattr(auth, "touch_last_seen", lambda _u: None)
    monkeypatch.setattr(
        router_backup,
        "get_user_with_hash",
        lambda u: {"username": u, "pin_hash": "h"} if u == "admin" else None,
    )
    monkeypatch.setattr(router_backup, "verify_pin", lambda pin, h: pin == "482913")

    async def fake_restore(content, cats, username):
        restored.append(username)
        return {"errors": [], "restart_needed": False, "restored": sorted(cats)}

    monkeypatch.setattr(router_backup, "_restore_from_zip", fake_restore)
    app = FastAPI()
    app.include_router(router_backup.router)
    return TestClient(app)


def _post(c, pin):
    tok = auth.create_token("admin", "admin")
    return c.post(
        "/api/backup/restore",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("b.zip", io.BytesIO(b"zip"), "application/zip")},
        data={"categories": '["config"]', "pin": pin},
    )


def test_restore_accepts_the_admins_real_pin(monkeypatch):
    restored = []
    c = _client(monkeypatch, restored)
    r = _post(c, "482913")
    assert r.status_code == 200, r.text
    assert restored == ["admin"]


def test_restore_rejects_a_wrong_pin(monkeypatch):
    restored = []
    c = _client(monkeypatch, restored)
    assert _post(c, "000000").status_code == 403
    assert restored == []


def test_restart_after_restore_uses_sigterm_in_docker(monkeypatch):
    sent = []
    monkeypatch.setattr(router_backup, "_IN_DOCKER", True)
    monkeypatch.setattr(router_backup.time, "sleep", lambda s: None)
    monkeypatch.setattr(router_backup.os, "kill", lambda pid, sig: sent.append(sig))
    monkeypatch.setattr(router_backup.subprocess, "run", lambda *a, **k: sent.append("systemctl"))
    router_backup._restart_kaare_service()
    assert sent == [signal.SIGTERM]


def test_restart_after_restore_uses_systemd_on_bare_metal(monkeypatch):
    sent = []
    monkeypatch.setattr(router_backup, "_IN_DOCKER", False)
    monkeypatch.setattr(router_backup.time, "sleep", lambda s: None)
    monkeypatch.setattr(router_backup.os, "kill", lambda pid, sig: sent.append(sig))
    monkeypatch.setattr(router_backup.subprocess, "run", lambda *a, **k: sent.append("systemctl"))
    router_backup._restart_kaare_service()
    assert sent == ["systemctl"]
