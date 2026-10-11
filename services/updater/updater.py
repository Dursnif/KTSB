"""KTSB updater (P57) — replaces a Docker install with a newer release, checks it, and rolls back on failure.

Runs in its own container (`kaare-updater`) with the Docker socket and the install folder mounted at the path the
Docker engine uses for it (KTSB_HOME — the real path on Linux/macOS, /run/desktop/mnt/host/<drive>/… on Docker
Desktop). Compose run from here then resolves the compose file's relative paths exactly as on the host (P57 V1/V10).

The API is the only client, over a Unix socket in state/updater/. Every request carries the install's service token
(configs/service_token.env). Three actions change anything: `update <version>`, `rollback`, and the automatic
resume after a restart in the middle of an update. Nothing here takes an image name, registry or URL from a caller.

An update, in order:
  preflight → download bundle → verify bundle signature + checksum → keep the current files in previous/ →
  put the new files in place → pull images (old containers keep running) → verify every own image's signature and
  that the pulled digest is the verified one → recreate all services except this one → health check →
  done, or put previous/ back and recreate again.
Data folders (configs/, state/, data/, logs/, models/) are never written by an update; the API takes the
pre-update save point before it calls `update`.

Standard library only, plus the docker CLI (with compose) and cosign in the image.
"""

import asyncio
import contextlib
import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# ── Fixed trust anchors ────────────────────────────────────────────────────────
# Where releases come from and who must have signed them. A fork sets KTSB_UPDATE_REPO / KTSB_UPDATE_REGISTRY in
# its compose file; nothing a request carries can change them.
REPO = os.environ.get("KTSB_UPDATE_REPO", "Dursnif/KTSB")
REGISTRY = os.environ.get("KTSB_UPDATE_REGISTRY", "ghcr.io/dursnif").rstrip("/")
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
_owner, _, _name = REPO.partition("/")
SIGNER_IDENTITY = (
    rf"^https://github\.com/(?i:{re.escape(_owner)}/{re.escape(_name)})"
    r"/\.github/workflows/build\.yml@refs/tags/v"
)

PROJECT = "kaare"
SELF_SERVICE = "kaare-updater"
API_URL = os.environ.get("KTSB_UPDATER_API_URL", "http://kaare-api:8000")

# ── Install layout ─────────────────────────────────────────────────────────────
HOME = Path(os.environ.get("KTSB_HOME") or "/nonexistent")
UPD_DIR = HOME / "state" / "updater"
# The socket has its own mount (./state/updater → /run/ktsb-updater), so the API can reach it — and show why updates
# are off — even when KTSB_HOME is missing.
SOCK_PATH = Path("/run/ktsb-updater/updater.sock")
STATE_PATH = UPD_DIR / "state.json"
PREV_DIR = UPD_DIR / "previous"
STAGE_DIR = UPD_DIR / "staging"
TOKEN_PATH = HOME / "configs" / "service_token.env"
AUDIT_PATH = HOME / "logs" / "audit.log"
LOG_PATH = HOME / "logs" / "updater.log"
# night_sequence.lock is an flock file that stays on disk between runs — only a *held* lock means a run is going on;
# meeting_active.lock exists only while a meeting runs.
NIGHT_RUN_LOCK = HOME / "state" / "night_sequence.lock"
MEETING_LOCK = HOME / "state" / "meeting_active.lock"

# The files a release bundle replaces. Everything else in the install folder belongs to the user.
BUNDLE_FILES = ("docker-compose.yml", "docker-compose.gpu.yml", ".env.example", "setup.sh", "setup.ps1", "VERSION")
BUNDLE_DIRS = ("docker",)

MIN_FREE_BYTES = 5 * 1024**3  # safety margin for new image layers
HEALTH_TIMEOUT_S = 600  # first start after an update may load models
HEALTH_SETTLE_S = 20  # services must stay up this long after the API answers
CHECK_CACHE_S = 6 * 3600
HTTP_TIMEOUT_S = 30

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_TEMP_NAME_RE = re.compile(r"^[0-9a-f]{12}_")
# Phases during which a restart of this container means the install may be half-switched.
_MID_PHASES = {"swap", "pull", "verify_images", "start", "health", "rollback"}

