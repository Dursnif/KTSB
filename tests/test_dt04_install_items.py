"""DT04 R0.8 — small install/security items a Docker user meets.

- The GUI loads no fonts from Google: the two fonts are files in the frontend (`public/fonts`), declared in CSS.
- CORS: the shipped default is «same-host» — a page may call the API when it was served from the same machine name or
  address the browser used for the API (what KTSB's own frontend does: http://<host>:5173 → http://<host>:8000). Any
  other website is refused. Origins the user lists still work; «*» still means everyone (rule 27: what the user
  configured is open). An install that still has the old shipped «*» is moved to «same-host» once.
- setup.sh / setup.ps1 make a SearXNG secret when it is blank and never replace one that is set.
- nginx: index.html is never cached (an update must show the new GUI); hashed assets stay cached.
- docker-compose: the optional VPN variables have empty defaults, so compose prints no «variable is not set» warning.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kaare_core import config_migrations, cors

ROOT = Path("/kaare")
API = "http://192.0.2.10:8000"  # RFC 5737 documentation address


# ── fonts ───────────────────────────────────────────────────────────────────────

def test_the_gui_loads_nothing_from_google():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert "googleapis" not in html and "gstatic" not in html
    css = (ROOT / "frontend/src/fonts.css").read_text(encoding="utf-8")
    for family in ("Cinzel", "JetBrains Mono"):
        assert f'font-family: "{family}"' in css
    files = re.findall(r'url\("\./(assets/fonts/[^"]+)"\)', css)  # relative → Vite bundles them with hashed names
    assert len(files) == 4
    for f in files:
        assert (ROOT / "frontend/src" / f).is_file(), f
    assert "http" not in re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # no remote source
    assert list((ROOT / "frontend/src/assets/fonts").glob("LICENSE-*-OFL.txt"))  # OFL: the licence travels with the files
    assert "import './fonts.css'" in (ROOT / "frontend/src/main.tsx").read_text(encoding="utf-8")


# ── CORS ────────────────────────────────────────────────────────────────────────

def _client(origins):
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    cors.add_cors(app, origins)
    return TestClient(app, base_url=API)


def _allowed(client, origin, host="192.0.2.10:8000"):
    r = client.get("/ping", headers={"Origin": origin, "Host": host})
    return r.headers.get("access-control-allow-origin")


def test_same_host_lets_ktsbs_own_frontend_in_and_other_sites_out():
    c = _client(["same-host"])
    assert _allowed(c, "http://192.0.2.10:5173") == "http://192.0.2.10:5173"
    assert _allowed(c, "https://evil.example") is None
    assert _allowed(c, "http://192.0.2.99:5173") is None  # another machine on the LAN
    assert _allowed(c, "http://ktsb.example:5173", host="ktsb.example:8000") == "http://ktsb.example:5173"
    assert _allowed(c, "http://[2001:db8::1]:5173", host="[2001:db8::1]:8000") == "http://[2001:db8::1]:5173"


def test_same_host_answers_the_preflight_too():
    c = _client(["same-host"])
    r = c.options("/ping", headers={"Origin": "http://192.0.2.10:5173", "Host": "192.0.2.10:8000",
                                    "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://192.0.2.10:5173"
    bad = c.options("/ping", headers={"Origin": "https://evil.example", "Host": "192.0.2.10:8000",
                                      "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


def test_origins_the_user_listed_still_work():  # rule 27
    c = _client(["same-host", "https://dash.example"])
    assert _allowed(c, "https://dash.example") == "https://dash.example"
    assert _allowed(c, "http://192.0.2.10:5173") == "http://192.0.2.10:5173"
    assert _allowed(_client(["https://dash.example"]), "http://192.0.2.10:5173") is None  # a list without same-host
    assert _allowed(_client(["*"]), "https://anything.example") == "*"  # the user's explicit choice


def test_missing_or_broken_setting_falls_back_to_same_host_not_everyone(tmp_path):
    assert cors.read_origins(tmp_path / "missing.yaml") == ["same-host"]
    bad = tmp_path / "settings.yaml"
    bad.write_text("{{{ not yaml")
    assert cors.read_origins(bad) == ["same-host"]
    bad.write_text("cors_origins: nonsense\n")
    assert cors.read_origins(bad) == ["same-host"]
    bad.write_text("cors_origins: ['https://dash.example']\n")
    assert cors.read_origins(bad) == ["https://dash.example"]


def test_template_ships_same_host():
    # DT04-S05r: was `tpl["cors_origins"] == ["same-host"]`. The default now lives in code (an older version misread the
    # value after a rollback); what a fresh install gets is unchanged: same-host.
    tpl_path = ROOT / "configs_default/settings.yaml"
    tpl = yaml.safe_load(tpl_path.read_text(encoding="utf-8"))
    assert "cors_origins" not in tpl
    assert cors.read_origins(tpl_path) == ["same-host"]


def test_an_install_with_the_old_shipped_star_is_moved_once():
    data = {"cors_origins": ["*"]}
    assert config_migrations.cors_same_host(data, {})
    assert data["cors_origins"] == ["same-host"]
    own = {"cors_origins": ["https://dash.example"]}
    assert config_migrations.cors_same_host(own, {}) == [] and own["cors_origins"] == ["https://dash.example"]
    mixed = {"cors_origins": ["*", "https://dash.example"]}
    assert config_migrations.cors_same_host(mixed, {}) == [] and mixed["cors_origins"][0] == "*"


# ── setup scripts ───────────────────────────────────────────────────────────────

def _run_helper(env_file: Path) -> None:
    script = (
        f'ENV_FILE="{env_file}"; '
        f'source <(sed -n "/^# >>> env-helpers/,/^# <<< env-helpers/p" {ROOT}/setup.sh); ensure_searxng_secret'
    )
    subprocess.run(["bash", "-c", script], check=True, capture_output=True, timeout=20)


def _value(env_file: Path, key: str) -> str:
    return next(line.split("=", 1)[1] for line in env_file.read_text().splitlines() if line.startswith(f"{key}="))


def test_setup_sh_makes_a_searxng_secret_when_blank(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KAARE_DOMAIN=localhost\nSEARXNG_SECRET=\nOTHER=1\n")
    _run_helper(env)
    secret = _value(env, "SEARXNG_SECRET")
    assert re.fullmatch(r"[0-9a-f]{64}", secret)
    assert "KAARE_DOMAIN=localhost" in env.read_text() and "OTHER=1" in env.read_text()
    _run_helper(env)
    assert _value(env, "SEARXNG_SECRET") == secret  # never replaced


def test_setup_sh_adds_the_key_when_it_is_missing_and_keeps_a_set_one(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KAARE_DOMAIN=localhost\n")
    _run_helper(env)
    assert re.fullmatch(r"[0-9a-f]{64}", _value(env, "SEARXNG_SECRET"))
    env.write_text("SEARXNG_SECRET=mine\n")
    _run_helper(env)
    assert _value(env, "SEARXNG_SECRET") == "mine"


def test_setup_ps1_makes_the_secret_the_same_way():
    """No PowerShell on the build machine — the text is checked here, the run on Windows (DT04 R2)."""
    ps1 = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    assert "SEARXNG_SECRET" in ps1 and "RandomNumberGenerator" in ps1
    assert re.search(r'-match\s+"\^SEARXNG_SECRET=\.\+"', ps1)  # only when blank


# ── nginx + compose ─────────────────────────────────────────────────────────────

def test_nginx_never_caches_index_html():
    conf = (ROOT / "docker/nginx.conf").read_text(encoding="utf-8")
    root_block = re.search(r"location / \{(.*?)\}", conf, re.S).group(1)
    assert "no-cache" in root_block
    assets = re.search(r"location ~\* [^{]*\{(.*?)\}", conf, re.S).group(1)
    assert "immutable" in assets


@pytest.mark.parametrize("var", ["WG_HOST", "WG_PASSWORD_HASH", "SEARXNG_SECRET"])
def test_optional_compose_variables_have_an_empty_default(var):
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"${{{var}}}" not in text and f"${{{var}:-}}" in text


# ── R0.8b (DT04-I5): Windows picks the heavy profile only for a GPU Docker can use ──

def test_setup_ps1_picks_full_only_for_nvidia():
    """On Windows, Docker can pass through an NVIDIA card only; the script itself says AMD and Intel run in CPU mode.
    Text check (no PowerShell on the build machine) — the run on a Radeon iGPU is DT04 R2."""
    ps1 = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    block = ps1[ps1.index("# Build COMPOSE_PROFILES"):ps1.index("# Update COMPOSE_PROFILES")]
    assert '$profiles = "medium"' in block and '$profiles = "full"' in block
    condition = re.search(r"if \((.*?)\) \{\s*\$profiles = \"full\"", block, re.S).group(1)
    assert '"nvidia"' in condition and '"amd"' not in condition and '"intel"' not in condition