log = logging.getLogger("ktsb.updater")
_job_lock = threading.Lock()
_state_lock = threading.Lock()
_check_cache: dict = {"at": 0.0, "result": None}


class UpdateError(Exception):
    """A step failed; the message is shown to the admin (English, the GUI maps the step to its own label)."""


# ── Small helpers ──────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_version(tag: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match((tag or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def audit(event: str, detail: str) -> None:
    """Same JSONL format as kaare_core.audit. Never raises."""
    entry = {"ts": _now(), "event": event, "user": "updater", "detail": detail, "ip": ""}
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("audit write failed: %s", e)


def read_state() -> dict:
    with _state_lock:
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"phase": "idle"}


def write_state(**changes) -> dict:
    """Merge changes into state.json atomically (survives a restart mid-update)."""
    with _state_lock:
        try:
            cur = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            cur = {"phase": "idle"}
        cur.update(changes)
        cur["updated"] = _now()
        UPD_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
        return cur


def step(phase: str, message: str) -> None:
    """Record progress: state.json (for the GUI), the updater log and the step list."""
    log.info("[%s] %s", phase, message)
    st = read_state()
    steps = (st.get("steps") or [])[-50:]
    steps.append({"ts": _now(), "phase": phase, "message": message})
    write_state(phase=phase, steps=steps)


def run(cmd: list[str], timeout: float = 900, env: dict | None = None) -> str:
    """Run a command in the install folder; raise UpdateError with the tail of its output on failure."""
    try:
        p = subprocess.run(
            cmd, cwd=HOME, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})}
        )
    except subprocess.TimeoutExpired as e:
        raise UpdateError(f"timed out after {int(timeout)} s: {' '.join(cmd[:4])}") from e
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-5:]
        raise UpdateError(f"{' '.join(cmd[:4])} failed (exit {p.returncode}): {' | '.join(tail)}")
    return p.stdout


def compose(*args: str, timeout: float = 900, env: dict | None = None) -> str:
    # cwd = HOME → compose reads HOME/.env, so COMPOSE_FILE / COMPOSE_PROFILES / overrides apply as on the host.
    # KTSB_HOME_HOST is the Windows path on Docker Desktop; from in here the engine-side path is the one that works.
    env = {"KTSB_HOME_HOST": str(HOME), **(env or {})}
    return run(["docker", "compose", "--project-directory", str(HOME), *args], timeout=timeout, env=env)


def http_get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ktsb-updater", "Accept": accept})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:  # fixed https hosts only
        return r.read()


def read_env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in (HOME / ".env").read_text(encoding="utf-8-sig").splitlines():
            key, sep, val = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                out[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    return out


def set_env_value(key: str, value: str) -> None:
    """Set KEY=value in .env, keeping every other line as the user wrote it."""
    path = HOME / ".env"
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def current_version() -> str:
    v = read_env_file().get("KTSB_VERSION", "")
    if parse_version(v):
        return v
    try:
        v = (HOME / "VERSION").read_text(encoding="utf-8").strip()
        return v if parse_version(v) else ""
    except FileNotFoundError:
        return ""


def service_token() -> str:
    try:
        for line in TOKEN_PATH.read_text(encoding="utf-8").splitlines():
            key, _, val = line.strip().partition("=")
            if key == "KAARE_SERVICE_TOKEN" and val:
                return val.strip()
    except Exception:
        pass
    return ""


# Why an install cannot be updated from the GUI. The code goes to the GUI (which words it in the user's language).
_PROBLEMS = {
    "no_home": "KTSB_HOME is not set — run setup.sh / setup.ps1 once in the install folder",
    "no_compose": "no docker-compose.yml at KTSB_HOME — the install folder is not mounted there",
    "no_docker_socket": "the Docker socket is not mounted",
}


def night_sequence_running() -> bool:
    """Same probe as kaare_night_runner.is_sequence_running: a shared lock fails while a run holds it."""
    if MEETING_LOCK.exists():
        return True
    try:
        with open(NIGHT_RUN_LOCK, encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def install_problem() -> str:
    """Problem code ('' when the install can be updated)."""
    if not os.environ.get("KTSB_HOME"):
        return "no_home"
    if not (HOME / "docker-compose.yml").exists():
        return "no_compose"
    if not Path("/var/run/docker.sock").exists():
        return "no_docker_socket"
    return ""


# ── Releases ───────────────────────────────────────────────────────────────────


def list_releases() -> list[dict]:
    """Stable releases that carry a complete install bundle, newest first."""
    data = json.loads(http_get(f"https://api.github.com/repos/{REPO}/releases?per_page=30"))
    out = []
    for rel in data:
        tag = rel.get("tag_name", "")
        if rel.get("draft") or rel.get("prerelease") or not parse_version(tag):
            continue
        assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
        names = (f"ktsb-docker-{tag}.tar.gz", f"ktsb-docker-{tag}.tar.gz.sigstore.json", "SHA256SUMS")
        if not all(n in assets for n in names):
            continue
        out.append(
            {
                "version": tag,
                "published": rel.get("published_at", ""),
                "notes": (rel.get("body") or "")[:20000],
                "url": rel.get("html_url", ""),
                "assets": {n: assets[n] for n in names},
            }
        )
    out.sort(key=lambda r: parse_version(r["version"]), reverse=True)
    return out


def check(force: bool = False) -> dict:
    if not force and _check_cache["result"] and time.time() - _check_cache["at"] < CHECK_CACHE_S:
        return _check_cache["result"]
    cur = current_version()
    try:
        releases = list_releases()
    except Exception as e:
        return {"ok": False, "current": cur, "error": f"could not reach GitHub: {e}"}
    newest = releases[0] if releases else None
    available = bool(newest and (not parse_version(cur) or parse_version(newest["version"]) > parse_version(cur)))
    result = {
        "ok": True,
        "current": cur,
        "latest": newest and {k: newest[k] for k in ("version", "published", "notes", "url")},
        "available": available,
        "checked": _now(),
    }
    _check_cache.update(at=time.time(), result=result)
    return result


# ── Verification ───────────────────────────────────────────────────────────────


def _cosign(*args: str, timeout: float = 180) -> str:
    return run(
        ["cosign", *args, "--certificate-oidc-issuer", OIDC_ISSUER, "--certificate-identity-regexp", SIGNER_IDENTITY],
        timeout=timeout,
    )


def verify_bundle(tarball: Path, sig_bundle: Path, sums: Path) -> None:
    _cosign("verify-blob", "--bundle", str(sig_bundle), str(tarball))
    want = ""
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == tarball.name:
            want = parts[0].lower()
    if not want:
        raise UpdateError(f"{tarball.name} is missing from SHA256SUMS")
    h = hashlib.sha256()
    with tarball.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if not hmac.compare_digest(h.hexdigest(), want):
        raise UpdateError("bundle checksum does not match SHA256SUMS")


def verify_image(ref: str) -> str:
    """Verify our workflow signed this image; return the signed digest."""
    out = _cosign("verify", ref)
    digests = {
        item.get("critical", {}).get("image", {}).get("docker-manifest-digest", "") for item in json.loads(out or "[]")
    }
    digests.discard("")
    if len(digests) != 1:
        raise UpdateError(f"{ref}: expected one signed digest, got {len(digests)}")
    return digests.pop()


def local_digests(ref: str) -> set[str]:
    out = run(["docker", "image", "inspect", ref, "--format", "{{json .RepoDigests}}"], timeout=60)
    return {d.split("@", 1)[1] for d in json.loads(out or "[]") if "@" in d}


# ── Update steps ───────────────────────────────────────────────────────────────


def preflight(version: str) -> dict:
    problem = install_problem()
    if problem:
        raise UpdateError(_PROBLEMS[problem])
    if night_sequence_running():
        raise UpdateError("the night sequence is running — try again when it has finished")
    if shutil.disk_usage(HOME).free < MIN_FREE_BYTES:
        raise UpdateError("less than 5 GB free disk space in the install folder")
    cur = current_version()
    target = parse_version(version)
    if not target:
        raise UpdateError(f"not a release version: {version!r}")
    if parse_version(cur) and target <= parse_version(cur):
        raise UpdateError(f"{version} is not newer than the running {cur}")
    rel = next((r for r in list_releases() if r["version"] == version), None)
    if not rel:
        raise UpdateError(f"{version} is not a published release with an install bundle")
    return rel


def download_and_stage(rel: dict) -> Path:
    version = rel["version"]
    shutil.rmtree(STAGE_DIR, ignore_errors=True)
    STAGE_DIR.mkdir(parents=True)
    files = {}
    for name, url in rel["assets"].items():
        if not url.startswith("https://github.com/"):
            raise UpdateError(f"unexpected download host for {name}")
        dest = STAGE_DIR / name
        dest.write_bytes(http_get(url, accept="application/octet-stream"))
        files[name] = dest
    step("verify_bundle", "checking the bundle signature and checksum")
    tarball = files[f"ktsb-docker-{version}.tar.gz"]
    verify_bundle(tarball, files[f"ktsb-docker-{version}.tar.gz.sigstore.json"], files["SHA256SUMS"])
    with tarfile.open(tarball) as tf:
        tf.extractall(STAGE_DIR / "x", filter="data")
    root = STAGE_DIR / "x" / f"ktsb-docker-{version}"
    if not (root / "docker-compose.yml").exists():
        raise UpdateError("bundle has no docker-compose.yml")
    if (root / "VERSION").read_text(encoding="utf-8").strip() != version:
        raise UpdateError("bundle VERSION does not match the release")
    return root


def _copy_bundle(src: Path, dst: Path) -> None:
    for name in BUNDLE_FILES:
        if (src / name).exists():
            tmp = dst / f".{name}.ktsb-new"
            shutil.copy2(src / name, tmp)
            os.replace(tmp, dst / name)
    for name in BUNDLE_DIRS:
        if (src / name).is_dir():
            shutil.rmtree(dst / name, ignore_errors=True)
            shutil.copytree(src / name, dst / name)


def save_previous() -> None:
    shutil.rmtree(PREV_DIR, ignore_errors=True)
    PREV_DIR.mkdir(parents=True)
    _copy_bundle(HOME, PREV_DIR)
    shutil.copy2(HOME / ".env", PREV_DIR / ".env")


def restore_previous() -> None:
    if not (PREV_DIR / "docker-compose.yml").exists():
        raise UpdateError("no previous version kept — cannot roll back")
    _copy_bundle(PREV_DIR, HOME)
    tmp = HOME / ".env.ktsb-restore"
    shutil.copy2(PREV_DIR / ".env", tmp)
    os.replace(tmp, HOME / ".env")


def own_images() -> list[str]:
    out = compose("config", "--images", timeout=120)
    return sorted({i.strip() for i in out.splitlines() if i.strip().startswith(REGISTRY + "/")})


def services_except_self() -> list[str]:
    out = compose("config", "--services", timeout=120)
    return [s for s in out.split() if s and s != SELF_SERVICE]


def start_services() -> None:
    compose("up", "-d", "--remove-orphans", *services_except_self(), timeout=1800)


def health(version: str) -> None:
    """The API answers with the expected version and no service is crash-looping."""
    token = service_token()
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    seen = ""
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"{API_URL}/api/update/alive", headers={"X-Kaare-Service-Token": token, "User-Agent": "ktsb-updater"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:  # compose-internal API
                seen = json.loads(r.read()).get("version", "")
            if seen == version or (not version and seen):
                break
        except Exception:
            pass
        time.sleep(5)
    else:
        raise UpdateError(f"KTSB did not come up with {version or 'the expected version'} (last seen: {seen or '—'})")
    time.sleep(HEALTH_SETTLE_S)
    bad = []
    for line in compose("ps", "--all", "--format", "json", timeout=60).splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        if c.get("Service") == SELF_SERVICE:
            continue
        state, code = c.get("State", ""), int(c.get("ExitCode") or 0)
        if state == "restarting" or (state == "exited" and code != 0):
            bad.append(f"{c.get('Service')} ({state}, exit {code})")
    if bad:
        raise UpdateError("services not healthy: " + ", ".join(bad))


def repair_temp_named() -> None:
    """A compose recreate cut off mid-way leaves `<hex>_<name>` containers (P57 V3); recreate those services."""
    try:
        out = run(
            ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format", "{{json .}}"],
            timeout=60,
        )
    except UpdateError as e:
        log.warning("could not list containers: %s", e)
        return
    services = set()
    for line in out.splitlines():
        with contextlib.suppress(ValueError):
            c = json.loads(line)
            if _TEMP_NAME_RE.match(c.get("Names", "")):
                services.update(re.findall(r"com\.docker\.compose\.service=([^,]+)", c.get("Labels", "")))
    services.discard(SELF_SERVICE)
    if services:
        log.warning("recreating half-replaced services: %s", sorted(services))
        with contextlib.suppress(UpdateError):
            compose("up", "-d", "--force-recreate", *sorted(services), timeout=900)


def self_update_if_needed(version: str) -> None:
    """Recreate this container through a short-lived helper — never from inside (P57 V3/V4)."""
    ref = f"{REGISTRY}/{SELF_SERVICE}:{version}"
    try:
        new_id = run(["docker", "image", "inspect", ref, "--format", "{{.Id}}"], timeout=60).strip()
        me = socket.gethostname()
        cur_id = run(["docker", "inspect", me, "--format", "{{.Image}}"], timeout=60).strip()
    except UpdateError as e:
        log.warning("self-update skipped: %s", e)
        return
    if not new_id or new_id == cur_id:
        return
    write_state(phase="self_update")
    step("self_update", "handing over to a helper that restarts the updater on the new version")
    helper = f"{PROJECT}-updater-handoff"
    with contextlib.suppress(UpdateError):
        run(["docker", "rm", "-f", helper], timeout=60)
    script = (
        f"sleep 3; docker compose --project-directory '{HOME}' up -d --no-deps --force-recreate {SELF_SERVICE}"
    )
    run(
        ["docker", "run", "-d", "--rm", "--name", helper,
         "-v", "/var/run/docker.sock:/var/run/docker.sock",
         "-v", f"{HOME}:{HOME}", "-w", str(HOME), "-e", f"KTSB_HOME_HOST={HOME}",
         "--entrypoint", "sh", ref, "-c", script],
        timeout=120,
    )  # fmt: skip


def do_update(version: str) -> None:
    started = _now()
    from_version = current_version()
    write_state(
        phase="preflight", target=version, from_version=from_version, started=started, error="", result="", steps=[]
    )
    audit("update_started", f"from={from_version or '-'} to={version}")
    switched = False
    try:
        step("preflight", f"checking that {version} can be installed")
        rel = preflight(version)
        step("download", "downloading the install bundle")
        staged = download_and_stage(rel)
        step("swap", "keeping the current files and putting the new ones in place")
        save_previous()
        switched = True
        _copy_bundle(staged, HOME)
        set_env_value("KTSB_VERSION", version)
        step("pull", "downloading the new images (KTSB keeps running meanwhile)")
        compose("pull", "--quiet", *services_except_self(), timeout=3600)
        compose("pull", "--quiet", SELF_SERVICE, timeout=1800)
        step("verify_images", "checking that every KTSB image was signed by the KTSB release workflow")
        for ref in own_images():
            signed = verify_image(ref)
            if signed not in local_digests(ref):
                raise UpdateError(f"{ref}: the downloaded image is not the signed one")
        step("start", "restarting KTSB on the new version")
        start_services()
        step("health", "waiting for KTSB to answer")
        health(version)
    except Exception as e:
        msg = str(e) if isinstance(e, UpdateError) else f"unexpected error: {e!r}"
        log.error("update to %s failed: %s", version, msg)
        audit("update_failed", f"to={version} error={msg}")
        if switched:
            _rollback(reason=msg, target=from_version)
        else:
            write_state(phase="failed", error=msg, result="failed", finished=_now())
        return
    shutil.rmtree(STAGE_DIR, ignore_errors=True)
    write_state(phase="done", result="updated", finished=_now())
    audit("update_done", f"from={from_version or '-'} to={version}")
    _check_cache.update(at=0.0, result=None)
    self_update_if_needed(version)


def _rollback(reason: str, target: str) -> None:
    step("rollback", f"putting the previous version back ({reason})")
    try:
        restore_previous()
        start_services()
        health(target)
    except Exception as e:
        msg = f"{reason} — rollback also failed: {e}"
        log.error(msg)
        audit("rollback_failed", msg)
        write_state(phase="failed", error=msg, result="rollback_failed", finished=_now())
        return
    audit("rollback_done", f"back on {target or 'previous'}; cause: {reason}")
    write_state(phase="rolled_back", error=reason, result="rolled_back", finished=_now())


def do_manual_rollback() -> None:
    st = read_state()
    target = st.get("from_version", "")
    write_state(phase="rollback", started=_now(), error="", result="", target=target, steps=[])
    audit("rollback_started", f"to={target or 'previous'} (admin)")
    _rollback(reason="requested by an admin", target=target)


def resume_after_restart() -> None:
    """Called at start: finish what a restart interrupted."""
    repair_temp_named()
    st = read_state()
    phase = st.get("phase", "idle")
    if phase == "self_update":
        write_state(phase="done", result="updated", finished=st.get("finished") or _now())
        audit("update_done", "updater restarted on the new version")
    elif phase in _MID_PHASES:
        log.warning("restarted during '%s' — rolling back", phase)
        with _job_lock:
            _rollback(reason=f"the updater was restarted during '{phase}'", target=st.get("from_version", ""))
    elif phase in ("preflight", "download", "verify_bundle"):
        write_state(phase="failed", error="interrupted before anything was changed", result="failed")


# ── Socket server ──────────────────────────────────────────────────────────────


def _start_job(target, *args) -> bool:
    if not _job_lock.acquire(blocking=False):
        return False

    def runner():
        try:
            target(*args)
        finally:
            _job_lock.release()

    threading.Thread(target=runner, daemon=True, name="ktsb-update-job").start()
    return True


def handle(req: dict) -> dict:
    expected = service_token()
    if not expected or not hmac.compare_digest(str(req.get("token", "")).encode(), expected.encode()):
        return {"ok": False, "error": "unauthorized"}
    cmd = req.get("cmd")
    if cmd == "status":
        return {
            "ok": True,
            "busy": _job_lock.locked(),
            "current": current_version(),
            "problem": install_problem(),
            "can_rollback": (PREV_DIR / "docker-compose.yml").exists(),
            "state": read_state(),
        }
    if cmd == "check":
        return check(force=bool(req.get("force")))
    if cmd == "update":
        version = str(req.get("version", ""))
        if not parse_version(version):
            return {"ok": False, "error": "invalid version"}
        if not _start_job(do_update, version):
            return {"ok": False, "error": "busy"}
        return {"ok": True, "started": version}
    if cmd == "rollback":
        if not (PREV_DIR / "docker-compose.yml").exists():
            return {"ok": False, "error": "nothing to roll back to"}
        if not _start_job(do_manual_rollback):
            return {"ok": False, "error": "busy"}
        return {"ok": True, "started": "rollback"}
    return {"ok": False, "error": "unknown command"}


async def _client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        req = json.loads(line or b"{}")
        resp = await asyncio.to_thread(handle, req if isinstance(req, dict) else {})
    except Exception as e:
        resp = {"ok": False, "error": f"bad request: {e}"}
    with contextlib.suppress(Exception):
        writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
        await writer.drain()
        writer.close()


async def main() -> None:
    SOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        SOCK_PATH.unlink()
    server = await asyncio.start_unix_server(_client, path=str(SOCK_PATH))
    os.chmod(SOCK_PATH, 0o600)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)  # exit fast: compose waits for us on recreate (P57 V4)
    problem = install_problem()
    if problem:
        log.warning("updates disabled: %s", _PROBLEMS[problem])
    else:
        threading.Thread(target=resume_after_restart, daemon=True, name="ktsb-update-resume").start()
    log.info("listening on %s (repo %s, current %s)", SOCK_PATH, REPO, current_version() or "unknown")
    async with server:
        await stop.wait()
    with contextlib.suppress(FileNotFoundError):
        SOCK_PATH.unlink()


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    with contextlib.suppress(Exception):
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(LOG_PATH, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


if __name__ == "__main__":
    _setup_logging()
    asyncio.run(main())
